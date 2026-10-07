"""Reproducible synthetic measurements, never real-network or research-quality claims."""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from citation_lens import server  # noqa: E402
from citation_lens.documents import Documents  # noqa: E402
from citation_lens.graph import Graphs, card  # noqa: E402
from citation_lens.network import Web  # noqa: E402
from citation_lens.papers import Papers  # noqa: E402
from citation_lens.storage import Store  # noqa: E402

TOKENIZER = None


def token_count(text):
    return len(TOKENIZER.encode(text, disallowed_special=())) if TOKENIZER else None


def size(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


async def batch_discovery(directory, scenario):
    """Pair actual search entrypoints over identical normalized provider fixtures."""
    queries, providers = ["exact attention", "attention GPU"], ["openalex", "semantic_scholar"]
    identities = {}
    for job, (query, provider) in enumerate((q, p) for q in queries for p in providers):
        identities[(query, provider)] = [
            {
                "id": f"OA:W{job * 10 + i + 1}",
                "provider": provider,
                "title": f"Synthetic attention method {i}",
                "year": 2026,
                "doi": f"10.1000/{i if scenario == 'duplicate-heavy' else job * 10 + i}",
                "arxiv": "",
                "citations": 0,
                "abstract": "Synthetic evidence. " * 50,
                "url": f"https://example.org/paper/{job * 10 + i}",
                "pdf": None,
            }
            for i in range(10)
        ]

    class FixturePapers(Papers):
        async def search(self, query, provider, limit, recent, year):
            host = "api.openalex.org" if provider == "openalex" else "api.semanticscholar.org"
            data = await self.web.json(f"https://{host}/fixture/{queries.index(query)}")
            return [self.remember(p) for p in data["papers"][:limit]], len(data["papers"])

    async def fixture(request):
        await asyncio.sleep(0.08)
        query = queries[int(request.url.path.rsplit("/", 1)[-1])]
        provider = "openalex" if request.url.host == "api.openalex.org" else "semantic_scholar"
        if scenario == "partial-outage" and query == queries[1] and provider == providers[1]:
            return httpx.Response(403)
        return httpx.Response(200, json={"papers": identities[(query, provider)]})

    results = {}
    for mode in ("scalar", "batch"):
        store = Store(directory / f"{scenario}-{mode}.sqlite")
        web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(fixture)))
        papers = FixturePapers(web)
        previous = {name: getattr(server, name, None) for name in ("papers", "graphs")}
        server.papers, server.graphs = papers, Graphs(papers)
        outputs, errors, cards = [], [], []
        start = time.perf_counter()
        try:
            if mode == "scalar":
                for query in queries:
                    for provider in providers:
                        try:
                            value = await server.research_search(query, provider, limit=10)
                            outputs.append(value)
                            cards.extend(json.loads(value)["papers"])
                        except RuntimeError as error:
                            errors.append(
                                {"query": query, "provider": provider, "error": str(error)}
                            )
                            outputs.append(json.dumps(errors[-1], separators=(",", ":")))
            else:
                value = await server.research_search(queries, providers, limit=10)
                outputs.append(value)
                page = json.loads(value)
                errors = [s for s in page["searches"] if s.get("error")]
                cards.extend(page["papers"])
                while page["next_offset"] is not None:
                    value = await server.research_graph(
                        page["graph_id"], offset=page["next_offset"]
                    )
                    outputs.append(value)
                    page = json.loads(value)
                    cards.extend(page["papers"])
            results[mode] = {
                "initial_tool_calls": 4 if mode == "scalar" else 1,
                "complete_tool_calls": len(outputs),
                "http_requests": web.requests,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "initial_payload_bytes": sum(len(v.encode()) for v in outputs)
                if mode == "scalar"
                else len(outputs[0].encode()),
                "complete_payload_bytes": sum(len(v.encode()) for v in outputs),
                "initial_payload_tokens": sum(token_count(v) for v in outputs)
                if TOKENIZER and mode == "scalar"
                else token_count(outputs[0]),
                "complete_payload_tokens": sum(token_count(v) for v in outputs)
                if TOKENIZER
                else None,
                "returned_cards": len(cards),
                "canonical_dois": sorted({p["doi"] for p in cards}),
                "failed_searches": len(errors),
            }
        finally:
            for name, value in previous.items():
                if value is None:
                    delattr(server, name)
                else:
                    setattr(server, name, value)
            await web.close()
            store.close()
    assert results["scalar"]["canonical_dois"] == results["batch"]["canonical_dois"]
    assert results["scalar"]["http_requests"] == results["batch"]["http_requests"]
    return {
        "scenario": scenario,
        "search_attempts": 4,
        "limit_per_search": 10,
        **results,
        "same_retained_identities": True,
        "definition": "Actual Python search replies, without SDK framing or host inference. "
        "Scalar initial payload = all four search replies; batch initial = first ten cards. "
        "Complete payload includes every paged card and all reported failures. "
        "Synthetic 80ms responses and production per-host pacing, including 1.05s for S2.",
    }


async def paged_read(directory):
    store = Store(directory / "reads.sqlite")
    web = Web(store)
    papers = Papers(web)
    papers.remember({"id": "OA:W1", "arxiv": "", "provider": "openalex"})
    text = "# Methods\n" + "Detailed methods and results. " * 5000
    store.save(
        "document:OA:W1",
        {
            "paper_id": "OA:W1",
            "source_url": "https://public.example/paper",
            "format": "html",
            "warnings": [],
            "fallback_errors": [],
            "text": text,
            "figures": [
                {
                    "index": i,
                    "url": f"https://public.example/figure{i}.png",
                    "caption": "Caption details. " * 100,
                }
                for i in range(12)
            ],
        },
    )
    documents, offset, parts, outputs = Documents(papers), 0, [], []
    try:
        while True:
            part = await documents.read("OA:W1", offset=offset, max_chars=12000)
            outputs.append(part)
            parts.append(part["text"])
            offset = part["next_offset"]
            if offset is None:
                break
        assert "".join(parts) == text
        first = outputs[0]
        previous = [
            {k: v for k, v in part.items() if k != "overview_included"}
            | {"outline": first["outline"], "figures": first["figures"]}
            for part in outputs
        ]
        from citation_lens.graph import payload

        before = sum(payload(part)["payload_bytes"] for part in previous)
        after = sum(part["payload_bytes"] for part in outputs)

        def serialize(part):
            return json.dumps(part, ensure_ascii=False, separators=(",", ":"))

        return {
            "pages": len(outputs),
            "text_chars": len(text),
            "identical_complete_text": True,
            "repeated_overview_bytes": before,
            "overview_once_bytes": after,
            "repeated_overview_tokens": sum(token_count(serialize(part)) for part in previous)
            if TOKENIZER
            else None,
            "overview_once_tokens": sum(token_count(serialize(part)) for part in outputs)
            if TOKENIZER
            else None,
            "reduction_percent": round(100 * (1 - after / before), 2),
            "definition": "Same text, sources, warnings and figures. Baseline reconstructs "
            "the previous reply contract that repeated the first overview on every text page. "
            "Updated replies include overview on offset=0 or outline_only; all text is retained.",
        }
    finally:
        await web.close()
        store.close()


async def discovery(store):
    """Same two search hits, with and without citation expansion on a toy corpus."""
    titles = [
        "Fixture: Tiled exact attention",
        "Fixture: Efficient attention survey",
        "Fixture: Transformer foundations",
        "Fixture: Decode-aware attention",
        "Fixture: Fresh attention kernel",
        "Fixture: Shared cache methods",
    ]
    records = {
        f"OA:W{i}": {
            "id": f"OA:W{i}",
            "title": title,
            "year": 2026 if i == 5 else 2020,
            "citations": 0 if i == 5 else 100,
            "provider": "openalex",
            "abstract": "Synthetic attention metadata for a deterministic workflow comparison.",
            "url": f"https://example.org/fixture/{i}",
            "doi": "",
            "arxiv": "",
        }
        for i, title in enumerate(titles, 1)
    }
    links = {
        "OA:W1": ["OA:W3", "OA:W6"],
        "OA:W2": ["OA:W1", "OA:W3", "OA:W6"],
        "OA:W4": ["OA:W1"],
        "OA:W5": ["OA:W1"],
        "OA:W3": [],
        "OA:W6": [],
    }

    class Fixture:
        def __init__(self):
            self.store = store
            self.calls = 0

        async def resolve(self, pid):
            return records[pid]

        async def neighbors(self, paper, direction, limit):
            self.calls += 1
            ids = (
                links[paper["id"]]
                if direction == "backward"
                else [pid for pid, refs in links.items() if paper["id"] in refs]
            )
            return [records[pid] for pid in ids], {"sampled": False, "indexed": len(ids)}

    seeds = ["OA:W1", "OA:W2"]
    provider = Fixture()
    graph = await Graphs(provider).expand(seeds, "efficient attention", depth=1)
    return {
        "kind": "deterministic synthetic citation fixture, not an LLM or live search evaluation",
        "query": "efficient attention",
        "search_only": {"papers": [card(records[pid]) for pid in seeds], "edges": []},
        "with_citation_lens": {
            "papers": graph["papers"],
            "edges": graph["edges"],
            "node_count": graph["node_count"],
            "edge_count": graph["edge_count"],
            "neighbor_calls": provider.calls,
            "graph_response_bytes": graph["payload_bytes"],
        },
        "caveat": "The two search hits and seven citation links are supplied by the fixture. "
        "Expansion executes the real Graphs code. Extra nodes do not establish relevance, "
        "recall or quality improvement against ordinary Codex/Claude research.",
    }


async def measure(directory):
    calls = 0

    async def fixture(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.08)
        return httpx.Response(200, json={"fixture": "synthetic", "id": str(request.url)})

    store = Store(directory / "bench.sqlite")
    web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(fixture)))
    urls = [f"https://api.openalex.org/works/W{i}" for i in range(8)]
    start = time.perf_counter()
    await asyncio.gather(*[web.json(url) for url in urls])
    cold = (time.perf_counter() - start) * 1000
    warm = []
    for _ in range(30):
        start = time.perf_counter()
        await asyncio.gather(*[web.json(url) for url in urls])
        warm.append((time.perf_counter() - start) * 1000)
    papers = [
        {
            "id": f"OA:W{i}",
            "title": f"Synthetic exact attention method {i}",
            "year": 2026,
            "citations": i,
            "provider": "openalex",
            "doi": "",
            "arxiv": "",
            "url": f"https://openalex.org/W{i}",
            "abstract": "An exact attention method. " * 60,
            "text": "Methods, results, equations, tables and limitations. " * 1000,
        }
        for i in range(20)
    ]
    card_bytes, dump_bytes = size([card(p) for p in papers]), size(papers)
    output = {
        "tokenizer": TOKENIZER.name if TOKENIZER else None,
        "token_definition": "Reference encoding over exact serialized payloads; excludes "
        "host prompts, inference, images and SDK framing. Not actual Codex/Claude usage. "
        "Token counts are null unless --tokens is requested with tiktoken installed.",
        "discovery": await discovery(store),
        "batch_search": [
            await batch_discovery(directory, scenario)
            for scenario in ("duplicate-heavy", "disjoint", "partial-outage")
        ],
        "paged_read": await paged_read(directory),
        "payload_and_cache": {
            "kind": "synthetic, 80ms fixture latency with production 150ms/host pacing",
            "cold_eight_requests_ms": round(cold, 3),
            "warm_p50_ms": round(statistics.median(warm), 3),
            "warm_p95_ms": round(sorted(warm)[28], 3),
            "upstream_requests": calls,
            "warm_iterations": len(warm),
            "cards_bytes": card_bytes,
            "full_dump_bytes": dump_bytes,
            "payload_reduction_percent": round(100 * (1 - card_bytes / dump_bytes), 2),
            "caveat": "Full texts are fabricated repeated prose. No real-network latency, "
            "model token counts or research quality measured.",
        },
    }
    await web.close()
    store.close()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", action="store_true", help="Count cl100k_base reference tokens")
    arguments = parser.parse_args()
    if arguments.tokens:
        import tiktoken

        TOKENIZER = tiktoken.get_encoding("cl100k_base")
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(asyncio.run(measure(Path(directory))), indent=2))
