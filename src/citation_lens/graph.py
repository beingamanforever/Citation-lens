"""Ranked discovery: fused search snapshots and Connected-Papers-style citation graphs.

Every snapshot is stored once and paged as compact cards. A card is a preview: ID, title, year,
citations, why it was selected, and one verbatim abstract sentence chosen for the query.
"""

import asyncio
import json
import math
import re
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from .papers import Papers, arxiv_base, identifier, merge, primary_url, s2_ref

RECENT_DAYS = 730
# One slow or throttled provider must not hold a whole call.
SEARCH_SECONDS, LOOKUP_SECONDS, BATCH_SECONDS = 15, 30, 15
SIMILAR_SEEDS = 4
EDGES_PER_PAGE = 120
MAX_CARDS = 60
STOP = set(
    "a an and are as at be by for from in into is it of on or our over the their this to under "
    "using via we with without what which how new based towards toward".split()
)


def bounded(value: int, low: int, high: int, name: str):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def payload(value: dict) -> dict:
    value["payload_bytes"] = 0
    while True:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
        if size == value["payload_bytes"]:
            return value
        value["payload_bytes"] = size


def describe(error: BaseException) -> str:
    return (str(error) or type(error).__name__)[:160]


def stems(text: str) -> set[str]:
    # ponytail: six-letter prefixes stand in for stemming; add a real stemmer if recall suffers.
    return {
        w[:6] for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in STOP and len(w) > 1
    }


def text_score(paper: dict, queries: list[set[str]]) -> float:
    """1.0 when every query term is in the title; 0.5 when they are only in the abstract."""
    title, abstract = stems(paper["title"]), stems(paper["abstract"])
    return max(
        ((len(q & title) + len(q & (title | abstract))) / (2 * len(q)) for q in queries if q),
        default=0.0,
    )


def snippet(abstract: str, terms: set[str], limit: int = 220) -> str:
    """The abstract sentence sharing most query terms, verbatim, cut at a word boundary."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", abstract.strip()) if s]
    if not sentences:
        return ""
    best = max(enumerate(sentences), key=lambda item: (len(stems(item[1]) & terms), -item[0]))[1]
    return best if len(best) <= limit else best[:limit].rsplit(" ", 1)[0]


def percentile(values: list[float]) -> list[float]:
    ordered = sorted(values)
    top = max(1, len(ordered) - 1)
    position = {}
    for index, value in enumerate(ordered):
        position.setdefault(value, index)  # Ties share the lowest rank, so zeros stay at zero.
    return [position[value] / top for value in values]


def window_start(today: date | None = None) -> str:
    return ((today or date.today()) - timedelta(days=RECENT_DAYS)).isoformat()


def is_recent(paper: dict, since: str) -> bool:
    if paper["date"]:
        return paper["date"] >= since
    return (paper["year"] or 0) > int(since[:4])


def card(paper: dict, terms: set[str], why: str = "", lane: str = "") -> dict:
    result = {
        "id": paper["id"],
        "title": paper["title"][:300],
        "year": paper["year"],
        "cites": paper["citations"],
        "url": paper["url"],
    }
    if lane:
        result["lane"] = lane
    if why:
        result["why"] = why
    if text := snippet(paper["abstract"], terms):
        result["snippet"] = text
    return result


def interleave(lanes: list[list[str]]) -> dict[str, int]:
    """Round-robin the ranked lanes; each ID keeps its first position and the lane it came from."""
    placed: dict[str, int] = {}
    for row in range(max(map(len, lanes), default=0)):
        for lane, members in enumerate(lanes):
            if row < len(members):
                placed.setdefault(members[row], lane)
    return placed


def ranked(items: list[dict], weights: dict[str, float]) -> list[str]:
    return [
        i["id"] for i in sorted(items, key=lambda i: -sum(w * i[f] for f, w in weights.items()))
    ]


class Graphs:
    def __init__(self, papers: Papers):
        self.papers = papers
        self.store = papers.store

    def view(self, graph_id: str, offset: int = 0, limit: int = 20) -> dict:
        """One page of cards plus the citation edges that this page completes."""
        bounded(offset, 0, 10_000, "offset")
        limit = max(1, min(limit, MAX_CARDS))  # Clamp rather than fail: a retry costs a turn.
        graph = self.store.load("graph:" + graph_id)
        if graph is None:
            raise ValueError("Unknown or expired graph_id")
        order, nodes = graph["order"], graph["nodes"]
        rank = {pid: -1 for pid in graph["seeds"]} | {pid: i for i, pid in enumerate(order)}
        terms = set().union(*map(set, graph["terms"]))
        result = {
            "graph_id": graph_id,
            "total": len(order),
            "offset": offset,
            "papers": [
                card(nodes[pid], terms, *graph["notes"].get(pid, ("", "")))
                for pid in order[offset : offset + limit]
            ],
            "next_offset": offset + limit if offset + limit < len(order) else None,
        }
        if "searches" in graph:
            result["searches"] = graph["searches"]
        if "coverage" in graph:
            result["seeds"] = graph["coverage"]
        if "errors" in graph:
            result["errors"] = graph["errors"]
        if graph["seeds"]:
            # Each edge appears once: on the page that shows its later-ranked endpoint;
            # edges between seeds (rank -1) come with the first page.
            edges = [
                edge
                for edge in graph["edges"]
                if edge[0] in rank
                and edge[1] in rank
                and (offset or -1) <= max(rank[edge[0]], rank[edge[1]]) < offset + limit
            ]
            # Dense neighborhoods have hundreds of links; keep those touching seeds and the
            # best-ranked cards so edges never dominate the context.
            edges.sort(key=lambda edge: sorted((rank[edge[0]], rank[edge[1]])))
            result["edges"] = edges[:EDGES_PER_PAGE]
            if len(edges) > EDGES_PER_PAGE:
                result["edges_omitted"] = len(edges) - EDGES_PER_PAGE
            displayed = {tuple(edge) for edge in result["edges"]}
            evidence = [
                item for item in graph.get("edge_evidence", []) if tuple(item["edge"]) in displayed
            ]
            if evidence:
                result["edge_evidence"] = evidence
            result["edge_note"] = (
                "[citing, cited] from reference lists; primary arXiv links include compact "
                "edge_evidence with the inspected bibliography item"
            )
        return result

    def _save(self, graph: dict) -> str:
        graph_id = uuid.uuid4().hex[:12]
        graph["created_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        for paper in graph["nodes"].values():
            cached = paper
            # A versioned seed navigates its bibliography, not the cached abstract metadata.
            if paper["arxiv"] != arxiv_base(paper["arxiv"]):
                versioned = paper["arxiv"]
                cached = paper | {"arxiv": arxiv_base(versioned)}
                cached["url"] = primary_url(cached)
                if paper["pdf"] == "https://arxiv.org/pdf/" + versioned:
                    cached["pdf"] = "https://arxiv.org/pdf/" + cached["arxiv"]
            self.papers.remember(cached)
        graph["nodes"] = {
            pid: {k: v for k, v in paper.items() if k != "refs"}
            for pid, paper in graph["nodes"].items()
        }
        self.store.save("graph:" + graph_id, graph)
        return graph_id

    async def search(self, queries: list[str], providers: list[str], limit: int = 20) -> dict:
        """Run every query on every provider concurrently, fuse their rankings, and reserve
        every third card for work from the last two years."""
        started, since = time.monotonic(), window_start()
        jobs = [(q, p, False) for q in queries for p in providers if p != "arxiv"]
        if "arxiv" in providers:
            both = "\n".join(queries)
            jobs += [(both, "arxiv", False), (both, "arxiv", True)]
        results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    self.papers.search(q, p, 25, since if newest else None, newest),
                    SEARCH_SECONDS,
                )
                for q, p, newest in jobs
            ),
            return_exceptions=True,
        )
        records, positions, searches = [], [], []
        succeeded = 0
        for job, ((query, provider, newest), result) in enumerate(zip(jobs, results, strict=True)):
            entry = {"query": query.replace("\n", " | "), "provider": provider}
            if newest:
                entry["newest_since"] = since
            if isinstance(result, Exception):
                searches.append(entry | {"error": describe(result)})
                continue
            succeeded += 1
            found, total = result
            searches.append(entry | {"returned": len(found), "total": total})
            records += found
            positions += [(job, rank) for rank in range(len(found))]
        if not succeeded:
            raise RuntimeError("Every search failed: " + json.dumps(searches))

        terms = [stems(q) for q in queries]
        if not records:
            graph = {
                "terms": [sorted(t) for t in terms],
                "seeds": [],
                "edges": [],
                "order": [],
                "notes": {},
                "nodes": {},
                "searches": searches,
            }
            result = self.view(self._save(graph), 0, limit)
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            return result

        papers, owners = merge(records)
        best_rank: dict[str, dict[int, int]] = {}
        for pid, (job, rank) in zip(owners, positions, strict=True):
            ranks = best_rank.setdefault(pid, {})
            ranks[job] = min(rank, ranks.get(job, rank))
        # Reciprocal rank fusion: agreement across queries and providers raises a paper.
        fused = {pid: sum(1 / (20 + r) for r in ranks.values()) for pid, ranks in best_rank.items()}
        top = max(fused.values())
        prominence = percentile([math.log1p(p["citations"] or 0) for p in papers])
        items = [
            {
                "id": p["id"],
                "fused": fused[p["id"]] / top,
                "text": text_score(p, terms),
                "prominence": prom,
                "recent": is_recent(p, since),
            }
            for p, prom in zip(papers, prominence, strict=True)
        ]
        best = ranked(items, {"fused": 0.45, "text": 0.25, "prominence": 0.3})
        recent = ranked([i for i in items if i["recent"]], {"fused": 0.5, "text": 0.5})
        placed = interleave([best[0::2], best[1::2], recent])
        order = list(placed)
        notes = {
            pid: (
                f"matched {len(best_rank[pid])}/{len(jobs)} searches",
                "recent" if lane == 2 else "",
            )
            for pid, lane in placed.items()
        }
        graph = {
            "terms": [sorted(t) for t in terms],
            "seeds": [],
            "edges": [],
            "order": order,
            "notes": notes,
            "nodes": {p["id"]: p for p in papers},
            "searches": searches,
        }
        result = self.view(self._save(graph), 0, limit)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return result

    async def expand(
        self, seed_ids: list[str], query: str, direction: str = "both", limit: int = 20
    ) -> dict:
        """Snowball one hop from the seeds and rank neighbors like Connected Papers:
        foundations are cited by many graph papers, follow-ups cite the seeds or share their
        references, and recent work comes from the last two years. Query match keeps the graph
        on topic; Semantic Scholar recommendations add similar papers a citation list misses."""
        started, since = time.monotonic(), window_start()
        requested_versions: dict[str, set[str]] = {}
        for seed_id in seed_ids:
            normalized = identifier(seed_id)
            if normalized.startswith("ARXIV:"):
                value = normalized[6:]
                if value != arxiv_base(value):
                    requested_versions.setdefault(arxiv_base(value), set()).add(value)
        seeds = await self.papers.resolve_many(seed_ids, BATCH_SECONDS)
        if not seeds:
            raise ValueError("None of the seed IDs could be resolved")
        conflicting_bases = {
            base for base, versions in requested_versions.items() if len(versions) > 1
        }
        version_errors = []
        for base, versions in requested_versions.items():
            if len(versions) > 1:
                version_errors.append(
                    {
                        "seed": "ARXIV:" + base,
                        "lookup": "seed",
                        "error": "Conflicting requested arXiv versions: "
                        + ", ".join(sorted(versions)),
                    }
                )
                continue
            version = next(iter(versions))
            seeds = [
                seed
                | {
                    "arxiv": version,
                    "url": "https://arxiv.org/abs/" + version,
                    "pdf": "https://arxiv.org/pdf/" + version,
                }
                if arxiv_base(seed["arxiv"]) == base
                else seed
                for seed in seeds
            ]
        lookups = []
        if direction in ("both", "forward"):
            for index, seed in enumerate(seeds):
                lookups.append((index, "citations", self.papers.citations(seed, since)))
                if index < SIMILAR_SEEDS:  # Seeds come most central first; spare the API.
                    lookups.append((index, "similar", self.papers.similar(seed)))
        backward = direction in ("both", "backward")
        reference_indices = [
            index
            for index, seed in enumerate(seeds)
            if backward and arxiv_base(seed["arxiv"]) not in conflicting_bases
        ]
        results = await asyncio.gather(
            *(asyncio.wait_for(call, LOOKUP_SECONDS) for *_, call in lookups),
            asyncio.wait_for(
                self.papers.references([seeds[index] for index in reference_indices]),
                LOOKUP_SECONDS,
            ),
            return_exceptions=True,
        )
        # One batch serves every seed's references; split it back into per-seed lookups.
        references = results.pop()
        by_seed = (
            {}
            if isinstance(references, BaseException)
            else dict(zip(reference_indices, references, strict=True))
        )
        for index in range(len(seeds) if backward else 0):
            lookups.append((index, "references", None))
            if arxiv_base(seeds[index]["arxiv"]) in conflicting_bases:
                results.append(
                    RuntimeError("Backward references skipped for conflicting arXiv versions")
                )
            else:
                results.append(
                    references if isinstance(references, BaseException) else by_seed[index]
                )

        records, raw_edges, primary_evidence, recommended = list(seeds), [], [], []
        errors = version_errors
        for seed in seeds:
            if not s2_ref(seed):
                errors.append(
                    {
                        "seed": seed["id"],
                        "error": "No arXiv, DOI or Semantic Scholar "
                        "ID: references and citing papers come from OpenAlex only",
                    }
                )
        coverage = [{"cites": seed["citations"]} for seed in seeds]
        if len(seeds) < len(seed_ids):
            errors.append({"error": f"{len(seed_ids) - len(seeds)} seed ID(s) not resolved"})
        for (index, kind, _), result in zip(lookups, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(
                    {"seed": seeds[index]["id"], "lookup": kind, "error": describe(result)}
                )
                continue
            if kind == "citations":
                result, failures = result
                errors += [
                    {"seed": seeds[index]["id"], "lookup": kind, "error": e} for e in failures
                ]
            elif kind == "references":
                errors += [
                    {"seed": seeds[index]["id"], "lookup": kind, "error": error}
                    for error in result["errors"]
                ]
                coverage[index]["reference_coverage"] = result["coverage"]
                evidence = {item["cited"]: item for item in result["evidence"]}
                result = result["papers"]
            coverage[index][kind] = len(result)
            for rank, paper in enumerate(result):
                if kind == "similar":
                    recommended.append((len(records), rank))
                else:
                    pair = (index, len(records))
                    raw_edges.append(pair if kind == "references" else pair[::-1])
                    if kind == "references" and paper["id"] in evidence:
                        primary_evidence.append((pair[0], pair[1], evidence[paper["id"]]))
                records.append(paper)

        papers, owners = merge(records)
        nodes = {p["id"]: p for p in papers}
        seed_set = list(dict.fromkeys(owners[: len(seeds)]))
        edges = {(owners[a], owners[b]) for a, b in raw_edges if owners[a] != owners[b]}
        edge_evidence = []
        evidenced = set()
        for citing, cited, evidence in primary_evidence:
            edge = (owners[citing], owners[cited])
            if edge in edges and edge not in evidenced:
                edge_evidence.append(
                    {
                        "edge": list(edge),
                        "matched_id": evidence["matched_id"],
                        "source": evidence["source"],
                        "url": evidence["url"],
                    }
                )
                evidenced.add(edge)
        pool = [p for p in papers if p["id"] not in seed_set]
        if not pool:
            raise RuntimeError("No citation neighbors found: " + json.dumps(errors))
        similar = {p["id"]: 0.0 for p in pool}
        similar_seeds = {p["id"]: 0 for p in pool}
        for record, rank in recommended:
            if (pid := owners[record]) in similar:
                similar[pid] += 1 - rank / 100
                similar_seeds[pid] += 1

        def direct(pid):
            return sum((pid, s) in edges or (s, pid) in edges for s in seed_set)

        # Reference lists of the likeliest candidates reveal coupling, co-citation and the
        # edges between candidates. One batch request covers them.
        terms = [stems(query)]
        shortlist = sorted(
            pool, key=lambda p: -(direct(p["id"]) + 2 * text_score(p, terms) + similar[p["id"]])
        )
        try:
            hydrated = self.papers.with_references(shortlist[:400])
            for paper in await asyncio.wait_for(hydrated, BATCH_SECONDS):
                nodes[paper["id"]]["refs"] = paper["refs"]
        except (RuntimeError, ValueError, TimeoutError) as error:
            errors.append({"lookup": "candidate references", "error": describe(error)})
        owner = {}
        for record, pid in zip(records, owners, strict=True):
            for name in ("s2", "oa"):
                if record[name]:
                    owner.setdefault(f"{name}:{record[name]}", pid)
        for paper in nodes.values():
            for ref in paper["refs"]:
                if (target := owner.get(ref)) and target != paper["id"]:
                    edges.add((paper["id"], target))

        cited_by = {pid: set() for pid in nodes}
        references = {pid: set() for pid in nodes}
        for a, b in edges:
            cited_by[b].add(a)
            references[a].add(b)
        seed_members = set(seed_set)
        seed_refs = set().union(*(references[s] for s in seed_set)) - seed_members
        seed_citers = set().union(*(cited_by[s] for s in seed_set))
        # A citation counts by how on-topic the citing paper is: seeds count fully, while
        # generic papers citing a popular tool add little (an application of FlashAttention
        # does not make vLLM a foundation of exact attention).
        relevance = {
            pid: 1.0 if pid in seed_members else text_score(nodes[pid], terms) for pid in nodes
        }
        this_year = date.today().year
        items = []
        for paper in pool:
            pid = paper["id"]
            weighted_citers = sum(relevance[c] for c in cited_by[pid])
            shared = references[pid] & seed_refs
            # Bibliographic coupling (Adamic/Adar weighted, Salton normalized) and co-citation.
            coupling = sum(1 / math.log(2 + (nodes[r]["citations"] or 0)) for r in shared)
            coupling /= math.sqrt(max(1, len(paper["refs"]) or len(references[pid])))
            age = max(1, this_year - (paper["year"] or this_year) + 1)
            items.append(
                {
                    "id": pid,
                    "direct": direct(pid) / len(seed_set),
                    "coupling": coupling,
                    "cocited": sum(relevance[c] for c in cited_by[pid] & seed_citers),
                    "indegree": weighted_citers,
                    "cited_by_relevant": weighted_citers,
                    "similar": similar[pid],
                    "text": text_score(paper, terms),
                    "total": math.log1p(paper["citations"] or 0),
                    "velocity": math.log1p((paper["citations"] or 0) / age),
                    "recent": is_recent(paper, since),
                }
            )
        for field in ("coupling", "cocited", "indegree", "similar", "total", "velocity"):
            for item, value in zip(items, percentile([i[field] for i in items]), strict=True):
                item[field] = value
        # Keep a neighbor that matches the query, resembles a seed, or is cited by several
        # relevant papers (two seeds suffice); drop papers that merely use a seed as a tool and
        # the off-topic entries of a single bibliography.
        kept = [
            i
            for i in items
            if i["text"] >= 0.15 or similar_seeds[i["id"]] or i["cited_by_relevant"] >= 1.5
        ]
        older = [i for i in kept if not i["recent"]]
        # Foundations are cited by a seed or by several relevant graph papers (Connected Papers'
        # prior works); follow-ups cite or resemble the seeds (derivative works).
        foundations = ranked(
            [i for i in older if cited_by[i["id"]] & seed_members or i["cited_by_relevant"] >= 1.5],
            {"indegree": 0.45, "cocited": 0.15, "text": 0.2, "total": 0.2},
        )
        follow_ups = ranked(
            [i for i in older if references[i["id"]] or similar_seeds[i["id"]]],
            {"coupling": 0.3, "direct": 0.2, "similar": 0.15, "text": 0.2, "velocity": 0.15},
        )
        recent = ranked(
            [i for i in kept if i["recent"]],
            {"coupling": 0.25, "direct": 0.15, "similar": 0.15, "text": 0.3, "velocity": 0.15},
        )
        placed = interleave([foundations, follow_ups, recent])
        lane_names = ("foundation", "follow-up", "recent")
        notes = {}
        for pid, lane in placed.items():
            parts = []
            if hits := sum((s, pid) in edges for s in seed_set):
                parts.append(f"cited by {hits}/{len(seed_set)} seeds")
            if hits := sum((pid, s) in edges for s in seed_set):
                parts.append(f"cites {hits}/{len(seed_set)} seeds")
            if hits := similar_seeds[pid]:
                parts.append(f"similar to {hits}/{len(seed_set)} seeds")
            if shared := len(references[pid] & seed_refs):
                parts.append(f"shares {shared} seed refs")
            if others := len(cited_by[pid] - seed_members):
                parts.append(f"cited by {others} graph papers")
            notes[pid] = ("; ".join(parts), lane_names[lane])
        seed_coverage = [
            {"id": pid} | item for pid, item in zip(owners[: len(seeds)], coverage, strict=True)
        ]
        graph = {
            "terms": [sorted(t) for t in terms],
            "seeds": seed_set,
            "order": list(placed),
            "notes": notes,
            "nodes": nodes,
            "edges": sorted(edges),
            "edge_evidence": edge_evidence,
            "coverage": seed_coverage,
            "errors": errors,
        }
        result = self.view(self._save(graph), 0, limit)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return result
