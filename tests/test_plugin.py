import asyncio
import json
import os
import subprocess
import sys
import tomllib
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from conftest import run, s2
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from citation_lens import network, papers, server
from citation_lens.network import Web
from citation_lens.papers import keys
from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"research_search", "research_expand", "research_graph", "research_read", "research_visual"}


def save_papers(directory, *records):
    store = Store(directory / "cache.sqlite")
    for record in records:
        store.save("paper:" + record["id"], record)
        for key in keys(record):
            if not key.startswith("title:"):
                store.save("alias:" + key, record["id"])
    store.close()


def s2_response(key, title, *, doi="", arxiv="", abstract="Indexed abstract."):
    external_ids = {}
    if doi:
        external_ids["DOI"] = doi
    if arxiv:
        external_ids["ArXiv"] = arxiv
    return {
        "paperId": key.ljust(40, "0"),
        "title": title,
        "year": 2024,
        "publicationDate": "2024-01-01",
        "citationCount": 3,
        "abstract": abstract,
        "externalIds": external_ids,
        "authors": [{"name": "Example Author"}],
    }


def arxiv_feed(
    arxiv_id="2401.00001",
    title="Graph routing reference",
    abstract="Graph routing methods and evidence.",
    doi="",
):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
        xmlns:arxiv="http://arxiv.org/schemas/atom">
      <opensearch:totalResults>1</opensearch:totalResults>
      <entry>
        <id>http://arxiv.org/abs/{arxiv_id}</id>
        <title>{title}</title>
        <published>2024-01-01T00:00:00Z</published>
        <summary>{abstract}</summary>
        <author><name>Example Author</name></author>
        {f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""}
      </entry>
    </feed>""".encode()


def original_host(request):
    return request.headers.get("host", request.url.host).split(":", 1)[0]


def crossref_record(doi, *, abstract="Recovered graph routing evidence."):
    return {
        "DOI": doi,
        "title": ["Exact graph routing evidence"],
        "author": [{"given": "Example", "family": "Author"}],
        "published": {"date-parts": [[2025, 1, 2]]},
        "abstract": f"<jats:p>{abstract}</jats:p>",
    }


@asynccontextmanager
async def connected_session(tmp_path, monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    created = []

    def web_factory(store):
        web = Web(store, client)
        web.attempts = 1
        web.max_wait = 0
        created.append(web)
        return web

    async def public_url(_url):
        return "8.8.8.8"

    monkeypatch.setenv("CITATION_LENS_DATA", str(tmp_path))
    monkeypatch.setattr(server, "Web", web_factory)
    monkeypatch.setattr(network, "public_url", public_url)
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=3)
    ) as session:
        yield session, created[0]


class StallingStream(httpx.AsyncByteStream):
    def __init__(self, payload=b""):
        self.payload = payload
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield self.payload

    async def aclose(self):
        pass


def test_manifests_point_at_the_plugin_and_hold_no_secrets():
    portable = json.loads((ROOT / "plugin.json").read_text())
    claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert portable["name"] == claude["name"] == "citation-lens"
    assert portable["version"] == claude["version"] == version
    assert "name: research" in (ROOT / "skills/research/SKILL.md").read_text()
    for name, variable in (("mcp.json", "PLUGIN_ROOT"), (".mcp.json", "CLAUDE_PLUGIN_ROOT")):
        config = json.loads((ROOT / name).read_text())["mcpServers"]["citation-lens"]
        assert config["args"][-1] == "${" + variable + "}/run.py"
        assert "API_KEY" not in json.dumps(config)
    for market in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
        source = json.loads((ROOT / market).read_text())["plugins"][0]["source"]
        assert (ROOT / (source["path"] if isinstance(source, dict) else source) / "run.py").exists()


def test_launch_is_warning_free():
    result = subprocess.run(
        [sys.executable, "-W", "error", str(ROOT / "run.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Citation Lens" in result.stdout and not result.stderr


def test_stdio_server_lists_tools_and_reads_cached_abstracts(tmp_path):
    store = Store(tmp_path / "cache.sqlite")
    paper = s2(
        "a1", "Fast Exact Attention", 2022, 3000, "Exact attention with tiling.", arxiv="2205.14135"
    )
    store.save("paper:" + paper["id"], paper)
    store.save("alias:arxiv:2205.14135", paper["id"])
    store.save("alias:s2:" + paper["s2"], paper["id"])
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "run.py")],
            env={**os.environ, "CITATION_LENS_DATA": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as s:
            info = await s.initialize()
            assert "untrusted" in info.instructions
            assert {tool.name for tool in (await s.list_tools()).tools} == TOOLS
            result = await s.call_tool("research_read", {"paper_id": ["2205.14135"]})
            text = result.content[0].text
            body = json.loads(text)
            assert not result.isError and result.structuredContent is None
            assert body["papers"][0]["abstract"] == "Exact attention with tiling."
            assert body["missing"] == [] and body["payload_bytes"] == len(text.encode())
            assert (await s.call_tool("research_graph", {"graph_id": "unknown"})).isError
            bad = await s.call_tool("research_expand", {"seed_ids": [], "query": "x"})
            assert bad.isError

    run(exercise())


def test_native_verified_urls_read_without_search(tmp_path, monkeypatch):
    requests = []

    async def respond(request):
        requests.append(request)
        assert original_host(request) == "api.semanticscholar.org"
        assert request.url.path == "/graph/v1/paper/batch"
        assert json.loads(request.content)["ids"] == [
            "DOI:10.1234/native",
            "arXiv:2401.00001",
        ]
        return httpx.Response(
            200,
            json=[
                s2_response("doi", "Native DOI paper", doi="10.1234/native"),
                s2_response("arxiv", "Native arXiv paper", arxiv="2401.00001"),
            ],
        )

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_read",
                {
                    "paper_id": [
                        "https://doi.org/10.1234/native",
                        "https://arxiv.org/abs/2401.00001",
                    ],
                    "part": "abstract",
                },
            )
            body = json.loads(result.content[0].text)
            assert not result.isError and body["missing"] == []
            assert [paper["title"] for paper in body["papers"]] == [
                "Native DOI paper",
                "Native arXiv paper",
            ]
            assert len(requests) == web.requests == 1
            assert "/search" not in str(requests[0].url)

    run(exercise())


def test_cached_native_abstracts_skip_http_and_only_missing_evidence_enriches(
    tmp_path, monkeypatch
):
    complete_arxiv = papers.from_arxiv(
        arxiv_feed("2401.00001", "Complete cached preprint", "Complete preprint evidence.")
    )[0][0]
    complete_openalex = papers.from_openalex(
        {
            "id": "https://openalex.org/W100",
            "title": "Complete cached journal paper",
            "abstract_inverted_index": {"Complete": [0], "journal": [1], "evidence.": [2]},
        }
    )
    incomplete_arxiv = papers.from_arxiv(
        arxiv_feed("2402.00002", "Incomplete cached preprint", "")
    )[0][0]
    incomplete_openalex = papers.from_openalex(
        {"id": "https://openalex.org/W300", "title": "Incomplete cached journal paper"}
    )
    save_papers(tmp_path, complete_arxiv, complete_openalex, incomplete_arxiv, incomplete_openalex)
    requests = []

    async def respond(request):
        requests.append(request)
        if original_host(request) == "api.semanticscholar.org":
            assert json.loads(request.content)["ids"] == ["arXiv:2402.00002"]
            return httpx.Response(
                200,
                json=[
                    s2_response(
                        "enriched",
                        "Enriched cached preprint",
                        arxiv="2402.00002",
                        abstract="Recovered preprint evidence.",
                    )
                ],
            )
        assert original_host(request) == "api.openalex.org"
        assert request.url.path == "/works/W300"
        return httpx.Response(
            200,
            json={
                "id": "https://openalex.org/W300",
                "title": "Enriched cached journal paper",
                "abstract_inverted_index": {"Recovered": [0], "journal": [1], "evidence.": [2]},
            },
        )

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            complete = await session.call_tool(
                "research_read",
                {"paper_id": ["ARXIV:2401.00001v4", "OA:W100"]},
            )
            complete_body = json.loads(complete.content[0].text)
            assert not complete.isError and complete_body["missing"] == []
            assert [paper["abstract"] for paper in complete_body["papers"]] == [
                "Complete preprint evidence.",
                "Complete journal evidence.",
            ]
            assert web.requests == 0 and requests == []

            mixed = await session.call_tool(
                "research_read",
                {
                    "paper_id": [
                        "ARXIV:2401.00001",
                        "OA:W100",
                        "ARXIV:2402.00002",
                        "OA:W300",
                    ]
                },
            )
            mixed_body = json.loads(mixed.content[0].text)
            assert not mixed.isError and mixed_body["missing"] == []
            assert [paper["abstract"] for paper in mixed_body["papers"]] == [
                "Complete preprint evidence.",
                "Complete journal evidence.",
                "Recovered preprint evidence.",
                "Recovered journal evidence.",
            ]
            assert web.requests == len(requests) == 2
            assert all("2401.00001" not in str(request.url) for request in requests)
            assert all("W100" not in str(request.url) for request in requests)

    run(exercise())


def test_failed_cached_enrichment_keeps_missing_abstract_visible(tmp_path, monkeypatch):
    incomplete = s2("d", "Cached paper without evidence") | {
        "abstract": " \n\t ",
        "abstract_source": "semantic_scholar",
    }
    save_papers(tmp_path, incomplete)

    async def respond(_request):
        return httpx.Response(503)

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_read", {"paper_id": incomplete["id"], "part": "abstract"}
            )
            body = json.loads(result.content[0].text)
            assert not result.isError and body["missing"] == []
            assert body["papers"][0]["abstract"] is None
            assert body["papers"][0]["abstract_source"] is None
            assert web.requests == 1

    run(exercise())


def test_cached_reuse_requires_nonblank_title_and_abstract(tmp_path, monkeypatch):
    whitespace_abstract = papers.from_arxiv(
        arxiv_feed("2403.00003", "Cached title", "Original evidence.")
    )[0][0] | {"abstract": " \n\t ", "abstract_source": "arxiv"}
    missing_title = papers.from_arxiv(
        arxiv_feed("2404.00004", "Original title", "Existing primary evidence.")
    )[0][0] | {"title": ""}
    save_papers(tmp_path, whitespace_abstract, missing_title)
    requests = []

    async def respond(request):
        requests.append(request)
        assert original_host(request) == "api.semanticscholar.org"
        assert json.loads(request.content)["ids"] == ["arXiv:2403.00003", "arXiv:2404.00004"]
        return httpx.Response(
            200,
            json=[
                s2_response(
                    "a",
                    "Indexed title",
                    arxiv="2403.00003",
                    abstract="Recovered indexed evidence.",
                ),
                s2_response(
                    "b",
                    "Recovered missing title",
                    arxiv="2404.00004",
                    abstract="Secondary evidence.",
                ),
            ],
        )

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_read",
                {"paper_id": ["ARXIV:2403.00003", "ARXIV:2404.00004"]},
            )
            body = json.loads(result.content[0].text)
            assert not result.isError and body["missing"] == []
            assert [(paper["title"], paper["abstract"]) for paper in body["papers"]] == [
                ("Cached title", "Recovered indexed evidence."),
                ("Recovered missing title", "Existing primary evidence."),
            ]
            assert [paper["abstract_source"] for paper in body["papers"]] == [
                "semantic_scholar",
                "arxiv",
            ]
            assert web.requests == len(requests) == 1

    run(exercise())


def test_atom_publication_doi_joins_cached_preprint_to_primary_bibliography(tmp_path, monkeypatch):
    seed = s2(
        "seed",
        "Graph routing seed",
        2025,
        20,
        "Graph routing seed evidence.",
        arxiv="2501.00001",
    )
    preprint = papers.from_arxiv(
        arxiv_feed(
            "2401.00001",
            "Publication linked graph routing",
            "Graph routing publication evidence.",
            doi="10.1234/published-version",
        )
    )[0][0]
    save_papers(tmp_path, seed, preprint)
    requests = []
    bibliography = b"""<html><body><li class="ltx_bibitem" id="bib.publication">
      <a href="https://doi.org/10.1234/published-version">published article</a>
    </li></body></html>"""

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path, request.url.query.decode()))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None])
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "arxiv.org":
            return httpx.Response(200, content=bibliography)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, _web):
            cached = await session.call_tool(
                "research_read", {"paper_id": "DOI:10.1234/published-version"}
            )
            cached_body = json.loads(cached.content[0].text)
            assert cached_body["papers"][0]["id"] == "ARXIV:2401.00001"
            assert requests == []

            result = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["ARXIV:2501.00001"],
                    "query": "graph routing publication",
                    "direction": "backward",
                },
            )
            body = json.loads(result.content[0].text)
            assert not result.isError
            assert body["papers"][0]["id"] == "ARXIV:2401.00001"
            assert body["edges"] == [["ARXIV:2501.00001", "ARXIV:2401.00001"]]
            assert body["edge_evidence"] == [
                {
                    "edge": ["ARXIV:2501.00001", "ARXIV:2401.00001"],
                    "matched_id": "DOI:10.1234/published-version",
                    "source": "primary_arxiv",
                    "url": "https://arxiv.org/html/2501.00001#bib.publication",
                }
            ]
            assert not any(host == "export.arxiv.org" for host, _, _ in requests)

    run(exercise())


def test_versioned_expand_keeps_canonical_cached_metadata(tmp_path, monkeypatch):
    seed = papers.from_arxiv(
        arxiv_feed(
            "2406.00006v3",
            "Canonical graph routing seed",
            "Latest cached seed evidence.",
        )
    )[0][0]
    neighbor = papers.from_arxiv(
        arxiv_feed(
            "2306.00007",
            "Graph routing reference",
            "Cached reference evidence.",
        )
    )[0][0] | {"refs": ["oa:W700"]}
    save_papers(tmp_path, seed, neighbor)
    requests = []
    bibliography = b"""<html><body><li class="ltx_bibitem" id="bib.versioned">
      <a href="https://arxiv.org/abs/2306.00007">versioned source</a>
    </li></body></html>"""

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None])
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "arxiv.org":
            assert request.url.path == "/html/2406.00006v1"
            return httpx.Response(200, content=bibliography)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            expanded = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["ARXIV:2406.00006v1"],
                    "query": "graph routing reference",
                    "direction": "backward",
                },
            )
            expanded_body = json.loads(expanded.content[0].text)
            assert not expanded.isError
            assert expanded_body["edge_evidence"] == [
                {
                    "edge": ["ARXIV:2406.00006", "ARXIV:2306.00007"],
                    "matched_id": "ARXIV:2306.00007",
                    "source": "primary_arxiv",
                    "url": "https://arxiv.org/html/2406.00006v1#bib.versioned",
                }
            ]
            request_count = len(requests)

            read = await session.call_tool(
                "research_read", {"paper_id": "ARXIV:2406.00006", "part": "abstract"}
            )
            read_body = json.loads(read.content[0].text)
            assert not read.isError and read_body["missing"] == []
            assert read_body["papers"][0]["abstract"] == "Latest cached seed evidence."
            assert read_body["papers"][0]["url"] == "https://arxiv.org/abs/2406.00006"
            assert len(requests) == request_count == web.requests

    run(exercise())


def test_expand_then_read_reuses_index_neighbor_without_http(tmp_path, monkeypatch):
    seed = s2(
        "a",
        "Graph routing seed",
        2025,
        20,
        "Graph routing seed evidence.",
        arxiv="2501.00001",
    )
    neighbor = s2_response(
        "b",
        "Indexed graph routing neighbor",
        arxiv="2401.00001",
        doi="10.1234/indexed-neighbor",
        abstract="Indexed neighbor evidence.",
    )
    save_papers(tmp_path, seed)
    requests = []

    async def respond(request):
        requests.append(request)
        assert original_host(request) == "api.semanticscholar.org"
        ids = json.loads(request.content)["ids"]
        if ids == [seed["s2"]]:
            return httpx.Response(200, json=[{"references": [neighbor]}])
        assert ids == [neighbor["paperId"]]
        return httpx.Response(200, json=[neighbor | {"references": []}])

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            expanded = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": [seed["id"]],
                    "query": "graph routing neighbor",
                    "direction": "backward",
                },
            )
            assert not expanded.isError, expanded.content[0].text
            expanded_body = json.loads(expanded.content[0].text)
            assert expanded_body["papers"][0]["id"] == "ARXIV:2401.00001"
            request_count = len(requests)

            read = await session.call_tool(
                "research_read",
                {
                    "paper_id": [
                        "ARXIV:2401.00001",
                        "DOI:10.1234/indexed-neighbor",
                        "S2:" + neighbor["paperId"],
                    ]
                },
            )
            read_body = json.loads(read.content[0].text)
            assert not read.isError and read_body["missing"] == []
            assert [paper["id"] for paper in read_body["papers"]] == ["ARXIV:2401.00001"] * 3
            assert [paper["abstract"] for paper in read_body["papers"]] == [
                "Indexed neighbor evidence."
            ] * 3
            assert [paper["abstract_source"] for paper in read_body["papers"]] == [
                "semantic_scholar"
            ] * 3
            assert len(requests) == request_count == web.requests

    run(exercise())


def test_search_then_read_reuses_fused_aliases_without_http(tmp_path, monkeypatch):
    indexed = s2_response(
        "c",
        "Fused graph routing paper",
        arxiv="2405.00005",
        abstract="Fused indexed evidence.",
    )
    requests = []

    async def respond(request):
        requests.append(request)
        host = original_host(request)
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json={"data": [indexed], "total": 1})
        assert host == "api.openalex.org"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W500",
                        "title": "Fused graph routing paper",
                        "doi": "https://doi.org/10.1234/fused-publication",
                        "ids": {"arxiv": "https://arxiv.org/abs/2405.00005"},
                        "cited_by_count": 12,
                    }
                ],
                "meta": {"count": 1},
            },
        )

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            searched = await session.call_tool(
                "research_search",
                {
                    "query": ["fused graph routing"],
                    "provider": ["semantic_scholar", "openalex"],
                },
            )
            assert not searched.isError, searched.content[0].text
            searched_body = json.loads(searched.content[0].text)
            assert searched_body["papers"][0]["id"] == "ARXIV:2405.00005"
            request_count = len(requests)

            read = await session.call_tool(
                "research_read",
                {
                    "paper_id": [
                        "DOI:10.1234/fused-publication",
                        "OA:W500",
                        "S2:" + indexed["paperId"],
                    ]
                },
            )
            read_body = json.loads(read.content[0].text)
            assert not read.isError and read_body["missing"] == []
            assert [paper["id"] for paper in read_body["papers"]] == ["ARXIV:2405.00005"] * 3
            assert [paper["abstract"] for paper in read_body["papers"]] == [
                "Fused indexed evidence."
            ] * 3
            assert [paper["abstract_source"] for paper in read_body["papers"]] == [
                "semantic_scholar"
            ] * 3
            assert len(requests) == request_count == web.requests == 2

    run(exercise())


def test_backward_expand_uses_one_primary_arxiv_batch_after_index_outages(tmp_path, monkeypatch):
    seed = s2(
        "seed",
        "Graph routing seed",
        2025,
        20,
        "Graph routing seed evidence.",
        arxiv="2501.00001",
    )
    save_papers(tmp_path, seed)
    requests = []
    bibliography = b"""<html><body><ol class="ltx_bibliography">
      <li class="ltx_bibitem" id="bib.bib1">
        <a href="https://arxiv.org/abs/2401.00001">Reference</a>
      </li>
      <li class="ltx_bibitem" id="bib.bib2">
        <a href="https://doi.org/10.9999/unresolved">Missing DOI</a>
      </li>
    </ol></body></html>"""

    async def respond(request):
        host = original_host(request)
        requests.append((request.method, host, request.url.path, request.url.query.decode()))
        if host in ("api.semanticscholar.org", "api.openalex.org"):
            return httpx.Response(429)
        if host == "arxiv.org":
            assert request.url.path == "/html/2501.00001v2"
            return httpx.Response(200, content=bibliography)
        if host == "export.arxiv.org":
            assert request.url.params["id_list"] == "2401.00001"
            return httpx.Response(200, content=arxiv_feed())
        if host == "api.crossref.org":
            assert "10.9999/unresolved" in request.url.path
            return httpx.Response(404)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["https://arxiv.org/abs/2501.00001v2"],
                    "query": "graph routing",
                    "direction": "backward",
                },
            )
            body = json.loads(result.content[0].text)
            assert not result.isError
            assert body["papers"][0]["id"] == "ARXIV:2401.00001"
            assert body["edges"] == [["ARXIV:2501.00001", "ARXIV:2401.00001"]]
            assert body["edge_evidence"] == [
                {
                    "edge": ["ARXIV:2501.00001", "ARXIV:2401.00001"],
                    "matched_id": "ARXIV:2401.00001",
                    "source": "primary_arxiv",
                    "url": "https://arxiv.org/html/2501.00001v2#bib.bib1",
                }
            ]
            coverage = body["seeds"][0]
            assert coverage["references"] == 1
            assert coverage["reference_coverage"] == {
                "source": "primary_arxiv",
                "returned": 1,
                "total": 2,
                "inspected": 2,
                "identified": 2,
                "unidentified": 0,
                "ambiguous": 0,
                "unresolved": 1,
                "truncated": 0,
                "metadata_truncated": 0,
            }
            errors = " ".join(error["error"] for error in body["errors"])
            assert "Semantic Scholar" in errors and "OpenAlex" in errors
            assert "1 unresolved identifier/item(s)" in errors
            assert "DOI:10.9999/unresolved" in errors

            assert web.requests == len(requests) == 5
            assert sum(host == "api.semanticscholar.org" for _, host, _, _ in requests) == 1
            assert sum(host == "api.openalex.org" for _, host, _, _ in requests) == 1
            assert sum(host == "export.arxiv.org" for _, host, _, _ in requests) == 1
            assert not any("search" in path for _, _, path, _ in requests)
            assert sum(host == "api.crossref.org" for _, host, _, _ in requests) == 1
            assert not any(
                ("10.9999" in path or "10.9999" in query) and host != "api.crossref.org"
                for _, host, path, query in requests
            )

    run(exercise())


@pytest.mark.parametrize("stalled_provider", ["semantic_scholar", "journal_openalex"])
def test_mixed_seed_stall_keeps_available_primary_reference(
    tmp_path, monkeypatch, stalled_provider
):
    arxiv_seed = s2("a", "arXiv seed", 2024, 10, "arXiv seed evidence.", arxiv="2205.14135")
    journal_seed = papers.from_s2(s2_response("b", "Journal seed", doi="10.1000/journal"))
    cached_raw = s2_response(
        "c",
        "Memory efficient exact attention reference",
        doi="10.1000/cached",
        abstract="Memory efficient exact attention.",
    )
    cached_raw["references"] = [{"paperId": "d" * 40}]
    cached = papers.from_s2(cached_raw)
    save_papers(tmp_path, arxiv_seed, journal_seed, cached)

    stalled_stream = StallingStream()
    requests = []
    bibliography = b"""<html><body><li class="ltx_bibitem" id="bib.cached">
      <a href="https://doi.org/10.1000/cached">cached reference</a>
    </li></body></html>"""

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host == "api.semanticscholar.org":
            assert request.url.path.endswith("/paper/batch")
            if stalled_provider == "semantic_scholar":
                return httpx.Response(200, stream=stalled_stream)
            return httpx.Response(200, json=[None, None])
        if host == "api.openalex.org":
            if "10.48550" in request.url.path:
                return httpx.Response(404)
            if "10.1000/journal" in request.url.path:
                if stalled_provider == "journal_openalex":
                    return httpx.Response(200, stream=stalled_stream)
                return httpx.Response(200, json={"id": "https://openalex.org/W123"})
            if request.url.path.endswith("/W123"):
                return httpx.Response(200, json={"referenced_works": []})
        if host == "arxiv.org":
            assert request.url.path == "/html/2205.14135"
            return httpx.Response(200, content=bibliography)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        # The scaled deadlines allow two paced OpenAlex requests in the healthy case.
        monkeypatch.setattr(papers, "REFERENCE_PROVIDER_SECONDS", 0.5)
        monkeypatch.setattr(papers, "PRIMARY_REFERENCE_SECONDS", 0.7)
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["ARXIV:2205.14135", "DOI:10.1000/journal"],
                    "query": "memory efficient exact attention",
                    "direction": "backward",
                },
            )
            body = json.loads(result.content[0].text)
            assert not result.isError
            assert body["papers"][0]["id"] == cached["id"]
            assert body["edges"] == [[arxiv_seed["id"], cached["id"]]]
            assert body["edge_evidence"] == [
                {
                    "edge": [arxiv_seed["id"], cached["id"]],
                    "matched_id": "DOI:10.1000/cached",
                    "source": "primary_arxiv",
                    "url": "https://arxiv.org/html/2205.14135#bib.cached",
                }
            ]

            errors = body["errors"]
            expected_provider = (
                "Semantic Scholar" if stalled_provider == "semantic_scholar" else "OpenAlex"
            )
            expected_detail = (
                "TimeoutError" if stalled_provider == "semantic_scholar" else "timed out"
            )
            assert any(
                error.get("seed") == journal_seed["id"]
                and expected_provider in error["error"]
                and expected_detail in error["error"]
                for error in errors
            ), errors
            assert stalled_stream.cancelled.is_set()
            assert web.pending == {} and web.waiters == {}
            assert (
                sum(
                    host == "api.semanticscholar.org" and path.endswith("/paper/batch")
                    for host, path in requests
                )
                == 1
            )
            request_count = len(requests)
            await asyncio.sleep(0.05)
            assert len(requests) == request_count

    run(exercise())


@pytest.mark.parametrize("stalled", ["html", "metadata"])
def test_primary_reference_deadline_cleans_requests_and_keeps_cached_results(
    tmp_path, monkeypatch, stalled
):
    seed = s2(
        "seed",
        "Graph routing seed",
        2025,
        20,
        "Graph routing seed evidence.",
        arxiv="2501.00001",
    )
    cached = s2(
        "cached",
        "Graph routing cached reference",
        2023,
        4,
        "Graph routing cached evidence.",
        arxiv="2301.00001",
    )
    save_papers(tmp_path, seed, cached)
    stream = StallingStream(arxiv_feed())
    requests = []
    bibliography = b"""<html><body><ol class="ltx_bibliography">
      <li class="ltx_bibitem" id="bib.cached">
        <a href="https://arxiv.org/abs/2301.00001">Cached reference</a>
      </li>
      <li class="ltx_bibitem" id="bib.uncached">
        <a href="https://arxiv.org/abs/2401.00001">Uncached reference</a>
      </li>
    </ol></body></html>"""

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host in ("api.semanticscholar.org", "api.openalex.org"):
            return httpx.Response(429)
        if host == "arxiv.org":
            return (
                httpx.Response(200, stream=stream)
                if stalled == "html"
                else httpx.Response(200, content=bibliography)
            )
        if host == "export.arxiv.org":
            assert stalled == "metadata"
            return httpx.Response(200, stream=stream)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        monkeypatch.setattr(papers, "REFERENCE_PROVIDER_SECONDS", 0.05)
        monkeypatch.setattr(papers, "PRIMARY_REFERENCE_SECONDS", 0.05)
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["ARXIV:2501.00001"],
                    "query": "graph routing",
                    "direction": "backward",
                },
            )
            assert stream.cancelled.is_set()
            assert web.pending == {} and web.waiters == {}
            request_count = len(requests)
            await asyncio.sleep(0.1)
            assert len(requests) == request_count

            if stalled == "html":
                assert result.isError
                assert "No citation neighbors found" in result.content[0].text
            else:
                body = json.loads(result.content[0].text)
                assert not result.isError
                assert body["papers"][0]["id"] == cached["id"]
                assert body["seeds"][0]["reference_coverage"] == {
                    "source": "primary_arxiv",
                    "returned": 1,
                    "total": 2,
                    "inspected": 2,
                    "identified": 2,
                    "unidentified": 0,
                    "ambiguous": 0,
                    "unresolved": 1,
                    "truncated": 0,
                    "metadata_truncated": 0,
                }
                assert any("arXiv metadata" in error["error"] for error in body["errors"])

    run(exercise())


def test_shared_primary_html_survives_one_cancelled_caller(tmp_path, monkeypatch):
    seed = s2(
        "seed",
        "Graph routing seed",
        2025,
        20,
        "Graph routing seed evidence.",
        arxiv="2501.00001",
    )
    save_papers(tmp_path, seed)
    stream = StallingStream(
        b"""<html><body><li class="ltx_bibitem" id="bib.shared">
          <a href="https://arxiv.org/abs/2401.00001">Shared reference</a>
        </li></body></html>"""
    )
    requests = []

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host in ("api.semanticscholar.org", "api.openalex.org"):
            return httpx.Response(429)
        if host == "arxiv.org":
            return httpx.Response(200, stream=stream)
        if host == "export.arxiv.org":
            return httpx.Response(200, content=arxiv_feed())
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        monkeypatch.setattr(papers, "PRIMARY_REFERENCE_SECONDS", 2)
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            args = {
                "seed_ids": ["ARXIV:2501.00001"],
                "query": "graph routing",
                "direction": "backward",
            }
            first_request_id = session._request_id
            first = asyncio.create_task(session.call_tool("research_expand", args))
            await asyncio.sleep(0)
            assert session._request_id == first_request_id + 1
            second = asyncio.create_task(session.call_tool("research_expand", args))
            await asyncio.wait_for(stream.started.wait(), 1)
            for _ in range(100):
                if 2 in web.waiters.values():
                    break
                await asyncio.sleep(0.005)
            assert 2 in web.waiters.values()

            # MCP cancellation is an explicit wire notification; cancelling only
            # the local coroutine does not tell the server to stop its request.
            await session.send_notification(
                types.ClientNotification(
                    types.CancelledNotification(
                        params=types.CancelledNotificationParams(
                            requestId=first_request_id,
                            reason="caller stopped",
                        )
                    )
                )
            )
            for _ in range(100):
                if list(web.waiters.values()) == [1]:
                    break
                await asyncio.sleep(0.005)
            assert list(web.waiters.values()) == [1]
            assert not stream.cancelled.is_set()

            first.cancel()
            with suppress(asyncio.CancelledError, McpError):
                await first

            stream.release.set()
            result = await asyncio.wait_for(second, 2)
            assert not result.isError
            body = json.loads(result.content[0].text)
            assert body["edges"] == [["ARXIV:2501.00001", "ARXIV:2401.00001"]]
            assert sum(host == "arxiv.org" for host, _ in requests) == 1
            assert web.pending == {} and web.waiters == {}

    run(exercise())


def test_read_recovers_exact_crossref_evidence_and_reuses_cache(tmp_path, monkeypatch):
    doi = "10.1234/recovered-evidence"
    requests = []

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None])
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "api.crossref.org":
            return httpx.Response(200, json={"message": crossref_record(doi)})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_read", {"paper_id": "https://doi.org/" + doi, "part": "abstract"}
            )
            body = json.loads(result.content[0].text)
            assert not result.isError and body["missing"] == []
            paper = body["papers"][0]
            assert paper["id"] == "DOI:" + doi
            assert paper["url"] == "https://doi.org/" + doi
            assert paper["abstract"] == "Recovered graph routing evidence."
            assert paper["abstract_source"] == "crossref"
            request_count = len(requests)
            repeated = await session.call_tool(
                "research_read", {"paper_id": "DOI:" + doi, "part": "abstract"}
            )
            assert not repeated.isError
            assert json.loads(repeated.content[0].text)["papers"] == body["papers"]
            assert len(requests) == request_count == web.requests
            assert sum(host == "api.crossref.org" for host, _ in requests) == 1

    run(exercise())


def test_expand_hydrates_doi_node_without_changing_primary_edge_proof(tmp_path, monkeypatch):
    seed = s2("seed", "Graph routing seed", 2025, 20, "Seed evidence.", arxiv="2501.00001")
    save_papers(tmp_path, seed)
    doi = "10.1234/bibliography-evidence"
    requests = []

    async def respond(request):
        host = original_host(request)
        requests.append((host, request.url.path))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None])
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "arxiv.org":
            return httpx.Response(
                200,
                content=f"""<html><body><li class="ltx_bibitem" id="bib.exact-doi">
                  <a href="https://doi.org/{doi}">Reference</a>
                </li></body></html>""".encode(),
            )
        if host == "api.crossref.org":
            return httpx.Response(200, json={"message": crossref_record(doi)})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            expanded = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": ["ARXIV:2501.00001"],
                    "query": "graph routing evidence",
                    "direction": "backward",
                },
            )
            body = json.loads(expanded.content[0].text)
            assert not expanded.isError
            assert body["papers"][0]["id"] == "DOI:" + doi
            assert body["edges"] == [[seed["id"], "DOI:" + doi]]
            assert body["edge_evidence"] == [
                {
                    "edge": [seed["id"], "DOI:" + doi],
                    "matched_id": "DOI:" + doi,
                    "source": "primary_arxiv",
                    "url": "https://arxiv.org/html/2501.00001#bib.exact-doi",
                }
            ]
            assert body["seeds"][0]["reference_coverage"]["unresolved"] == 0
            request_count = len(requests)
            page = await session.call_tool("research_graph", {"graph_id": body["graph_id"]})
            assert not page.isError
            assert json.loads(page.content[0].text)["edge_evidence"] == body["edge_evidence"]
            read = await session.call_tool(
                "research_read", {"paper_id": "DOI:" + doi, "part": "abstract"}
            )
            assert not read.isError
            read_body = json.loads(read.content[0].text)
            assert read_body["papers"][0]["abstract_source"] == "crossref"
            assert len(requests) == request_count == web.requests

    run(exercise())


def test_read_does_not_turn_crossref_math_markup_into_a_quote(tmp_path, monkeypatch):
    doi = "10.1234/formula-evidence"

    async def respond(request):
        host = original_host(request)
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None])
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "api.crossref.org":
            record = crossref_record(
                doi, abstract="Evidence uses <mml:math><mml:mi>x</mml:mi></mml:math>."
            )
            return httpx.Response(200, json={"message": record})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        async with connected_session(tmp_path, monkeypatch, respond) as (session, _):
            result = await session.call_tool(
                "research_read", {"paper_id": "DOI:" + doi, "part": "abstract"}
            )
            body = json.loads(result.content[0].text)
            assert not result.isError and body["missing"] == []
            assert body["papers"][0]["id"] == "DOI:" + doi
            assert body["papers"][0]["abstract"] is None
            assert body["papers"][0]["abstract_source"] is None

    run(exercise())


def test_expand_retains_completed_doi_page_when_next_page_times_out(tmp_path, monkeypatch):
    seed = s2("seed", "Graph routing seed", 2025, 20, "Seed evidence.", arxiv="2501.00001")
    save_papers(tmp_path, seed)
    dois = [f"10.1234/reference-{index}" for index in range(21)]
    stream = StallingStream()
    bibliography = "".join(
        f'<li class="ltx_bibitem" id="bib.{index}">'
        f'<a href="https://doi.org/{doi}">Reference</a></li>'
        for index, doi in enumerate(dois)
    ).encode()

    async def respond(request):
        host = original_host(request)
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[None] * len(json.loads(request.content)["ids"]))
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "arxiv.org":
            return httpx.Response(200, content=bibliography)
        if host == "api.crossref.org":
            if request.url.path == "/works":
                rows = []
                for index, doi in enumerate(dois[:20]):
                    record = crossref_record(doi)
                    record["title"] = [f"Graph routing prior evidence {index}"]
                    rows.append(record)
                return httpx.Response(200, json={"message": {"items": rows}})
            return httpx.Response(200, stream=stream)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        monkeypatch.setattr(papers, "PRIMARY_REFERENCE_SECONDS", 1.4)
        async with connected_session(tmp_path, monkeypatch, respond) as (session, web):
            result = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": [seed["id"]],
                    "query": "graph routing evidence",
                    "direction": "backward",
                    "limit": 60,
                },
                # SDK delivery and CI scheduling are outside the 1.4s metadata deadline.
                read_timeout_seconds=timedelta(seconds=5),
            )
            body = json.loads(result.content[0].text)
            assert not result.isError
            assert {paper["id"] for paper in body["papers"]} == {"DOI:" + doi for doi in dois[:20]}
            coverage = body["seeds"][0]["reference_coverage"]
            assert coverage["returned"] == 20 and coverage["unresolved"] == 1
            assert len(body["edge_evidence"]) == 20
            assert all(proof["source"] == "primary_arxiv" for proof in body["edge_evidence"])
            assert all("#bib." in proof["url"] for proof in body["edge_evidence"])
            assert any("timed out" in error["error"] for error in body["errors"])
            assert stream.started.is_set() and stream.cancelled.is_set()
            assert not web.pending and not web.waiters
            request_count = web.requests
            cached = await session.call_tool(
                "research_read", {"paper_id": "DOI:" + dois[0], "part": "abstract"}
            )
            assert not cached.isError
            assert json.loads(cached.content[0].text)["papers"][0]["abstract_source"] == "crossref"
            assert web.requests == request_count

    run(exercise())
