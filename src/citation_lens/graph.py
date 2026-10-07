"""Bounded snowballing with explicit coverage and lossless graph paging."""

import asyncio
import json
import math
import re
import time
import uuid
from datetime import UTC, datetime

from .papers import Papers, aliases


def bounded(value: int, low: int, high: int, name: str):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def payload(value: dict):
    value["payload_bytes"] = 0
    while True:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        if size == value["payload_bytes"]:
            return value
        value["payload_bytes"] = size


def card(p: dict):
    result = {
        k: (p[k][:512] if isinstance(p.get(k), str) else p.get(k))
        for k in (
            "id",
            "title",
            "year",
            "citations",
            "citation_status",
            "provider",
            "url",
            "doi",
            "arxiv",
        )
    } | {
        "abstract_excerpt": p.get("abstract", "")[:650],
        "excerpt_truncated": len(p.get("abstract", "")) > 650,
        "full_text_available": bool(p.get("pdf") or p.get("arxiv")),
    }
    if "search_matches" in p:
        result["search_matches"] = p["search_matches"]
    return result


def relevance(p: dict, query: str):
    terms = set(re.findall(r"\w+", query.lower()))
    words = set(re.findall(r"\w+", (p["title"] + " " + p.get("abstract", "")).lower()))
    overlap = len(terms & words) / max(1, len(terms))
    return overlap + 0.03 * math.log1p(p.get("citations", 0))


def balanced(papers: list[dict], query: str, limit: int):
    """Round-robin topical relevance, recency and prominence; stable tie-breaks."""
    relevant = sorted(papers, key=lambda p: (-relevance(p, query), p["id"]))
    recent = sorted(papers, key=lambda p: (-(p.get("year") or 0), -relevance(p, query), p["id"]))
    prominent = sorted(
        papers, key=lambda p: (-p.get("citations", 0), -relevance(p, query), p["id"])
    )
    selected, seen = [], set()
    for row in zip(relevant, recent, prominent, strict=True):
        for p in row:
            if p["id"] not in seen:
                selected.append(p)
                seen.add(p["id"])
                if len(selected) == limit:
                    return selected
    return selected


class Graphs:
    def __init__(self, papers: Papers):
        self.papers = papers
        self.store = papers.store
        self.locks: dict[str, asyncio.Lock] = {}

    def view(self, graph_id: str, offset=0, limit=10, edge_offset=0):
        bounded(offset, 0, 200, "offset")
        bounded(limit, 1, 20, "limit")
        bounded(edge_offset, 0, 20000, "edge_offset")
        graph = self.store.load("graph:" + graph_id)
        if graph is None:
            raise ValueError("Unknown or expired graph_id")
        ordered = balanced(list(graph["nodes"].values()), graph["query"], 200)
        edges = graph["edges"][edge_offset : edge_offset + 50]
        result = {
            "graph_id": graph_id,
            "query": graph["query"],
            "created_at": graph["created_at"],
            "node_count": len(ordered),
            "edge_count": len(graph["edges"]),
            "papers": [card(p) for p in ordered[offset : offset + limit]],
            "next_offset": offset + limit if offset + limit < len(ordered) else None,
            "edges": edges,
            "next_edge_offset": edge_offset + 50
            if edge_offset + 50 < len(graph["edges"])
            else None,
            "coverage": graph["coverage"][-12:],
            "errors": graph["errors"][-12:],
            "coverage_entries": len(graph["coverage"]),
            "error_count": len(graph["errors"]),
            "stops": graph["stops"],
            "selection": "relevance/recency/prominence heuristic, not SOTA",
        }
        if "searches" in graph:
            result["searches"] = graph["searches"]
        return payload(result)

    async def search(self, query, provider, limit, recent=False, year_from=None):
        queries = [query] if isinstance(query, str) else query
        providers = [provider] if isinstance(provider, str) else provider
        if (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 3
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 500
                for value in queries
            )
            or len(set(queries)) != len(queries)
        ):
            raise ValueError(
                "query must be 1 to 3 distinct nonblank strings of at most 500 characters"
            )
        allowed_providers = {"openalex", "semantic_scholar", "arxiv"}
        if (
            not isinstance(providers, list)
            or not 1 <= len(providers) <= 3
            or any(value not in allowed_providers for value in providers)
            or len(set(providers)) != len(providers)
        ):
            raise ValueError("provider must contain 1 to 3 distinct supported providers")
        bounded(limit, 1, 20, "limit")
        if not isinstance(recent, bool):
            raise ValueError("recent must be a boolean")
        if year_from is not None:
            bounded(year_from, 1800, 2100, "year_from")

        jobs = [(term, source) for term in queries for source in providers]
        results = await asyncio.gather(
            *[self.papers.search(term, source, limit, recent, year_from) for term, source in jobs],
            return_exceptions=True,
        )
        searches = []
        components = []
        succeeded = 0
        result_order = 0
        for search_index, ((term, source), result) in enumerate(zip(jobs, results, strict=True)):
            search = {
                "query": term,
                "provider": source,
                "recent": recent,
                "year_from": year_from,
            }
            if isinstance(result, Exception):
                searches.append(
                    search
                    | {
                        "returned": None,
                        "total_indexed_matches": None,
                        "sampled": None,
                        "error": str(result)[:200],
                    }
                )
                continue
            succeeded += 1
            found, total = result
            searches.append(
                search
                | {
                    "returned": len(found),
                    "total_indexed_matches": total,
                    "sampled": total > len(found),
                    "error": None,
                }
            )
            for paper in found:
                result_order += 1
                paper_aliases = aliases(paper)
                overlapping = [part for part in components if part["aliases"] & paper_aliases]
                if not overlapping:
                    components.append(
                        {
                            "paper": paper,
                            "paper_order": result_order,
                            "aliases": set(paper_aliases),
                            "search_matches": {search_index},
                        }
                    )
                    continue
                representative = overlapping[0]
                candidates = [(part["paper_order"], part["paper"]) for part in overlapping] + [
                    (result_order, paper)
                ]
                readable = [
                    candidate
                    for candidate in candidates
                    if candidate[1].get("arxiv") or candidate[1].get("pdf")
                ]
                selected_order, selected_paper = min(
                    readable or candidates, key=lambda item: item[0]
                )
                representative["paper"] = selected_paper
                representative["paper_order"] = selected_order
                representative["aliases"].update(paper_aliases)
                representative["search_matches"].add(search_index)
                for duplicate in overlapping[1:]:
                    representative["aliases"].update(duplicate["aliases"])
                    representative["search_matches"].update(duplicate["search_matches"])
                    components.remove(duplicate)

        if not succeeded:
            details = "; ".join(
                f"{item['query']}/{item['provider']}: {item['error']}" for item in searches
            )
            raise RuntimeError(("All searches failed: " + details)[:500])

        nodes = {}
        for component in components:
            paper = dict(component["paper"])
            paper["aliases"] = sorted(component["aliases"])
            paper["search_matches"] = sorted(component["search_matches"])
            nodes[paper["id"]] = paper
        graph_id = uuid.uuid4().hex
        self.store.save(
            "graph:" + graph_id,
            {
                "query": queries[0],
                "nodes": nodes,
                "edges": [],
                "coverage": [],
                "errors": [
                    {"query": item["query"], "provider": item["provider"], "error": item["error"]}
                    for item in searches
                    if item["error"] is not None
                ],
                "expanded": [],
                "stops": [],
                "created_at": datetime.now(UTC).isoformat(),
                "searches": searches,
            },
        )
        return self.view(graph_id)

    async def expand(
        self,
        seed_ids: list[str],
        query: str,
        graph_id: str | None = None,
        depth=1,
        beam=4,
        max_nodes=80,
        neighbors=20,
        direction="both",
    ):
        if not 1 <= len(seed_ids) <= 8 or len(set(seed_ids)) != len(seed_ids):
            raise ValueError("Provide 1 to 8 distinct seed_ids")
        for value, lo, hi, name in (
            (depth, 1, 3, "depth"),
            (beam, 1, 8, "beam"),
            (max_nodes, 1, 200, "max_nodes"),
            (neighbors, 1, 40, "neighbors"),
        ):
            bounded(value, lo, hi, name)
        if direction not in ("both", "forward", "backward"):
            raise ValueError("direction must be forward, backward, or both")
        if not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1 to 500 characters")
        started = time.monotonic()
        resuming = graph_id is not None
        graph_id = graph_id or uuid.uuid4().hex
        # ponytail: per-process locks; add revision checks for cross-process shared writes.
        async with self.locks.setdefault(graph_id, asyncio.Lock()):
            graph = self.store.load("graph:" + graph_id)
            if resuming and graph is None:
                raise ValueError("Unknown or expired graph_id")
            if graph is None:
                graph = {
                    "query": query,
                    "nodes": {},
                    "edges": [],
                    "coverage": [],
                    "errors": [],
                    "expanded": [],
                    "stops": [],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            elif query != graph["query"]:
                raise ValueError("A resumed graph must keep its original query")
            if max_nodes < len(graph["nodes"]) or max_nodes < len(seed_ids):
                raise ValueError("max_nodes cannot be smaller than the graph or seed count")
            seeds = await asyncio.gather(
                *[self.papers.resolve(s) for s in seed_ids], return_exceptions=True
            )
            alias_map = {a: p["id"] for p in graph["nodes"].values() for a in aliases(p)}

            def add(p):
                canonical = next(
                    (alias_map[a] for a in sorted(aliases(p)) if a in alias_map), p["id"]
                )
                if canonical not in graph["nodes"]:
                    if len(graph["nodes"]) >= max_nodes:
                        return None
                    graph["nodes"][canonical] = dict(p)
                elif p["id"] == canonical:
                    existing = graph["nodes"][canonical]
                    refreshed = dict(p)
                    refreshed["aliases"] = sorted(aliases(existing) | aliases(p))
                    if "search_matches" in existing or "search_matches" in p:
                        refreshed["search_matches"] = sorted(
                            set(existing.get("search_matches", []))
                            | set(p.get("search_matches", []))
                        )
                    graph["nodes"][canonical] = refreshed
                else:
                    existing = graph["nodes"][canonical]
                    existing["aliases"] = sorted(aliases(existing) | aliases(p))
                alias_map.update(dict.fromkeys(aliases(graph["nodes"][canonical]), canonical))
                return canonical

            frontier = []
            for seed, result in zip(seed_ids, seeds, strict=True):
                if isinstance(result, Exception):
                    graph["errors"].append({"seed": seed, "error": str(result)[:200]})
                elif (pid := add(result)) and pid not in frontier:
                    frontier.append(pid)
            directions = ("backward", "forward") if direction == "both" else (direction,)
            edge_keys = {(e["from"], e["to"]) for e in graph["edges"]}
            for _hop in range(depth):
                jobs = [
                    (pid, d)
                    for pid in frontier
                    for d in directions
                    if not any(e[:2] == [pid, d] and e[2] >= neighbors for e in graph["expanded"])
                ]
                if not jobs:
                    graph["stops"].append("frontier exhausted or already expanded")
                    break
                results = await asyncio.gather(
                    *[self.papers.neighbors(graph["nodes"][pid], d, neighbors) for pid, d in jobs],
                    return_exceptions=True,
                )
                discovered = []
                for (pid, d), result in zip(jobs, results, strict=True):
                    if isinstance(result, Exception):
                        graph["errors"].append(
                            {"seed": pid, "direction": d, "error": str(result)[:200]}
                        )
                        continue
                    candidates, coverage = result
                    selected = balanced(candidates, query, neighbors)
                    retained = 0
                    for p in selected:
                        if (other := add(p)) is None:
                            continue
                        retained += 1
                        discovered.append(graph["nodes"][other])
                        source, target = (pid, other) if d == "backward" else (other, pid)
                        if source != target and (source, target) not in edge_keys:
                            edge_keys.add((source, target))
                            graph["edges"].append(
                                {
                                    "from": source,
                                    "to": target,
                                    "provider": p["provider"],
                                    "evidence": p.get("edge_evidence"),
                                }
                            )
                    graph["coverage"].append(
                        {
                            "seed": pid,
                            "direction": d,
                            **coverage,
                            "selected": len(selected),
                            "retained": retained,
                        }
                    )
                    # A capped expansion can be retried after increasing max_nodes.
                    if retained == len(selected):
                        graph["expanded"].append([pid, d, neighbors])
                if len(graph["nodes"]) >= max_nodes:
                    graph["stops"].append("max_nodes reached")
                    break
                frontier = [p["id"] for p in balanced(discovered, query, beam)]
            graph["stops"] = list(dict.fromkeys(graph["stops"]))
            self.store.save("graph:" + graph_id, graph)
        view = self.view(graph_id)
        view["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return payload(view)
