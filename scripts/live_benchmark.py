"""Live search-only vs citation expansion; paired card/full-text bytes on readable papers."""

import asyncio
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from citation_lens.documents import Documents  # noqa: E402
from citation_lens.graph import Graphs, card  # noqa: E402
from citation_lens.network import Web  # noqa: E402
from citation_lens.papers import Papers  # noqa: E402
from citation_lens.storage import Store  # noqa: E402


def size(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


async def measure(directory):
    query = "FlashAttention"
    store = Store(directory / "cache.sqlite")
    web = Web(store)
    papers = Papers(web)
    result = {
        "kind": "live provider workflow, not a paired Codex/Claude reasoning evaluation",
        "recorded_at": datetime.now(UTC).isoformat(),
        "query": query,
        "provider": "openalex",
        "errors": [],
        "full_text_attempts": [],
    }
    try:
        start = time.perf_counter()
        found, total = await papers.search(query, "openalex", 5, False, None)
        result["search_only"] = {
            "papers": [card(p) for p in found],
            "total_indexed_matches": total,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
            "http_requests": web.requests,
        }
        graphs = Graphs(papers)
        view = await graphs.expand(
            [p["id"] for p in found[:3]], query, depth=1, neighbors=6, max_nodes=30
        )
        graph = store.load("graph:" + view["graph_id"])
        result["with_citation_lens"] = {
            "node_count": view["node_count"],
            "edge_count": view["edge_count"],
            "elapsed_ms": view["elapsed_ms"],
            "papers": [card(p) for p in graph["nodes"].values()],
            "edges": graph["edges"],
            "coverage": graph["coverage"],
            "errors": graph["errors"],
            "first_graph_page_bytes": view["payload_bytes"],
            "http_requests": web.requests,
        }
        docs = Documents(papers)
        readable, full = [], []
        # Compare title-matched papers; graph growth alone does not establish relevance.
        candidates = [
            p
            for p in graph["nodes"].values()
            if query.casefold() in p["title"].casefold() and (p.get("arxiv") or p.get("pdf"))
        ][:8]
        for p in candidates:
            if len(readable) == 5:
                continue
            try:
                pieces, offset = [], 0
                while True:
                    part = await docs.read(p["id"], offset=offset, max_chars=12000)
                    pieces.append(part["text"])
                    offset = part["next_offset"]
                    if offset is None:
                        break
                attempt = {
                    "paper_id": p["id"],
                    "title": p["title"],
                    "source_url": part["source_url"],
                    "format": part["format"],
                    "total_chars": part["total_chars"],
                    "warnings": part["warnings"],
                    "fallback_errors": part["fallback_errors"],
                    "included_in_payload": False,
                }
                result["full_text_attempts"].append(attempt)
                if part["format"] == "pdf" and any(
                    warning.startswith("No extractable text:") for warning in part["warnings"]
                ):
                    raise ValueError("No extractable full text; excluded from paired payload")
                readable.append(card(p))
                full.append({"paper": card(p), "text": "".join(pieces)})
                attempt["included_in_payload"] = True
            except Exception as error:
                result["errors"].append({"paper_id": p["id"], "error": str(error)[:300]})
        card_bytes, dump_bytes = size(readable), size(full)
        result["paired_payload"] = {
            "readable_papers": len(readable),
            "paper_ids": [p["id"] for p in readable],
            "cards_bytes": card_bytes,
            "full_text_dump_bytes": dump_bytes,
            "reduction_percent": round(100 * (1 - card_bytes / dump_bytes), 2)
            if readable
            else None,
            "definition": "Compact UTF-8 JSON for the same readable papers. Full dump = "
            "cards plus extracted text, excluding images. Not actual model tokens.",
            "selection": "Graph papers whose title contains the query, with advertised full text.",
        }
    except Exception as error:
        result["errors"].append({"stage": "search/expansion", "error": str(error)[:300]})
    finally:
        result["http_totals"] = {"requests": web.requests, "cache_hits": web.hits}
        await web.close()
        store.close()
    result["caveat"] = (
        "One query, three unscreened search seeds, one bounded hop. Additional papers may be "
        "irrelevant. Provider ranking and corpus change. Does not measure research quality, "
        "SOTA coverage, whole-host latency or accuracy uplift. Failures are retained."
    )
    return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(asyncio.run(measure(Path(directory))), indent=2))
