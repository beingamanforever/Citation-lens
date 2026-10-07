"""Pooled, paced, cached HTTP. No provider credentials in caller-controlled URLs."""

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import time
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
    addresses = await asyncio.get_running_loop().getaddrinfo(
        parts.hostname, 443, type=socket.SOCK_STREAM
    )
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
        self.locks: dict[str, asyncio.Lock] = {}
        self.next_at: dict[str, float] = {}
        self.pending: dict[str, asyncio.Task] = {}
        self.requests = 0
        self.hits = 0

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
        try:
            data = await asyncio.shield(task)
            if len(data) > max_bytes:
                raise ValueError("Response exceeds this request's byte limit")
            self.store.put(key, data, ttl)
            return data
        finally:
            if task.done():
                self.pending.pop(key, None)

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
        async with asyncio.timeout(45), self.slots:
            for _redirect in range(6):
                host = urlsplit(url).hostname
                for attempt in range(3):
                    # S2 keys commonly begin at 1 request/s. arXiv asks for 3s pacing.
                    interval = (
                        1.05
                        if host == "api.semanticscholar.org"
                        else 3.05
                        if host in ("arxiv.org", "export.arxiv.org")
                        else 0.15
                    )
                    async with self.locks.setdefault(host, asyncio.Lock()):
                        await asyncio.sleep(max(0, self.next_at.get(host, 0) - time.monotonic()))
                        self.next_at[host] = time.monotonic() + interval
                    self.requests += 1
                    try:
                        request_url, request_headers, extensions = url, dict(headers), {}
                        if host not in ("api.openalex.org", "api.semanticscholar.org", "r.jina.ai"):
                            address = await public_url(url)
                            parts = urlsplit(url)
                            authority = f"[{address}]" if ":" in address else address
                            request_url = urlunsplit(parts._replace(netloc=authority))
                            request_headers["Host"] = host
                            extensions["sni_hostname"] = host
                        async with self.client.stream(
                            "POST" if body else "GET",
                            request_url,
                            json=body,
                            headers=request_headers,
                            extensions=extensions,
                        ) as response:
                            if response.status_code in (429, 500, 502, 503, 504):
                                if attempt == 2:
                                    raise RuntimeError(
                                        f"{host}: HTTP {response.status_code}; retry later"
                                    )
                                retry = response.headers.get("retry-after", "")
                                delay = float(retry) if retry.isdigit() else 0.5 * 2**attempt
                                if delay > 3:
                                    raise RuntimeError(f"{host}: retry after {retry}s; retry later")
                                await asyncio.sleep(delay)
                                continue
                            if response.is_redirect:
                                target = urljoin(url, response.headers.get("location", ""))
                                await public_url(target)
                                if urlsplit(target).hostname != original_host:
                                    headers = {"User-Agent": headers["User-Agent"]}
                                url, body = target, None
                                break
                            if response.status_code >= 400:
                                raise RuntimeError(f"{host}: HTTP {response.status_code}")
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > max_bytes:
                                    raise ValueError("Remote response exceeds byte limit")
                                chunks.append(chunk)
                            return b"".join(chunks)
                    except httpx.TransportError:
                        if attempt == 2:
                            raise RuntimeError(f"{host}: network request failed") from None
                        await asyncio.sleep(0.5 * 2**attempt)
            raise RuntimeError("Too many redirects")

    async def json(self, url, *, body=None):
        return json.loads(await self.fetch(url, body=body, max_bytes=8_000_000))

    async def close(self):
        for task in self.pending.values():
            task.cancel()
        await asyncio.gather(*self.pending.values(), return_exceptions=True)
        self.pending.clear()
        await self.client.aclose()
