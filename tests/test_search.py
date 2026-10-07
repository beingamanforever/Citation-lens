import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from citation_lens import server
from citation_lens.documents import Documents
from citation_lens.graph import Graphs
from citation_lens.papers import OA, OA_FIELDS
from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def run(coroutine):
    return asyncio.run(coroutine)


def paper(pid, *, title="Shared title", doi="", arxiv="", citations=1, year=2024):
    return {
        "id": pid,
        "provider": "semantic_scholar" if pid.startswith("S2:") else "openalex",
        "title": title,
        "abstract": "A paper about efficient attention.",
        "year": year,
        "date": f"{year}-01-01",
        "citations": citations,
        "doi": doi,
        "arxiv": arxiv,
        "references": [],
        "url": "https://example.org/" + pid,
        "pdf": None,
    }


class SearchPapers:
    def __init__(self, store, responses):
        self.store = store
        self.web = SimpleNamespace(requests=0, hits=0)
        self.responses = responses
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.refreshed = {}

    async def search(self, query, provider, limit, recent, year):
        self.calls.append((query, provider, limit, recent, year))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        result = self.responses[(query, provider)]
        if isinstance(result, Exception):
            raise result
        found, total = result
        return found[:limit], total

    async def resolve(self, value):
        return self.refreshed[value]

    async def neighbors(self, _paper, _direction, _limit):
        return [], {"indexed": 0, "sampled": False}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "cache.sqlite")
    yield result
    result.close()


def test_scalar_search_response_stays_compatible(store, monkeypatch):
    record = paper("OA:W1")
    papers = SearchPapers(store, {("attention", "openalex"): ([record], 4)})
    monkeypatch.setattr(server, "papers", papers, raising=False)
    monkeypatch.setattr(server, "graphs", Graphs(papers), raising=False)

    result = json.loads(run(server.research_search("attention", "openalex", 7, True, 2020)))

    assert set(result) == {
        "papers",
        "total_indexed_matches",
        "sampled",
        "provider",
        "next_action",
        "http_totals",
        "payload_bytes",
    }
    assert result["total_indexed_matches"] == 4
    assert result["sampled"]
    assert result["provider"] == "openalex"
    assert "graph_id" not in result and "search_matches" not in result["papers"][0]
    assert papers.calls == [("attention", "openalex", 7, True, 2020)]


def test_batch_schema_and_invalid_inputs_fail_before_search(store, monkeypatch):
    tool = next(tool for tool in run(server.mcp.list_tools()) if tool.name == "research_search")
    query_options = tool.inputSchema["properties"]["query"]["anyOf"]
    query_array = next(option for option in query_options if option["type"] == "array")
    query_string = next(option for option in query_options if option["type"] == "string")
    assert query_array["minItems"] == 1 and query_array["maxItems"] == 3
    assert query_array["items"]["minLength"] == query_string["minLength"] == 1
    assert query_array["items"]["maxLength"] == query_string["maxLength"] == 500
    provider_options = tool.inputSchema["properties"]["provider"]["anyOf"]
    provider_array = next(option for option in provider_options if option["type"] == "array")
    assert provider_array["minItems"] == 1 and provider_array["maxItems"] == 3
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False

    papers = SearchPapers(store, {})
    monkeypatch.setattr(server, "papers", papers, raising=False)
    monkeypatch.setattr(server, "graphs", Graphs(papers), raising=False)
    invalid = [
        ("   ", "openalex"),
        ("x" * 501, "openalex"),
        ("valid", "invalid"),
        ([], "openalex"),
        (["one", "two", "three", "four"], "openalex"),
        (["same", "same"], "openalex"),
        (["valid", "   "], "openalex"),
        (["valid", "x" * 501], "openalex"),
        (["valid"], []),
        (["valid"], ["openalex"] * 2),
        (["valid"], ["openalex", "invalid"]),
    ]
    for query, provider in invalid:
        with pytest.raises(ValueError):
            run(server.research_search(query, provider))
    assert papers.calls == []


def test_batch_search_is_concurrent_and_retains_partial_failures_and_empty_results(store):
    first = paper("OA:W1")
    second = paper("OA:W2")
    responses = {
        ("first", "openalex"): ([first], 8),
        ("first", "semantic_scholar"): RuntimeError("provider unavailable"),
        ("second", "openalex"): ([], 0),
        ("second", "semantic_scholar"): ([second], 1),
    }
    papers = SearchPapers(store, responses)
    view = run(
        Graphs(papers).search(["first", "second"], ["openalex", "semantic_scholar"], 20, True, 2020)
    )

    assert papers.max_active == 4
    assert [(item["query"], item["provider"]) for item in view["searches"]] == [
        ("first", "openalex"),
        ("first", "semantic_scholar"),
        ("second", "openalex"),
        ("second", "semantic_scholar"),
    ]
    failed = view["searches"][1]
    assert failed["returned"] is None and failed["total_indexed_matches"] is None
    assert failed["sampled"] is None and failed["error"] == "provider unavailable"
    empty = view["searches"][2]
    assert empty["returned"] == empty["total_indexed_matches"] == 0
    assert empty["sampled"] is False and empty["error"] is None
    assert view["query"] == "first" and view["node_count"] == 2
    assert view["edge_count"] == 0 and view["edges"] == []


def test_all_failed_searches_raise_bounded_error_but_all_empty_is_valid(store):
    failures = {
        ("first", "openalex"): RuntimeError("a" * 400),
        ("second", "openalex"): RuntimeError("b" * 400),
    }
    with pytest.raises(RuntimeError) as caught:
        run(Graphs(SearchPapers(store, failures)).search(["first", "second"], ["openalex"], 10))
    assert str(caught.value).startswith("All searches failed:")
    assert len(str(caught.value)) <= 520

    empty = SearchPapers(store, {("empty", "openalex"): ([], 0)})
    view = run(Graphs(empty).search(["empty"], ["openalex"], 10))
    assert view["node_count"] == 0 and view["searches"][0]["error"] is None


def test_batch_deduplicates_transitive_aliases_and_keeps_first_readable_record(store):
    representative = paper("OA:W1", doi="10.1000/shared", citations=11)
    same_title = paper("OA:W4", citations=44)
    alternate = paper("S2:" + "b" * 40, arxiv="2610.00001", citations=22)
    bridge = paper("OA:W3", doi="10.1000/shared", arxiv="2610.00001v2", citations=999)
    papers = SearchPapers(
        store,
        {
            ("one", "openalex"): ([representative, same_title], 2),
            ("two", "openalex"): ([alternate], 1),
            ("three", "openalex"): ([bridge], 1),
        },
    )
    view = run(Graphs(papers).search(["one", "two", "three"], ["openalex"], 20))
    graph = store.load("graph:" + view["graph_id"])

    assert graph["nodes"].keys() == {"S2:" + "b" * 40, "OA:W4"}
    merged = graph["nodes"]["S2:" + "b" * 40]
    assert merged["citations"] == 22 and merged["provider"] == "semantic_scholar"
    assert merged["search_matches"] == [0, 1, 2]
    assert set(merged["aliases"]) >= {
        "OA:W1",
        "OA:W3",
        "S2:" + "b" * 40,
        "DOI:10.1000/shared",
        "ARXIV:2610.00001",
    }
    assert graph["nodes"]["OA:W4"]["search_matches"] == [0]
    card = next(item for item in view["papers"] if item["id"] == "S2:" + "b" * 40)
    assert card["search_matches"] == [0, 1, 2]


def test_batch_selected_read_uses_identity_equivalent_full_text_record(store, monkeypatch):
    doi = "10.1000/readable"
    unreadable = paper("OA:W1", doi=doi, citations=90)
    readable = paper("S2:" + "c" * 40, doi=doi, citations=7)
    readable["pdf"] = "https://example.org/readable.pdf"
    papers = SearchPapers(
        store,
        {
            ("attention", "openalex"): ([unreadable], 1),
            ("attention", "semantic_scholar"): ([readable], 1),
        },
    )
    papers.refreshed[readable["id"]] = readable
    store.save(
        "document:" + readable["id"],
        {
            "paper_id": readable["id"],
            "source_url": readable["pdf"],
            "format": "pdf",
            "warnings": [],
            "text": "# Fixture\nReadable identity-equivalent full text.",
            "figures": [],
        },
    )
    monkeypatch.setattr(server, "papers", papers, raising=False)
    monkeypatch.setattr(server, "graphs", Graphs(papers), raising=False)

    batch = json.loads(
        run(server.research_search(["attention"], ["openalex", "semantic_scholar"], limit=10))
    )
    chosen = batch["papers"][0]
    assert chosen["id"] == readable["id"]
    assert chosen["provider"] == "semantic_scholar" and chosen["citations"] == 7
    assert chosen["full_text_available"]
    result = run(Documents(papers).read(chosen["id"]))
    assert "Readable identity-equivalent full text" in result["text"]


def test_search_snapshot_pages_after_restart_without_network(tmp_path):
    path = tmp_path / "cache.sqlite"
    store = Store(path)
    first = [paper(f"OA:W{i}", year=2024) for i in range(1, 21)]
    second = [paper(f"OA:W{i}", year=2023) for i in range(21, 41)]
    papers = SearchPapers(
        store,
        {("first", "openalex"): (first, 20), ("second", "openalex"): (second, 20)},
    )
    initial = run(Graphs(papers).search(["first", "second"], ["openalex"], 20))
    graph_id = initial["graph_id"]
    assert len(initial["papers"]) == 10 and initial["next_offset"] == 10
    store.close()

    reopened = Store(path)
    offline = SearchPapers(reopened, {})
    page = Graphs(offline).view(graph_id, offset=20, limit=20)
    assert len(page["papers"]) == 20 and page["next_offset"] is None
    assert offline.calls == [] and page["searches"] == initial["searches"]
    reopened.close()


def test_expansion_refresh_preserves_search_aliases_and_provenance(store):
    representative = paper("OA:W1", doi="10.1000/shared", citations=1)
    alternate = paper("S2:" + "a" * 40, doi="10.1000/shared", citations=100)
    papers = SearchPapers(
        store,
        {("first", "openalex"): ([representative], 1), ("second", "openalex"): ([alternate], 1)},
    )
    view = run(Graphs(papers).search(["first", "second"], ["openalex"], 10))
    papers.refreshed["OA:W1"] = {**representative, "citations": 2}
    run(Graphs(papers).expand(["OA:W1"], "first", view["graph_id"], direction="backward"))
    refreshed = store.load("graph:" + view["graph_id"])["nodes"]["OA:W1"]

    assert refreshed["citations"] == 2
    assert refreshed["search_matches"] == [0, 1]
    assert "S2:" + "a" * 40 in refreshed["aliases"]


def http_cache_key(url):
    identity = json.dumps([url, None], sort_keys=True).encode()
    return "http:" + hashlib.sha256(identity).hexdigest()


def openalex_raw(index, query):
    return {
        "id": f"https://openalex.org/W{index}",
        "title": f"{query} attention paper {index}",
        "publication_year": 2026 - index % 3,
        "publication_date": f"{2026 - index % 3}-01-01",
        "cited_by_count": index,
        "referenced_works": [],
    }


def test_sdk_batch_search_page_expand_and_cached_read(tmp_path):
    directory = tmp_path / "data"
    store = Store(directory / "cache.sqlite")
    for query, start in (("first", 1), ("second", 7)):
        params = {"search": query, "select": OA_FIELDS, "per_page": 6}
        url = OA + "?" + urlencode(params)
        rows = [openalex_raw(index, query) for index in range(start, start + 6)]
        store.put(http_cache_key(url), json.dumps({"results": rows, "meta": {"count": 6}}).encode())
        for row in rows:
            pid = "OA:" + row["id"].rsplit("/", 1)[-1]
            store.save(
                "document:" + pid,
                {
                    "paper_id": pid,
                    "source_url": row["id"],
                    "format": "html",
                    "warnings": [],
                    "text": "# Fixture\nCached full-text evidence.",
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
            await session.initialize()
            searched = await session.call_tool(
                "research_search",
                {"query": ["first", "second"], "provider": ["openalex"], "limit": 6},
            )
            assert not searched.isError
            batch = json.loads(searched.content[0].text)
            assert batch["node_count"] == 12 and batch["next_offset"] == 10
            assert batch["http_totals"] == {"requests": 0, "cache_hits": 2}

            paged = await session.call_tool(
                "research_graph", {"graph_id": batch["graph_id"], "offset": 10}
            )
            page = json.loads(paged.content[0].text)
            assert not paged.isError and len(page["papers"]) == 2
            assert page["http_totals"]["requests"] == 0

            selected = batch["papers"][0]["id"]
            expanded = await session.call_tool(
                "research_expand",
                {
                    "seed_ids": [selected],
                    "query": "first",
                    "graph_id": batch["graph_id"],
                    "direction": "backward",
                },
            )
            assert not expanded.isError
            expanded_payload = json.loads(expanded.content[0].text)
            selected_card = next(p for p in expanded_payload["papers"] if p["id"] == selected)
            assert selected_card["search_matches"]
            assert expanded_payload["http_totals"]["requests"] == 0

            read = await session.call_tool("research_read", {"paper_id": selected})
            assert not read.isError and "Cached full-text evidence" in read.content[0].text
            assert json.loads(read.content[0].text)["http_totals"]["requests"] == 0

    run(exercise())
