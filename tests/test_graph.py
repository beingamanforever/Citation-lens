import json

import pytest
from conftest import run, s2

from citation_lens.graph import Graphs, interleave, percentile, snippet, stems, text_score

QUERY = "memory efficient exact attention"


class FakePapers:
    """Provider double: two seeds, shared foundations, prominent and recent citing papers."""

    def __init__(self, store):
        self.store = store
        self.seed_a = s2(
            "a1",
            "Fast Exact Attention",
            2022,
            3000,
            "We propose an exact attention algorithm.",
            arxiv="2205.14135",
        )
        self.seed_b = s2(
            "b1",
            "Memory Efficient Self-Attention",
            2021,
            200,
            "Attention needs little memory.",
            arxiv="2112.05682",
        )
        self.foundation = s2(
            "f1",
            "Online Softmax Normalizer For Attention",
            2018,
            300,
            "An exact online softmax for attention.",
            refs=(),
        )
        self.off_topic_ref = s2("f2", "Protein Folding With Graphs", 2019, 900, "Proteins fold.")
        self.follow_up = s2(
            "c1",
            "Exact Attention Parallelism On GPUs",
            2023,
            800,
            "Better work partitioning for exact attention.",
            refs=("a1", "b1", "f1"),
        )
        self.generic = s2(
            "c2",
            "A General Purpose Language Model Report",
            2023,
            9000,
            "We train a big model.",
            refs=("a1", "x1", "x2", "x3", "x4", "x5"),
        )
        self.prominent = s2(
            "p1",
            "Exact Attention Kernels With Asynchrony",
            2024,
            700,
            "Exact attention on new GPUs.",
            refs=("a1", "b1"),
            date="2024-07-11",
        )
        self.recent = s2(
            "r1",
            "Distributed Exact Attention Across Devices",
            None,
            3,
            "Exact attention with ring communication saves memory.",
            refs=("a1", "b1", "f1"),
            date="2026-05-01",
        )

    async def resolve_many(self, ids, seconds=None):
        return [{"ARXIV:2205.14135": self.seed_a, "ARXIV:2112.05682": self.seed_b}[i] for i in ids]

    async def references(self, seeds):
        return [
            [self.foundation, self.off_topic_ref, self.seed_b]
            if seed is self.seed_a
            else [self.foundation]
            for seed in seeds
        ]

    async def citations(self, seed, since):
        if seed is self.seed_a:
            return [self.follow_up, self.generic, self.recent], []
        return [self.follow_up, self.recent], ["openalex: HTTP 429; retry later"]

    async def similar(self, seed):
        return [self.prominent] if seed is self.seed_b else []

    async def with_references(self, papers):
        return papers

    async def search(self, query, provider, limit, since=None, newest=False):
        if provider == "semantic_scholar":
            return [self.seed_a, self.seed_b], 2
        if provider == "openalex":
            raise RuntimeError("api.openalex.org: daily budget exhausted; set its API key")
        return ([self.recent], 1) if newest else ([self.seed_a], 1)


def test_text_score_and_snippet_are_query_biased_and_verbatim():
    query = [stems(QUERY)]
    assert text_score({"title": "Memory efficient exact attention", "abstract": ""}, query) == 1
    assert text_score({"title": "Other", "abstract": QUERY}, query) == 0.5
    abstract = "Transformers are popular. We make exact attention use less memory. It is fast."
    assert snippet(abstract, stems(QUERY)) == "We make exact attention use less memory."
    assert snippet("x " * 300, stems("x")).endswith("x") and len(snippet("x " * 300, set())) <= 220


def test_percentile_ties_share_the_lowest_rank_and_interleave_deduplicates():
    assert percentile([0, 0, 0, 5]) == [0, 0, 0, 1]
    assert interleave([["a", "b"], ["a", "c"], ["d"]]) == {"a": 0, "d": 2, "b": 0, "c": 1}


def test_expand_ranks_lanes_like_connected_papers(store):
    fake = FakePapers(store)
    result = run(Graphs(fake).expand(["ARXIV:2205.14135", "ARXIV:2112.05682"], QUERY, "both", 10))
    cards = {card["id"]: card for card in result["papers"]}
    assert cards[fake.foundation["id"]]["lane"] == "foundation"
    assert cards[fake.foundation["id"]]["why"].startswith("cited by 2/2 seeds")
    assert cards[fake.follow_up["id"]]["lane"] == "follow-up"
    assert cards[fake.recent["id"]]["lane"] == "recent"
    assert fake.off_topic_ref["id"] not in cards and fake.generic["id"] not in cards
    assert result["papers"][0]["id"] == fake.foundation["id"]
    # Edges always point citing -> cited, including candidate-to-candidate references.
    edges = {tuple(edge) for edge in result["edges"]}
    assert ("ARXIV:2205.14135", fake.foundation["id"]) in edges
    assert ("ARXIV:2205.14135", "ARXIV:2112.05682") in edges  # seed -> seed, on page one
    assert (fake.recent["id"], "ARXIV:2112.05682") in edges
    assert (fake.follow_up["id"], fake.foundation["id"]) in edges
    assert result["errors"][0]["error"].startswith("openalex: HTTP 429")
    # A recommendation reached only through similarity still gets its citation edges.
    assert cards[fake.prominent["id"]]["lane"] == "follow-up"
    assert "similar to 1/2 seeds" in cards[fake.prominent["id"]]["why"]
    assert (fake.prominent["id"], "ARXIV:2205.14135") in edges
    assert result["seeds"][0] == {
        "id": "ARXIV:2205.14135",
        "cites": 3000,
        "references": 3,
        "citations": 3,
        "similar": 0,
    }
    assert set(result["papers"][0]) <= {
        "id",
        "title",
        "year",
        "cites",
        "url",
        "lane",
        "why",
        "snippet",
    }


def test_graph_pages_show_each_edge_once(store):
    fake = FakePapers(store)
    graphs = Graphs(fake)
    first = run(graphs.expand(["ARXIV:2205.14135", "ARXIV:2112.05682"], QUERY, "both", 1))
    pages, edges, offset = [first], list(first["edges"]), first["next_offset"]
    while offset is not None:
        page = graphs.view(first["graph_id"], offset, 1)
        pages.append(page)
        edges += page["edges"]
        offset = page["next_offset"]
    assert len(edges) == len({tuple(edge) for edge in edges})
    assert not any("edges_omitted" in page for page in pages)  # small graphs show every edge
    assert sum(len(page["papers"]) for page in pages) == first["total"]
    with pytest.raises(ValueError):
        graphs.view("missing")
    assert len(graphs.view(first["graph_id"], 0, 500)["papers"]) == first["total"]  # clamped


def test_search_fuses_providers_keeps_failures_and_shows_recent_work(store):
    fake = FakePapers(store)
    result = run(
        Graphs(fake).search(
            [QUERY, "IO-aware attention"], ["semantic_scholar", "openalex", "arxiv"], 3
        )
    )
    ids = [card["id"] for card in result["papers"]]
    assert ids[0] == "ARXIV:2205.14135" and len(ids) == len(set(ids)) == 3
    assert fake.recent["id"] in ids
    assert "matched 3/6 searches" in result["papers"][0]["why"]
    failures = [s for s in result["searches"] if "error" in s]
    assert len(failures) == 2 and "budget exhausted" in failures[0]["error"]
    assert len(json.dumps(result)) < 3000


def test_search_empty_success_returns_a_page_with_persisted_provider_coverage(store):
    fake = FakePapers(store)

    async def empty_or_failed(query, provider, limit, since=None, newest=False):
        if provider == "semantic_scholar":
            return [], 0
        raise RuntimeError("provider unavailable")

    fake.search = empty_or_failed
    graphs = Graphs(fake)
    result = run(graphs.search([QUERY], ["semantic_scholar", "openalex"]))
    saved = graphs.view(result["graph_id"])

    assert result["papers"] == [] and result["total"] == 0
    assert saved["searches"] == result["searches"]
    assert saved["searches"] == [
        {"query": QUERY, "provider": "semantic_scholar", "returned": 0, "total": 0},
        {"query": QUERY, "provider": "openalex", "error": "provider unavailable"},
    ]

    async def all_failed(query, provider, limit, since=None, newest=False):
        raise RuntimeError(f"{provider} unavailable")

    fake.search = all_failed
    with pytest.raises(RuntimeError, match="Every search failed"):
        run(graphs.search([QUERY], ["semantic_scholar", "openalex"]))


def test_failed_lookups_become_errors_not_exceptions(store):
    fake = FakePapers(store)

    async def throttled(seeds):
        raise RuntimeError("api.semanticscholar.org: HTTP 429; retry later")

    fake.references = throttled
    graphs = Graphs(fake)
    result = run(graphs.expand(["ARXIV:2205.14135", "ARXIV:2112.05682"], QUERY))
    failed = [e for e in result["errors"] if e.get("lookup") == "references"]
    assert len(failed) == 2 and "429" in failed[0]["error"]
    assert result["papers"]  # citing papers still arrive
    saved = graphs.view(result["graph_id"])
    assert saved["errors"] == result["errors"]
    assert saved["seeds"] == result["seeds"]


def test_dense_pages_keep_the_best_ranked_edges(store):
    graphs = Graphs(FakePapers(store))
    nodes = {f"S2:{i}": s2(str(i), f"Paper number {i} on attention") for i in range(40)}
    order = list(nodes)
    edges = sorted({(a, b) for a in order for b in order if a < b})  # 780 links
    graph = {
        "terms": [],
        "seeds": ["S2:seed"],
        "order": order,
        "notes": {},
        "nodes": nodes,
        "edges": edges,
    }
    page = graphs.view(graphs._save(graph), 0, 40)
    assert len(page["edges"]) == 120 and page["edges_omitted"] == 660
    ranks = {pid: i for i, pid in enumerate(order)}
    worst_kept = max(min(ranks[a], ranks[b]) for a, b in page["edges"])
    assert worst_kept < 10  # kept links touch the top-ranked cards
