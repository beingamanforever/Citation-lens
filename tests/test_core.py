import asyncio
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from citation_lens.documents import Documents, html_document, pdf_document, render_pdf
from citation_lens.graph import Graphs, balanced, payload
from citation_lens.network import Web, public_url
from citation_lens.papers import Papers, aliases, identifier, normalize
from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "cache.sqlite")
    yield result
    result.close()


def paper(pid, year=2020, citations=10, doi=""):
    return {
        "id": pid,
        "provider": "openalex",
        "title": "Efficient attention " + pid,
        "abstract": "An exact attention method with tiling, performance and limitations.",
        "year": year,
        "citations": citations,
        "doi": doi,
        "arxiv": "",
        "references": [],
        "url": "https://openalex.org/" + pid[3:],
        "pdf": None,
    }


class FixturePapers:
    def __init__(self, store):
        self.store = store
        self.records = {
            p["id"]: p
            for p in [
                paper("OA:W1"),
                paper("OA:W2", 2017, 1000),
                paper("OA:W3", 2026, 0),
                paper("OA:W4"),
                paper("OA:W5"),
            ]
        }
        self.calls = 0
        self.failed = False
        self.links = {
            "OA:W1": ["OA:W2", "OA:W4"],
            "OA:W2": ["OA:W4"],
            "OA:W3": ["OA:W1"],
            "OA:W4": ["OA:W1"],
            "OA:W5": ["OA:W2"],
        }

    async def resolve(self, value):
        return self.records[value]

    async def neighbors(self, p, direction, limit):
        self.calls += 1
        if self.failed and direction == "forward":
            raise RuntimeError("fixture provider failed")
        ids = (
            self.links[p["id"]]
            if direction == "backward"
            else [pid for pid, refs in self.links.items() if p["id"] in refs]
        )
        return [self.records[pid] for pid in ids], {"indexed": len(ids), "sampled": False}


def test_graph_direction_shared_ancestor_cycle_and_resume(store):
    provider = FixturePapers(store)
    graphs = Graphs(provider)
    view = run(graphs.expand(["OA:W1", "OA:W2"], "exact attention", depth=3))
    graph = store.load("graph:" + view["graph_id"])
    pairs = [(e["from"], e["to"]) for e in graph["edges"]]
    assert ("OA:W1", "OA:W2") in pairs
    assert ("OA:W3", "OA:W1") in pairs  # Forward traversal still stores citing -> cited.
    assert ("OA:W1", "OA:W4") in pairs and ("OA:W4", "OA:W1") in pairs
    assert ("OA:W2", "OA:W4") in pairs  # Shared ancestor, not duplicated tree nodes.
    assert len(pairs) == len(set(pairs)) and view["node_count"] == 5
    before = provider.calls
    run(Graphs(provider).expand(["OA:W1"], "exact attention", view["graph_id"]))
    assert provider.calls == before


def test_node_budget_resumes_and_partial_failure(store):
    provider = FixturePapers(store)
    graphs = Graphs(provider)
    view = run(graphs.expand(["OA:W1"], "attention", max_nodes=2, depth=3))
    assert view["node_count"] == 2 and "max_nodes reached" in view["stops"]
    view = run(graphs.expand(["OA:W1"], "attention", view["graph_id"], max_nodes=10))
    assert view["node_count"] > 2
    provider.failed = True
    view = run(graphs.expand(["OA:W2"], "attention", direction="both"))
    assert view["error_count"] == 1 and view["node_count"] >= 2


def test_recent_work_is_not_drowned_by_citation_counts():
    selected = balanced(
        [paper("OA:W1", 2020, 10000), paper("OA:W2", 2026, 0), paper("OA:W3", 2021, 1000)],
        "attention",
        2,
    )
    assert "OA:W2" in [p["id"] for p in selected]


def test_cross_provider_alias_deduplication(store):
    provider = FixturePapers(store)
    provider.records["OA:W2"]["doi"] = "10.1234/same"
    alternate = paper("S2:" + "a" * 40, doi="10.1234/same")
    alternate["provider"] = "semantic_scholar"
    provider.records[alternate["id"]] = alternate
    view = run(Graphs(provider).expand(["OA:W2", alternate["id"]], "attention", depth=1))
    assert alternate["id"] not in store.load("graph:" + view["graph_id"])["nodes"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"depth": 4},
        {"beam": 0},
        {"max_nodes": 201},
        {"neighbors": 41},
        {"direction": "sideways"},
        {"graph_id": "missing"},
        {"depth": True},
    ],
)
def test_graph_rejects_invalid_inputs(store, kwargs):
    with pytest.raises(ValueError):
        run(Graphs(FixturePapers(store)).expand(["OA:W1"], "attention", **kwargs))


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1706.03762", "ARXIV:1706.03762"),
        ("https://arxiv.org/abs/1706.03762v2", "ARXIV:1706.03762v2"),
        ("https://doi.org/10.1234/abc", "DOI:10.1234/abc"),
        ("W123", "OA:W123"),
    ],
)
def test_identifiers(value, expected):
    assert identifier(value) == expected


@pytest.mark.parametrize("value", ["https://127.0.0.1/a", "file:///etc/passwd", "OA:../../secrets"])
def test_identifier_rejects_urls_and_paths(value):
    with pytest.raises(ValueError):
        identifier(value)


def test_openalex_abstract_and_alias_normalization():
    p = normalize(
        {
            "id": "https://openalex.org/W123",
            "title": "Example",
            "doi": "https://doi.org/10.1234/X",
            "abstract_inverted_index": {"world": [1], "Hello": [0]},
            "primary_location": {"landing_page_url": "https://arxiv.org/abs/1706.03762v2"},
        },
        "openalex",
    )
    assert p["abstract"] == "Hello world"
    assert "DOI:10.1234/x" in aliases(p) and "ARXIV:1706.03762" in aliases(p)


def test_cache_ttl_restart_and_payload_measurement(tmp_path):
    path = tmp_path / "store.sqlite"
    store = Store(path)
    store.put("expired", b"x", ttl=-1)
    assert store.get("expired") is None
    store.save("graph", {"nodes": [1]})
    store.close()
    with_store = Store(path)
    assert with_store.load("graph") == {"nodes": [1]}
    with_store.close()
    output = payload({"unicode": "科学"})
    assert output["payload_bytes"] == len(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()
    )


def test_network_singleflight_cache_and_byte_limit(store):
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, content=b'{"ok":true}')

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        url = "https://api.openalex.org/works?search=test"
        a, b = await asyncio.gather(web.json(url), web.json(url))
        assert a == b == {"ok": True} and len(calls) == 1
        assert await web.json(url) == a and web.hits == 1
        with pytest.raises(ValueError):
            await web.fetch(url, max_bytes=2)
        with pytest.raises(ValueError):
            await web.fetch(url + "2", max_bytes=2)
        await web.close()

    run(exercise())


def test_network_retry_and_credential_isolation(store, monkeypatch):
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


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://user:pass@example.com", "https://example.com:99"]
)
def test_url_policy(url):
    with pytest.raises(ValueError):
        run(public_url(url))


def test_url_blocks_private_dns():
    async def exercise():
        with patch.object(
            asyncio.get_running_loop(),
            "getaddrinfo",
            new=AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]),
        ):
            with pytest.raises(ValueError):
                await public_url("https://private.example/a")

    run(exercise())


def test_html_math_tables_caption_and_paged_read(store):
    doc = html_document(
        (ROOT / "tests/fixtures/paper.html").read_bytes(), "https://arxiv.org/html/1706.03762"
    )
    assert "$QK^T / \\sqrt{d}$" in doc["text"]
    assert "Baseline" in doc["text"] and "12 ms" in doc["text"] and "|" in doc["text"]
    assert doc["figures"][0]["url"] == "https://arxiv.org/html/figures/architecture.png"
    assert "Exact attention" in doc["figures"][0]["caption"]
    assert "$K$" in doc["figures"][0]["caption"]
    assert "CITATIONLENSMATH" not in doc["figures"][0]["caption"]
    assert "Ignore navigation" not in doc["text"]
    p = FixturePapers(store)
    p.web = Web(store)
    docs = Documents(p)
    doc.update(paper_id="OA:W1", source_url="https://public.example/paper", source_sha256="test")
    store.save("document:OA:W1", doc)

    async def exercise():
        outline = await docs.read("OA:W1", outline_only=True)
        assert not outline["text"] and outline["outline"] and outline["untrusted_content"]
        parts, offset = [], 0
        while True:
            result = await docs.read("OA:W1", offset=offset, max_chars=90)
            assert result["overview_included"] is (offset == 0)
            if offset:
                assert not result["outline"] and not result["figures"]
                assert result["figure_count"] == len(doc["figures"])
            parts.append(result["text"])
            offset = result["next_offset"]
            if offset is None:
                break
        assert "".join(parts) == doc["text"]
        await p.web.close()

    run(exercise())


def example_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): writer._add_object(
                        DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    )
                }
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"1 0 0 rg 20 30 150 100 re f BT /F1 14 Tf 20 250 Td (Evidence diagram) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_text_and_actual_visual():
    data = example_pdf()
    doc = pdf_document(data)
    assert "Evidence diagram" in doc["text"] and "## Page 1" in doc["text"]
    image = Image.open(io.BytesIO(render_pdf(data, 1, 600)))
    assert max(image.size) <= 600
    assert image.getpixel((80, image.height - 100))[0] > 240
    assert image.getpixel((80, image.height - 100))[1] < 10  # The red diagram survived.
    with pytest.raises(ValueError):
        render_pdf(data, 2, 600)


def test_concurrent_pdf_rendering_preserves_images():
    data = example_pdf()
    expected = render_pdf(data, 1, 600)

    async def exercise():
        images = await asyncio.gather(
            *[asyncio.to_thread(render_pdf, data, 1, 600) for _ in range(24)]
        )
        assert all(image == expected for image in images)

    run(exercise())


def test_provider_contract_and_sorted_forward_queries(store):
    urls = []
    raw = {
        "id": "https://openalex.org/W1",
        "title": "Exact attention",
        "publication_year": 2026,
        "cited_by_count": 0,
        "referenced_works": ["https://openalex.org/W2"],
    }

    async def respond(request):
        urls.append(str(request.url))
        if request.url.path == "/works/W1":
            return httpx.Response(200, json=raw)
        neighbor = {**raw, "id": "https://openalex.org/W2"}
        return httpx.Response(200, json={"results": [neighbor], "meta": {"count": 12}})

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        papers = Papers(web)
        seed = await papers.resolve("OA:W1")
        candidates, coverage = await papers.neighbors(seed, "forward", 10)
        assert candidates[0]["id"] == "OA:W2" and coverage["retrieved_unique"] == 1
        assert any("cited_by_count" in u for u in urls) and any(
            "publication_date" in u for u in urls
        )
        backward, _ = await papers.neighbors(seed, "backward", 10)
        assert backward[0]["id"] == "OA:W2"
        await web.close()

    run(exercise())


def test_sdk_stdio_tools_and_cached_calls(tmp_path):
    directory = tmp_path / "data"
    store = Store(directory / "cache.sqlite")
    p = paper("OA:W1")
    store.save("paper:OA:W1", p)
    store.save(
        "document:OA:W1",
        {
            "paper_id": "OA:W1",
            "source_url": p["url"],
            "format": "html",
            "warnings": [],
            "text": "# Method\nExact attention evidence.",
            "figures": [],
        },
    )
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "citation_lens.server"],
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "CITATION_LENS_DATA": str(directory),
            },
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            info = await session.initialize()
            assert "citation counts are not SOTA" in info.instructions
            tools = await session.list_tools()
            assert len(tools.tools) == 5
            result = await session.call_tool(
                "research_read", {"paper_id": "OA:W1", "max_chars": 100}
            )
            assert not result.isError and "Exact attention evidence" in str(result.content)
            assert result.structuredContent is None
            text = result.content[0].text
            assert json.loads(text)["payload_bytes"] == len(text.encode())
            error = await session.call_tool(
                "research_expand", {"seed_ids": ["OA:W1"], "query": "attention", "depth": 9}
            )
            assert error.isError
            error = await session.call_tool("research_graph", {"graph_id": "unknown"})
            assert error.isError

    run(exercise())


def test_pins_public_ip_and_keeps_tls_hostname(store):
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx.Response(200, content=b"image")

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with patch(
            "citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")
        ) as resolver:
            url = "https://public.example/paper.pdf"
            assert await web.fetch(url) == b"image"
            assert await web.fetch(url) == b"image"
            assert resolver.await_count == 1  # Cached bytes require no DNS or network.
        assert seen[0].url.host == "8.8.8.8"
        assert seen[0].headers["host"] == "public.example"
        assert seen[0].extensions["sni_hostname"] == "public.example"
        await web.close()

    run(exercise())


def test_larger_neighbor_budget_reexpands(store):
    provider = FixturePapers(store)
    graphs = Graphs(provider)
    view = run(graphs.expand(["OA:W1"], "attention", neighbors=1))
    count = provider.calls
    run(graphs.expand(["OA:W1"], "attention", view["graph_id"], neighbors=5))
    assert provider.calls > count


def test_native_arxiv_search_and_unindexed_paper_read(store):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>1</opensearch:totalResults>
        <entry><id>http://arxiv.org/abs/2610.00001v1</id><title>Fresh exact attention</title>
        <published>2026-10-07T00:00:00Z</published><summary>Fresh preprint with a method.</summary>
        </entry></feed>"""

    async def respond(request):
        if "/api/query" in request.url.path:
            assert request.url.params["sortBy"] == "submittedDate"
            return httpx.Response(200, content=atom)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(404)
        return httpx.Response(200, content=(ROOT / "tests/fixtures/paper.html").read_bytes())

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        papers = Papers(web)
        with patch("citation_lens.network.public_url", new=AsyncMock(return_value="8.8.8.8")):
            found, total = await papers.search("attention", "arxiv", 5, True, 2025)
            assert total == 1 and found[0]["citation_status"]
            read = await Documents(papers).read(found[0]["id"])
            assert "Baseline" in read["text"]
            graph = await Graphs(papers).expand([found[0]["id"]], "attention")
            assert graph["node_count"] == 1 and graph["error_count"] == 2
        await web.close()

    run(exercise())


def test_semantic_scholar_direction_and_coverage(store):
    seed_id = "S2:" + "a" * 40
    raw = {
        "paperId": "a" * 40,
        "title": "Seed attention",
        "year": 2020,
        "externalIds": {"ArXiv": "1706.03762"},
        "citationCount": 100,
    }

    async def respond(request):
        if request.url.path.endswith("/references"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citedPaper": {**raw, "paperId": "b" * 40, "externalIds": {}},
                            "isInfluential": True,
                            "intents": ["methodology"],
                        }
                    ],
                    "next": 20,
                },
            )
        if request.url.path.endswith("/citations"):
            return httpx.Response(
                200,
                json={"data": [{"citingPaper": {**raw, "paperId": "c" * 40, "externalIds": {}}}]},
            )
        return httpx.Response(200, json=raw)

    async def exercise():
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        graph = await Graphs(Papers(web)).expand([seed_id], "attention")
        assert {tuple(e[k] for k in ("from", "to")) for e in graph["edges"]} == {
            (seed_id, "S2:" + "b" * 40),
            ("S2:" + "c" * 40, seed_id),
        }
        assert graph["coverage"][0]["sampled"] and graph["coverage"][0]["next_offset"] == 20
        await web.close()

    run(exercise())


def test_sdk_visual_image_block(tmp_path):
    data = example_pdf()
    directory = tmp_path / "data"
    store = Store(directory / "cache.sqlite")
    p = paper("OA:W1")
    p["pdf"] = "https://public.example/paper.pdf"
    store.save("paper:OA:W1", p)
    key = (
        "http:" + hashlib.sha256(json.dumps([p["pdf"], None], sort_keys=True).encode()).hexdigest()
    )
    store.put(key, data)
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "run.py")],
            env={**os.environ, "CITATION_LENS_DATA": str(directory)},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            results = await asyncio.gather(
                *[
                    session.call_tool("research_visual", {"paper_id": "OA:W1", "page": 1})
                    for _ in range(12)
                ]
            )
            for result in results:
                assert not result.isError and any(c.type == "image" for c in result.content)

    run(exercise())


def test_explicit_arxiv_revision_is_preserved(store):
    p = FixturePapers(store)
    record = p.records["OA:W1"]
    record["arxiv"] = "1706.03762"
    p.records["ARXIV:1706.03762v1"] = record
    p.web = Web(store)

    async def exercise():
        selected = await Documents(p).selected_paper("ARXIV:1706.03762v1")
        assert selected["arxiv"] == "1706.03762v1"
        assert selected["pdf"].endswith("1706.03762v1")
        assert record["arxiv"] == "1706.03762"  # Never poison shared canonical metadata.
        await p.web.close()

    run(exercise())


def test_latex_survives_markdown_escaping_and_error_html_is_rejected():
    data = b'<article><math alttext="x_i * y_j">x</math><math><mi>z</mi></math></article>'
    doc = html_document(data, "https://arxiv.org/html/1")
    assert "$x_i * y_j$" in doc["text"]
    assert any("lacked LaTeX" in w for w in doc["warnings"])
    with pytest.raises(ValueError):
        html_document(
            b"<html><body><h1>Unavailable paper</h1></body></html>", "https://arxiv.org/html/1"
        )
