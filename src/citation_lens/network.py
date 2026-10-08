"""Pooled, paced, cached HTTP. No provider credentials in caller-controlled URLs."""

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import time
from contextlib import nullcontext
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .storage import Store


def checked_url(url: str):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise ValueError("Only public HTTPS URLs without credentials or custom ports are allowed")
    return parts


async def public_url(url: str):
    parts = checked_url(url)
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, 443, type=socket.SOCK_STREAM
        )
    except OSError:
        raise RuntimeError(f"{parts.hostname}: DNS lookup failed") from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Private, loopback, and reserved network addresses are blocked")
    return addresses[0][4][0]


class Web:
    def __init__(self, store: Store, client: httpx.AsyncClient | None = None):
        self.store = store
        self.client = client or httpx.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False
        )
        self.slots = asyncio.Semaphore(6)
        self.crossref_slot = asyncio.Semaphore(1)
        self.locks: dict[str, asyncio.Lock] = {}
        self.next_at: dict[str, float] = {}
        self.pending: dict[str, asyncio.Task] = {}
        self.waiters: dict[str, int] = {}
        self.requests = 0
        self.hits = 0
        # Interactive defaults: give up quickly. Batch jobs such as grading can be patient.
        self.attempts, self.max_wait = 5, 8.0
        # A host that stays throttled through every retry is skipped for a minute, so one
        # saturated provider fails fast instead of costing every later call its retries.
        self.cooldown = 60.0
        self.down_until: dict[str, float] = {}

    async def fetch(self, url: str, *, body=None, ttl=86400, max_bytes=20_000_000) -> bytes:
        checked_url(url)
        identity = json.dumps([url, body], sort_keys=True).encode()
        key = "http:" + hashlib.sha256(identity).hexdigest()
        cached = self.store.get(key)
        if cached is not None:
            self.hits += 1
            if len(cached) > max_bytes:
                raise ValueError("Cached response exceeds this request's byte limit")
            return cached
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(self._download(url, body, max_bytes))
        task = self.pending[key]
        self.waiters[key] = self.waiters.get(key, 0) + 1
        try:
            data = await asyncio.shield(task)
            if len(data) > max_bytes:
                raise ValueError("Response exceeds this request's byte limit")
            self.store.put(key, data, ttl)
            return data
        finally:
            remaining = self.waiters[key] - 1
            if remaining:
                self.waiters[key] = remaining
            else:
                self.waiters.pop(key)
                self.pending.pop(key, None)
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _download(self, url, body, max_bytes):
        original_host = urlsplit(url).hostname
        headers = {"User-Agent": "CitationLens/0.1 (academic literature research)"}
        secrets = {
            "api.openalex.org": ("OPENALEX_API_KEY", "Authorization", "Bearer "),
            "api.semanticscholar.org": ("SEMANTIC_SCHOLAR_API_KEY", "x-api-key", ""),
            "r.jina.ai": ("JINA_API_KEY", "Authorization", "Bearer "),
        }
        if original_host in secrets:
            env, header, prefix = secrets[original_host]
            if value := os.getenv(env):
                headers[header] = prefix + value
        async with asyncio.timeout(60 * self.attempts / 3):
            for _redirect in range(6):
                host = urlsplit(url).hostname
                if (wait := self.down_until.get(host, 0) - time.monotonic()) > 0:
                    raise RuntimeError(f"{host}: throttled; skipped for {wait:.0f}s")
                for attempt in range(self.attempts):
                    # S2 keys commonly begin at 1 request/s. arXiv asks for 3s pacing.
                    interval = {
                        "api.crossref.org": 1.05,
                        "api.semanticscholar.org": 1.05,
                        "export.arxiv.org": 3.05,
                    }.get(host, 0.15)
                    is_crossref = host == "api.crossref.org"
                    try:
                        attempt_slot = self.crossref_slot if is_crossref else nullcontext()
                        async with attempt_slot:
                            async with self.locks.setdefault(host, asyncio.Lock()):
                                await asyncio.sleep(
                                    max(0, self.next_at.get(host, 0) - time.monotonic())
                                )
                                if not is_crossref:
                                    self.next_at[host] = time.monotonic() + interval
                            request_url, request_headers, extensions = url, dict(headers), {}
                            if host not in (
                                "api.openalex.org",
                                "api.semanticscholar.org",
                                "r.jina.ai",
                            ):
                                address = await public_url(url)
                                parts = urlsplit(url)
                                authority = f"[{address}]" if ":" in address else address
                                request_url = urlunsplit(parts._replace(netloc=authority))
                                request_headers["Host"] = host
                                extensions["sni_hostname"] = host
                            # Hold a connection slot only while a request is in flight, never
                            # while pacing or backing off, so one provider cannot stall the rest.
                            async with self.slots:
                                if (wait := self.down_until.get(host, 0) - time.monotonic()) > 0:
                                    raise RuntimeError(
                                        f"{host}: throttled; skipped for {wait:.0f}s"
                                    )
                                self.requests += 1
                                request_stream = self.client.stream(
                                    "POST" if body else "GET",
                                    request_url,
                                    json=body,
                                    headers=request_headers,
                                    extensions=extensions,
                                )
                                if is_crossref:
                                    self.next_at[host] = time.monotonic() + interval
                                async with request_stream as response:
                                    if is_crossref:
                                        self.next_at[host] = time.monotonic() + interval
                                    status = response.status_code
                                    response_headers = response.headers
                                    if status in (429, 500, 502, 503, 504) or response.is_redirect:
                                        pass
                                    elif status >= 400:
                                        raise RuntimeError(f"{host}: HTTP {status}")
                                    else:
                                        chunks, size = [], 0
                                        async for chunk in response.aiter_bytes():
                                            size += len(chunk)
                                            if size > max_bytes:
                                                raise ValueError(
                                                    "Remote response exceeds byte limit"
                                                )
                                            chunks.append(chunk)
                                        return b"".join(chunks)
                    except httpx.TransportError:
                        if is_crossref:
                            self.next_at[host] = time.monotonic() + interval
                        if attempt == self.attempts - 1:
                            raise RuntimeError(f"{host}: network request failed") from None
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    if status in (301, 302, 303, 307, 308):
                        target = urljoin(url, response_headers.get("location", ""))
                        await public_url(target)
                        if urlsplit(target).hostname != original_host:
                            headers = {"User-Agent": headers["User-Agent"]}
                        url, body = target, None
                        break
                    if host == "api.openalex.org" and (
                        response_headers.get("x-ratelimit-remaining") == "0"
                    ):
                        raise RuntimeError(f"{host}: daily budget exhausted; set its API key")
                    retry = response_headers.get("retry-after", "")
                    # Keyless Semantic Scholar shares one pool; it needs longer waits.
                    base = 2.0 if host == "api.semanticscholar.org" else 1.0
                    delay = float(retry) if retry.isdigit() else base * 2**attempt
                    if attempt == self.attempts - 1 or delay > self.max_wait:
                        if status in (429, 503):
                            self.down_until[host] = time.monotonic() + self.cooldown
                        raise RuntimeError(f"{host}: HTTP {status}; retry later")
                    await asyncio.sleep(delay)
            raise RuntimeError("Too many redirects")

    async def json(self, url, *, body=None):
        return json.loads(await self.fetch(url, body=body, max_bytes=8_000_000))

    async def close(self):
        for task in self.pending.values():
            task.cancel()
        await asyncio.gather(*self.pending.values(), return_exceptions=True)
        self.pending.clear()
        await self.client.aclose()
