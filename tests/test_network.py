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


def test_cancelled_sole_fetch_stops_retrying(store):
    first_request_started = asyncio.Event()
    seen = []

    async def respond(request):
        seen.append(request)
        first_request_started.set()
        return httpx.Response(429, headers={"retry-after": "0"})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.attempts = 3
        fetch = asyncio.create_task(web.fetch("https://api.openalex.org/works?search=cancelled"))
        await first_request_started.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(fetch, 0.02)
        assert len(seen) == 1
        await asyncio.sleep(0.18)
        assert len(seen) == 1
        assert not web.pending
        await web.close()

    run(exercise())


def test_cancelling_one_coalesced_fetch_keeps_the_shared_download(store):
    requests_started = asyncio.Event()
    release_response = asyncio.Event()
    seen = []

    async def respond(request):
        seen.append(request)
        requests_started.set()
        await release_response.wait()
        return httpx.Response(200, content=b"shared")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        url = "https://api.openalex.org/works?search=shared"
        cancelled = asyncio.create_task(web.fetch(url))
        await requests_started.wait()
        remaining = asyncio.create_task(web.fetch(url))
        await asyncio.sleep(0)

        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        release_response.set()

        assert await remaining == b"shared"
        assert len(seen) == 1
        await web.close()

    run(exercise())


def test_cancelling_all_coalesced_fetches_stops_retries(store):
    first_response = asyncio.Event()
    seen = []

    async def respond(request):
        seen.append(request)
        first_response.set()
        return httpx.Response(429, headers={"retry-after": "0"})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.attempts = 3
        url = "https://api.openalex.org/works?search=coalesced-cancellation"
        first = asyncio.create_task(web.fetch(url))
        second = asyncio.create_task(web.fetch(url))
        await first_response.wait()

        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await asyncio.sleep(0.18)

        assert len(seen) == 1
        assert not web.pending
        await web.close()

    run(exercise())


def test_fresh_fetch_works_after_the_previous_download_is_cancelled(store):
    first_request_started = asyncio.Event()
    never_respond = asyncio.Event()
    seen = []

    async def respond(request):
        seen.append(request)
        if len(seen) == 1:
            first_request_started.set()
            await never_respond.wait()
        return httpx.Response(200, content=b"fresh")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        url = "https://api.openalex.org/works?search=fresh"
        first = asyncio.create_task(web.fetch(url))
        await first_request_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        assert await asyncio.wait_for(web.fetch(url), 0.3) == b"fresh"
        assert len(seen) == 2
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


def test_queued_retry_respects_cooldown_set_by_another_request(store):
    seen = []

    async def respond(request):
        seen.append(request.url.params["search"])
        return httpx.Response(429, headers={"retry-after": "0"})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.attempts = 2
        first = asyncio.create_task(
            web.fetch("https://api.openalex.org/works?search=establish-cooldown")
        )
        queued = asyncio.create_task(
            web.fetch("https://api.openalex.org/works?search=queued-retry")
        )

        with pytest.raises(RuntimeError, match="HTTP 429"):
            await first
        with pytest.raises(RuntimeError, match="skipped"):
            await queued
        assert seen == ["establish-cooldown", "queued-retry", "establish-cooldown"]
        await web.close()

    run(exercise())


def test_dns_failure_is_a_reported_error():
    async def exercise():
        loop = asyncio.get_running_loop()
        failing = AsyncMock(side_effect=OSError("nodename nor servname provided"))
        with patch.object(loop, "getaddrinfo", new=failing), pytest.raises(RuntimeError):
            await public_url("https://arxiv.org/html/2205.14135")

    run(exercise())
