import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from conftest import run

from citation_lens import network
from citation_lens.graph import payload
from citation_lens.network import Web, public_url
from citation_lens.storage import Store


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self, started, release, finished):
        self.started = started
        self.release = release
        self.finished = finished

    async def __aiter__(self):
        self.started.set()
        try:
            await self.release.wait()
            yield b"ok"
        finally:
            self.finished.set()


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


def test_crossref_serializes_and_spaces_distinct_requests(store):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    first_finished = asyncio.Event()
    starts = []

    async def respond(request):
        starts.append(time.monotonic())
        if len(starts) == 1:
            return httpx.Response(
                200, stream=WaitingStream(first_started, release_first, first_finished)
            )
        assert first_finished.is_set()
        return httpx.Response(200, content=b"second")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            first = asyncio.create_task(web.fetch("https://api.crossref.org/works/first"))
            await first_started.wait()
            second = asyncio.create_task(web.fetch("https://api.crossref.org/works/second"))
            await asyncio.sleep(0.2)
            assert len(starts) == 1
            release_first.set()
            assert await asyncio.gather(first, second) == [b"ok", b"second"]
        assert starts[1] - starts[0] >= 1.05
        await web.close()

    run(exercise())


@pytest.mark.parametrize("host,interval", [("api.crossref.org", 1.05), ("export.arxiv.org", 3.05)])
def test_provider_pacing_rechecks_clock_after_early_wakeup(store, monkeypatch, host, interval):
    current_time = 0.0
    starts = []
    sleeps = []

    async def early_sleep(delay):
        nonlocal current_time
        if delay <= 0:
            return
        sleeps.append(delay)
        current_time += delay / 2 if len(sleeps) == 1 else delay

    async def respond(request):
        starts.append(current_time)
        return httpx.Response(200, content=b"ok")

    async def exercise():
        monkeypatch.setattr(network, "time", SimpleNamespace(monotonic=lambda: current_time))
        monkeypatch.setattr(network.asyncio, "sleep", early_sleep)
        monkeypatch.setattr(network, "public_url", AsyncMock(return_value="8.8.8.8"))
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            assert await web.fetch(f"https://{host}/first") == b"ok"
            assert await web.fetch(f"https://{host}/second") == b"ok"
            assert starts[1] - starts[0] >= interval
            assert len(sleeps) == 2
        finally:
            await web.close()

    run(exercise())


def test_other_host_progresses_while_crossref_is_pacing(store):
    ordinary_started = asyncio.Event()

    async def respond(request):
        if request.headers["host"] == "public.example":
            ordinary_started.set()
        return httpx.Response(200, content=b"ok")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.slots = asyncio.Semaphore(1)
        web.next_at["api.crossref.org"] = time.monotonic() + 0.3
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            crossref = asyncio.create_task(web.fetch("https://api.crossref.org/works/waiting"))
            for _ in range(10):
                if web.crossref_slot.locked():
                    break
                await asyncio.sleep(0)
            assert web.crossref_slot.locked()
            ordinary = asyncio.create_task(web.fetch("https://public.example/paper"))
            await asyncio.wait_for(ordinary_started.wait(), 0.1)
            assert await ordinary == b"ok"
            assert await crossref == b"ok"
        await web.close()

    run(exercise())


def test_crossref_spaces_retries_and_redirects(store):
    starts = []

    async def respond(request):
        starts.append(time.monotonic())
        if len(starts) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if len(starts) == 2:
            return httpx.Response(302, headers={"location": "/works/redirected"})
        return httpx.Response(200, content=b"ok")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            assert await web.fetch("https://api.crossref.org/works/retrying") == b"ok"
        assert len(starts) == 3
        assert all(
            later - earlier >= 1.05 for earlier, later in zip(starts, starts[1:], strict=False)
        )
        await web.close()

    run(exercise())


def test_crossref_cancellation_releases_queued_and_streaming_attempts(store):
    streaming_started = asyncio.Event()
    never_release = asyncio.Event()
    streaming_finished = asyncio.Event()
    seen = []

    async def respond(request):
        case = request.url.params["case"]
        seen.append(case)
        if case == "streaming":
            return httpx.Response(
                200, stream=WaitingStream(streaming_started, never_release, streaming_finished)
            )
        return httpx.Response(200, content=b"fresh")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            streaming = asyncio.create_task(
                web.fetch("https://api.crossref.org/works?case=streaming")
            )
            await streaming_started.wait()
            queued = asyncio.create_task(web.fetch("https://api.crossref.org/works?case=queued"))
            await asyncio.sleep(0.02)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued

            streaming.cancel()
            with pytest.raises(asyncio.CancelledError):
                await streaming
            assert streaming_finished.is_set()
            assert (
                await asyncio.wait_for(web.fetch("https://api.crossref.org/works?case=after"), 1.3)
                == b"fresh"
            )

        assert seen == ["streaming", "after"]
        await web.close()

    run(exercise())


def test_crossref_cancellation_before_headers_preserves_start_spacing(store):
    first_started = asyncio.Event()
    never_send_headers = asyncio.Event()
    starts = []

    async def respond(request):
        starts.append(time.monotonic())
        if request.url.path.endswith("/first"):
            first_started.set()
            await never_send_headers.wait()
        return httpx.Response(200, content=b"fresh")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        web.slots = asyncio.Semaphore(1)
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            first = asyncio.create_task(web.fetch("https://api.crossref.org/works/first"))
            await first_started.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert not web.pending
            assert not web.crossref_slot.locked()

            assert (
                await asyncio.wait_for(web.fetch("https://api.crossref.org/works/second"), 1.3)
                == b"fresh"
            )

        await asyncio.sleep(0.05)
        assert len(starts) == 2
        assert starts[1] - starts[0] >= 1.05
        assert not web.pending
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
