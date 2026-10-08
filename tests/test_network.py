import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import run

from citation_lens.graph import payload
from citation_lens.network import Web, public_url
from citation_lens.storage import Store


def test_cache_survives_restart_and_expires(tmp_path):
    store = Store(tmp_path / "store.sqlite")
    store.put("expired", b"x", ttl=-1)
    store.save("graph", {"nodes": [1]})
    store.close()
    reopened = Store(tmp_path / "store.sqlite")
    assert reopened.get("expired") is None and reopened.load("graph") == {"nodes": [1]}
    reopened.close()
    measured = payload({"unicode": "科学"})
    size = len(json.dumps(measured, ensure_ascii=False, separators=(",", ":")).encode())
    assert measured["payload_bytes"] == size


def test_single_flight_cache_and_byte_limit(store):
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, content=b'{"ok":true}')

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        url = "https://api.openalex.org/works?search=test"
        first, second = await asyncio.gather(web.json(url), web.json(url))
        assert first == second == {"ok": True} and len(calls) == 1
        assert await web.json(url) == first and web.hits == 1
        with pytest.raises(ValueError):
            await web.fetch(url + "2", max_bytes=2)
        await web.close()

    run(exercise())


def test_retry_redirect_and_credential_isolation(store, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "fake-secret")
    seen = []

    async def respond(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if len(seen) == 2:
            return httpx.Response(302, headers={"location": "https://public.example/paper.pdf"})
        return httpx.Response(200, content=b"paper")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            assert await web.fetch("https://api.openalex.org/works/W1") == b"paper"
        assert seen[0].headers["authorization"] == "Bearer fake-secret"
        assert "authorization" not in seen[-1].headers
        await web.close()

    run(exercise())


def test_exhausted_daily_budget_fails_fast(store):
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx.Response(429, headers={"x-ratelimit-remaining": "0"})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await web.fetch("https://api.openalex.org/works?search=x")
        assert len(seen) == 1
        await web.close()

    run(exercise())


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://user:pass@example.com", "https://example.com:99"]
)
def test_url_policy(url):
    with pytest.raises(ValueError):
        run(public_url(url))


def test_private_dns_is_blocked():
    async def exercise():
        loop = asyncio.get_running_loop()
        answer = AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
        with patch.object(loop, "getaddrinfo", new=answer), pytest.raises(ValueError):
            await public_url("https://private.example/a")

    run(exercise())


def test_a_host_throttled_through_every_retry_is_skipped_for_a_while(store):
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx.Response(429, headers={"retry-after": "0"})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.attempts = 2
        with pytest.raises(RuntimeError, match="HTTP 429"):
            await web.fetch("https://api.openalex.org/works?search=a")
        with pytest.raises(RuntimeError, match="skipped"):
            await web.fetch("https://api.openalex.org/works?search=b")
        assert len(seen) == 2  # the second call made no request
        await web.close()

    run(exercise())


def test_dns_failure_is_a_reported_error():
    async def exercise():
        loop = asyncio.get_running_loop()
        failing = AsyncMock(side_effect=OSError("nodename nor servname provided"))
        with patch.object(loop, "getaddrinfo", new=failing), pytest.raises(RuntimeError):
            await public_url("https://arxiv.org/html/2205.14135")

    run(exercise())
