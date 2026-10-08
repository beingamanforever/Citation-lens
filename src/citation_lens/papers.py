"""Scholarly providers behind one paper record.

Semantic Scholar supplies complete reference lists and the newest citing papers, OpenAlex sorts
citing papers by prominence, and arXiv supplies fresh preprints. Records from different providers
merge on shared arXiv, DOI, provider or exact-title keys.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urljoin

from bs4 import BeautifulSoup

from .network import Web

S2 = "https://api.semanticscholar.org/graph/v1"
OA = "https://api.openalex.org/works"
ARXIV = "https://export.arxiv.org/api/query"
S2_FIELDS = (
    "paperId,externalIds,title,abstract,year,publicationDate,citationCount,venue,authors,"
    "openAccessPdf"
)
OA_FIELDS = (
    "id,doi,ids,title,publication_year,publication_date,cited_by_count,abstract_inverted_index,"
    "authorships,primary_location,best_oa_location,referenced_works"
)
ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})"
ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "o": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}
PREFIXES = {"ARXIV": "arxiv", "DOI": "doi", "S2": "s2", "OA": "oa"}
STOPWORDS = set("a an and are as at by for from in of on or the to via with without".split())
CITATION_PROVIDER_SECONDS = 20
REFERENCE_PROVIDER_SECONDS = 8
PRIMARY_REFERENCE_SECONDS = 12
PRIMARY_REFERENCE_LIMIT = 100
PRIMARY_HYDRATION_LIMIT = 100


def _provider_error(provider: str, error: BaseException) -> str:
    detail = str(error) or type(error).__name__
    return f"{provider}: {detail}"[:160]


def identifier(value: str) -> str:
    """Normalize a paper URL or ID to ARXIV:, DOI:, S2: or OA:. arXiv versions are kept."""
    text = value.strip().removesuffix(".pdf")
    patterns = (
        (
            rf"(?i)^(?:arxiv:|https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/)?({ARXIV_ID}(?:v\d+)?)$",
            "ARXIV:",
        ),
        (rf"(?i)^(?:doi:|https?://(?:dx\.)?doi\.org/)?10\.48550/arxiv\.({ARXIV_ID})$", "ARXIV:"),
        (r"(?i)^(?:doi:|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", "DOI:"),
        (r"(?i)^(?:oa:|https?://(?:api\.)?openalex\.org/(?:works/)?)?(W\d+)$", "OA:"),
        (
            r"(?i)^(?:s2:|https?://(?:www\.)?semanticscholar\.org/paper/(?:[^/]+/)?)?([0-9a-f]{40})$",
            "S2:",
        ),
    )
    for pattern, prefix in patterns:
        if match := re.match(pattern, text):
            found = match.group(1)
            return prefix + (found.lower() if prefix == "DOI:" else found)
    raise ValueError("Use an arXiv ID, DOI, OpenAlex W-ID, Semantic Scholar ID, or their URLs")


def arxiv_base(value: str) -> str:
    return re.sub(r"v\d+$", "", value or "")


def keys(paper: dict) -> set[str]:
    """Stable identifiers plus an exact-title candidate key."""
    found = {f"{name}:{paper[name]}" for name in ("s2", "oa", "doi") if paper.get(name)}
    if paper.get("arxiv"):
        found.add("arxiv:" + arxiv_base(paper["arxiv"]))
    words = re.findall(r"\w+", (paper.get("title") or "").casefold())
    # ponytail: exact-title merging can join two different works with one long title.
    if len(words) >= 4:
        found.add("title:" + " ".join(words))
    return found


def canonical_id(paper: dict) -> str:
    for prefix, name in PREFIXES.items():
        if paper.get(name):
            return prefix + ":" + (arxiv_base(paper[name]) if name == "arxiv" else paper[name])
    raise ValueError("Paper has no identifier")


def primary_url(paper: dict) -> str:
    if paper.get("arxiv"):
        return "https://arxiv.org/abs/" + arxiv_base(paper["arxiv"])
    if paper.get("doi"):
        return "https://doi.org/" + paper["doi"]
    if paper.get("s2"):
        return "https://www.semanticscholar.org/paper/" + paper["s2"]
    return "https://openalex.org/" + paper["oa"]


def _record(**fields) -> dict:
    paper = {
        "title": "",
        "year": None,
        "date": None,
        "authors": [],
        "venue": "",
        "citations": None,
        "abstract": "",
        "abstract_source": None,
        "arxiv": "",
        "doi": "",
        "s2": "",
        "oa": "",
        "pdf": None,
        "refs": [],
        "sources": [],
    } | fields
    doi = (paper["doi"] or "").strip().lower().removeprefix("https://doi.org/")
    if match := re.fullmatch(rf"10\.48550/arxiv\.({ARXIV_ID})", doi):
        paper["arxiv"], doi = paper["arxiv"] or match.group(1), ""
    paper["doi"] = doi
    paper["title"] = " ".join(paper["title"].split())
    if not paper["abstract"].strip():
        paper["abstract"] = ""
        paper["abstract_source"] = None
    paper["year"] = paper["year"] or (int(paper["date"][:4]) if paper["date"] else None)
    paper["id"] = canonical_id(paper)
    paper["url"] = primary_url(paper)
    return paper


def from_s2(raw: dict) -> dict:
    ids = raw.get("externalIds") or {}
    return _record(
        title=raw.get("title") or "",
        year=raw.get("year"),
        date=raw.get("publicationDate"),
        authors=[a["name"] for a in raw.get("authors") or [] if a.get("name")],
        venue=raw.get("venue") or "",
        citations=raw.get("citationCount"),
        abstract=raw.get("abstract") or "",
        abstract_source="semantic_scholar" if raw.get("abstract") else None,
        arxiv=ids.get("ArXiv") or "",
        doi=ids.get("DOI") or "",
        s2=raw["paperId"],
        pdf=(raw.get("openAccessPdf") or {}).get("url") or None,
        refs=["s2:" + r["paperId"] for r in raw.get("references") or [] if r.get("paperId")],
        sources=["semantic_scholar"],
    )


def from_openalex(raw: dict) -> dict:
    positions = raw.get("abstract_inverted_index") or {}
    abstract = " ".join(w for _, w in sorted((i, w) for w, ids in positions.items() for i in ids))
    location = raw.get("best_oa_location") or raw.get("primary_location") or {}
    arxiv = re.search(rf"arxiv\.org/(?:abs|pdf)/({ARXIV_ID})", str(raw.get("ids") or {}))
    return _record(
        title=raw.get("title") or "",
        year=raw.get("publication_year"),
        date=raw.get("publication_date"),
        authors=[
            a["author"]["display_name"]
            for a in raw.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        venue=((raw.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
        citations=raw.get("cited_by_count"),
        abstract=abstract,
        abstract_source="openalex" if abstract else None,
        arxiv=arxiv.group(1) if arxiv else "",
        doi=raw.get("doi") or "",
        oa=raw["id"].rsplit("/", 1)[-1],
        pdf=location.get("pdf_url"),
        refs=["oa:" + r.rsplit("/", 1)[-1] for r in raw.get("referenced_works") or []],
        sources=["openalex"],
    )


def from_arxiv(data: bytes) -> tuple[list[dict], int]:
    root = ET.fromstring(data)
    papers = []
    for entry in root.findall("a:entry", ATOM):
        match = re.search(rf"arxiv\.org/abs/({ARXIV_ID})", entry.findtext("a:id", "", ATOM))
        if not match:
            continue  # arXiv reports query errors as entries without a paper ID.
        published = entry.findtext("a:published", "", ATOM)[:10]
        papers.append(
            _record(
                title=entry.findtext("a:title", "", ATOM),
                date=published or None,
                authors=[a.findtext("a:name", "", ATOM) for a in entry.findall("a:author", ATOM)],
                venue="arXiv",
                abstract=" ".join(entry.findtext("a:summary", "", ATOM).split()),
                abstract_source="arxiv",
                arxiv=match.group(1),
                doi=entry.findtext("arxiv:doi", "", ATOM),
                pdf="https://arxiv.org/pdf/" + match.group(1),
                sources=["arxiv"],
            )
        )
    return papers, int(root.findtext("o:totalResults", "0", ATOM) or 0)


def _combine(first: dict, second: dict) -> dict:
    """Merge two records of one work; arXiv text is primary, the larger citation count wins."""
    rank = {"arxiv": 0, "semantic_scholar": 1, "openalex": 2}
    a, b = sorted((first, second), key=lambda p: min(rank[s] for s in p["sources"]))
    merged = dict(a)
    merged["title"] = a["title"] if a["title"].strip() else b["title"]
    for field in ("venue", "arxiv", "doi", "s2", "oa", "pdf"):
        merged[field] = a[field] or b[field]
    abstract = a if a["abstract"].strip() else b
    merged["abstract"] = abstract["abstract"]
    merged["abstract_source"] = abstract.get("abstract_source")
    merged["authors"] = max(a["authors"], b["authors"], key=len)
    merged["citations"] = max(
        (p["citations"] for p in (a, b) if p["citations"] is not None), default=None
    )
    dates = [p["date"] for p in (a, b) if p["date"]]
    merged["date"] = min(dates) if dates else None
    merged["year"] = min(y for y in (a["year"], b["year"]) if y) if a["year"] or b["year"] else None
    merged["refs"] = list(dict.fromkeys(a["refs"] + b["refs"]))
    merged["sources"] = list(dict.fromkeys(a["sources"] + b["sources"]))
    return _record(**{k: v for k, v in merged.items() if k not in ("id", "url")})


def _title_compatible(first: dict, second: dict) -> bool:
    """Conservatively confirm that an exact title is enough to join two provider records."""
    for name in ("arxiv", "doi", "s2", "oa"):
        a, b = first.get(name), second.get(name)
        if name == "arxiv":
            a, b = arxiv_base(a), arxiv_base(b)
        if a and b and a.casefold() != b.casefold():
            return False
    if first.get("year") and second.get("year") and first["year"] != second["year"]:
        return False

    def surnames(paper):
        return {
            words[-1]
            for author in paper.get("authors") or []
            if (words := re.findall(r"\w+", author.casefold()))
        }

    first_authors, second_authors = surnames(first), surnames(second)
    return not first_authors or not second_authors or first_authors == second_authors


def merge(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Collapse records sharing an identity key. Returns the merged papers in first-seen order
    and, for every input record, the ID of the paper it became."""
    groups: list[dict] = []
    parent: list[int] = []
    owner: dict[str, int] = {}
    members = []

    def root(index):
        while parent[index] != index:
            index = parent[index]
        return index

    for record in records:
        record_keys = keys(record)
        stable_keys = {key for key in record_keys if not key.startswith("title:")}
        hits = sorted({root(owner[k]) for k in stable_keys if k in owner})
        if not hits:
            title_keys = record_keys - stable_keys
            for candidate, group in enumerate(groups):
                if (
                    root(candidate) == candidate
                    and title_keys & keys(group)
                    and _title_compatible(group, record)
                    and all(_title_compatible(group, groups[index]) for index in hits)
                ):
                    hits.append(candidate)
        arxiv_ids = {arxiv_base(groups[i]["arxiv"]) for i in hits if groups[i]["arxiv"]}
        if record["arxiv"]:
            arxiv_ids.add(arxiv_base(record["arxiv"]))
        if len(arxiv_ids) > 1:
            hits = []  # Two distinct arXiv papers never merge through a shared DOI or title.
        if hits:
            index = hits[0]
            groups[index] = _combine(groups[index], record)
            for other in hits[1:]:
                groups[index] = _combine(groups[index], groups[other])
                parent[other] = index
        else:
            groups.append(record)
            parent.append(len(groups) - 1)
            index = len(groups) - 1
        for key in keys(record) | keys(groups[index]):
            if key.startswith("title:"):
                continue
            # Never move a key from a live group: a record kept apart by the arXiv guard must
            # not take over the shared identifier of the paper it conflicts with.
            owner.setdefault(key, index)
        members.append(index)
    merged = [group for index, group in enumerate(groups) if parent[index] == index]
    return merged, [groups[root(index)]["id"] for index in members]


def strict_terms(query: str) -> list[str]:
    """Content words and quoted phrases used by strict-match providers."""
    words = re.findall(r'"[^"]+"|[^\s"]+', query)
    return [word for word in words if word.strip('"').casefold() not in STOPWORDS]


def _arxiv_expression(query: str) -> str:
    terms = ['"' + re.sub(r'["\\]', "", t) + '"' for t in strict_terms(query)]
    return " AND ".join(f"(ti:{t} OR abs:{t})" for t in terms if t != '""')


class Papers:
    def __init__(self, web: Web):
        self.web = web
        self.store = web.store

    def remember(self, paper: dict) -> dict:
        self.store.save("paper:" + paper["id"], paper, ttl=86400 * 7)
        for key in keys(paper) - {k for k in keys(paper) if k.startswith("title:")}:
            self.store.save("alias:" + key, paper["id"], ttl=86400 * 7)
        return paper

    def cached(self, paper_id: str) -> dict | None:
        prefix, value = identifier(paper_id).split(":", 1)
        name = PREFIXES[prefix]
        key = f"{name}:{arxiv_base(value) if name == 'arxiv' else value}"
        canonical = self.store.load("alias:" + key)
        return self.store.load("paper:" + canonical) if canonical else None

    async def resolve(self, paper_id: str) -> dict:
        """One paper by ID, from cache, Semantic Scholar, OpenAlex or arXiv."""
        if paper := self.cached(paper_id):
            return paper
        found = await self.resolve_many([paper_id])
        if not found:
            raise ValueError(f"No provider has metadata for {paper_id}")
        return found[0]

    async def resolve_many(self, paper_ids: list[str], s2_seconds: float = 60) -> list[dict]:
        """Batch-resolve IDs; unresolved IDs are omitted. One S2 request covers up to 500."""
        wanted = [identifier(value) for value in paper_ids]
        cached = {value: self.cached(value) for value in wanted}
        missing = [
            value
            for value in wanted
            if not ((cached[value] or {}).get("title") or "").strip()
            or not ((cached[value] or {}).get("abstract") or "").strip()
        ]
        found: dict[str, dict] = {}
        if missing:
            s2_targets = []
            for value in missing:
                ref = s2_ref(cached[value]) if cached[value] else ""
                if not ref and value.startswith("ARXIV:"):
                    ref = "arXiv:" + arxiv_base(value[6:])
                elif not ref and value.startswith("DOI:"):
                    ref = value
                elif not ref and value.startswith("S2:"):
                    ref = value[3:]
                if ref:
                    s2_targets.append((value, ref))
            if s2_targets:
                batch = s2_targets[:500]
                try:
                    rows = await asyncio.wait_for(
                        self.web.json(
                            f"{S2}/paper/batch?fields={S2_FIELDS}",
                            body={"ids": [ref for _, ref in batch]},
                        ),
                        s2_seconds,
                    )
                    for (value, ref), row in zip(batch, rows, strict=False):
                        if row:
                            paper = from_s2(row)
                            if _matches(paper, identifier(ref)):
                                found[value] = paper
                except (RuntimeError, ValueError, TimeoutError):
                    pass  # OpenAlex and arXiv below are independent fallbacks.
            rest = [value for value in missing if value not in found]
            for paper in await self._fallback(rest):
                for value in rest:
                    if _matches(paper, value):
                        found[value] = paper
        result = []
        for value in wanted:
            known = [p for p in (cached[value], found.get(value)) if p]
            if known:
                paper = merge(known)[0][0] if len(known) > 1 else known[0]
                result.append(self.remember(paper) if value in found else paper)
        return result

    async def _fallback(self, values: list[str]) -> list[dict]:
        """Free OpenAlex lookups by W-ID or DOI (arXiv DOIs too) plus one arXiv request for
        every arXiv ID; either source may be throttled."""
        tails = [
            v[3:]
            if v.startswith("OA:")
            else "doi:" + v[4:]
            if v.startswith("DOI:")
            else "doi:10.48550/arxiv." + arxiv_base(v[6:])
            for v in values
            if not v.startswith("S2:")
        ]
        calls = [self.web.json(f"{OA}/{quote(t, safe=':/')}?select={OA_FIELDS}") for t in tails]
        if arxiv_ids := [arxiv_base(v[6:]) for v in values if v.startswith("ARXIV:")]:
            calls.append(self._arxiv_batch(arxiv_ids))
        papers = []
        for result in await asyncio.gather(*calls, return_exceptions=True):
            if isinstance(result, dict):
                papers.append(from_openalex(result))
            elif isinstance(result, list):
                papers += result
        return papers

    async def _arxiv_batch(self, arxiv_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(map(arxiv_base, arxiv_ids)))
        if not ids:
            return []
        params = {"id_list": ",".join(ids), "max_results": len(ids)}
        return from_arxiv(await self.web.fetch(f"{ARXIV}?{urlencode(params)}"))[0]

    async def search(
        self, query: str, provider: str, limit: int, since: str | None = None, newest: bool = False
    ) -> tuple[list[dict], int]:
        """Ranked provider results. since is an ISO date; newest sorts by submission date."""
        if provider == "semantic_scholar":
            params = {"query": query, "limit": limit, "fields": S2_FIELDS}
            if since:
                params["publicationDateOrYear"] = since + ":"
            data = await self.web.json(f"{S2}/paper/search?{urlencode(params)}")
            papers = [from_s2(raw) for raw in data.get("data") or [] if raw.get("paperId")]
            total = data.get("total", len(papers))
        elif provider == "openalex":
            params = {
                "search.title_and_abstract": " ".join(strict_terms(query)),
                "select": OA_FIELDS,
                "per_page": limit,
            }
            if since:
                params["filter"] = "from_publication_date:" + since
            if newest:
                params["sort"] = "publication_date:desc"
            data = await self.web.json(f"{OA}?{urlencode(params)}")
            papers = [from_openalex(raw) for raw in data["results"]]
            total = data["meta"]["count"]
        elif provider == "arxiv":
            expression = " OR ".join(f"({_arxiv_expression(q)})" for q in query.split("\n"))
            if since:
                start = since.replace("-", "")
                expression = f"({expression}) AND submittedDate:[{start}0000 TO 299912312359]"
            params = {
                "search_query": expression,
                "max_results": limit,
                "sortBy": "submittedDate" if newest else "relevance",
                "sortOrder": "descending",
            }
            papers, total = from_arxiv(await self.web.fetch(f"{ARXIV}?{urlencode(params)}"))
        else:
            raise ValueError("provider must be semantic_scholar, openalex or arxiv")
        return [self.remember(paper) for paper in papers], total

    async def references(self, papers: list[dict]) -> list[dict]:
        """References and per-provider coverage for every seed.

        Semantic Scholar and OpenAlex remain authoritative when they return a reference list,
        including an empty one. An unavailable indexed list for an arXiv seed falls back to
        explicit identifiers in that seed's primary HTML bibliography.
        """
        ids = [s2_ref(p) for p in papers]
        wanted = [i for i, ref in enumerate(ids) if ref]
        results = [
            {
                "papers": [],
                "errors": [],
                "coverage": {"source": "unavailable", "returned": 0},
                "evidence": [],
            }
            for _ in papers
        ]
        available = [False] * len(papers)
        if wanted:
            nested = ",".join("references." + field for field in S2_FIELDS.split(","))
            try:
                rows = await asyncio.wait_for(
                    self.web.json(
                        f"{S2}/paper/batch?fields={nested}",
                        body={"ids": [ids[index] for index in wanted]},
                    ),
                    REFERENCE_PROVIDER_SECONDS,
                )
            except (RuntimeError, ValueError, TimeoutError) as error:
                for index in wanted:
                    results[index]["errors"].append(_provider_error("Semantic Scholar", error))
            else:
                for position, index in enumerate(wanted):  # rows follow the ID order
                    row = (
                        rows[position] if isinstance(rows, list) and position < len(rows) else None
                    )
                    refs = row.get("references") if isinstance(row, dict) else None
                    if not isinstance(refs, list):
                        results[index]["errors"].append(
                            "Semantic Scholar: reference list unavailable"
                        )
                        continue
                    try:
                        found = [
                            from_s2(raw)
                            for raw in refs
                            if isinstance(raw, dict) and raw.get("paperId")
                        ]
                    except ValueError as error:
                        results[index]["errors"].append(
                            _provider_error("Semantic Scholar reference metadata", error)
                        )
                        continue
                    results[index]["papers"] = found
                    results[index]["coverage"] = {
                        "source": "semantic_scholar",
                        "returned": len(found),
                    }
                    available[index] = True

        missing = [index for index, known in enumerate(available) if not known]
        tasks = {
            asyncio.create_task(self._openalex_references(papers[index])): index
            for index in missing
        }
        if tasks:
            try:
                done, pending = await asyncio.wait(
                    tasks,
                    timeout=REFERENCE_PROVIDER_SECONDS,
                    return_when=asyncio.ALL_COMPLETED,
                )
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                index = tasks[task]
                try:
                    found = task.result()
                except (RuntimeError, ValueError, TimeoutError) as error:
                    results[index]["errors"].append(_provider_error("OpenAlex", error))
                else:
                    results[index]["papers"] = found
                    results[index]["coverage"] = {
                        "source": "openalex",
                        "returned": len(found),
                    }
                    available[index] = True
            for task in pending:
                results[tasks[task]]["errors"].append("OpenAlex: reference lookup timed out")

        primary = await self._primary_references(
            [
                (index, paper)
                for index, paper in enumerate(papers)
                if not available[index] and paper["arxiv"]
            ]
        )
        for index, recovered in primary.items():
            results[index]["papers"] = recovered["papers"]
            results[index]["errors"] += recovered["errors"]
            results[index]["coverage"] = recovered["coverage"]
            results[index]["evidence"] = recovered["evidence"]
        for result in results:
            result["errors"] = list(dict.fromkeys(result["errors"]))
        return results

    async def _primary_references(self, seeds: list[tuple[int, dict]]) -> dict[int, dict]:
        """Recover explicit arXiv and DOI bibliography links without title matching."""
        if not seeds:
            return {}
        deadline = asyncio.get_running_loop().time() + PRIMARY_REFERENCE_SECONDS
        recovered = {
            index: {
                "papers": [],
                "errors": [],
                "coverage": {
                    "source": "primary_arxiv",
                    "returned": 0,
                    "total": 0,
                    "inspected": 0,
                    "identified": 0,
                    "unidentified": 0,
                    "ambiguous": 0,
                    "unresolved": 0,
                    "truncated": 0,
                    "metadata_truncated": 0,
                },
                "evidence": [],
            }
            for index, _ in seeds
        }
        issues = {index: {"ambiguous": [], "unresolved": []} for index, _ in seeds}

        def compact(message: str) -> str:
            return message if len(message) <= 160 else message[:140].rstrip() + "... [truncated]"

        def report(index: int, category: str, detail: str, count: int = 1):
            recovered[index]["coverage"][category] += count
            examples = issues[index][category]
            example = compact(detail)
            if example not in examples and len(examples) < 3:
                examples.append(example)

        urls = {index: "https://arxiv.org/html/" + paper["arxiv"] for index, paper in seeds}
        tasks = {
            asyncio.create_task(self.web.fetch(url, ttl=86400 * 30, max_bytes=5_000_000)): index
            for index, url in urls.items()
        }
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
                return_when=asyncio.ALL_COMPLETED,
            )
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        items_by_seed: dict[int, list[dict]] = {index: [] for index, _ in seeds}
        for task in done:
            index = tasks[task]
            try:
                html = task.result()
            except (RuntimeError, ValueError, TimeoutError) as error:
                recovered[index]["errors"].append(_provider_error("arXiv primary", error))
                continue
            items = BeautifulSoup(html, "html.parser").select("li.ltx_bibitem")
            if not items:
                recovered[index]["errors"].append(
                    f"arXiv primary: no bibliography items at {urls[index]}"
                )
                continue
            inspected = items[:PRIMARY_REFERENCE_LIMIT]
            coverage = recovered[index]["coverage"]
            coverage["total"] = len(items)
            coverage["inspected"] = len(inspected)
            coverage["truncated"] = len(items) - len(inspected)
            if len(items) > PRIMARY_REFERENCE_LIMIT:
                inspected_count = f"{PRIMARY_REFERENCE_LIMIT}/{len(items)}"
                recovered[index]["errors"].append(
                    f"arXiv primary: inspected {inspected_count} bibliography items"
                )
            for item in inspected:
                found = []
                for link in item.select("a[href]"):
                    try:
                        paper_id = identifier(urljoin(urls[index], link["href"]).split("#", 1)[0])
                    except ValueError:
                        continue
                    if paper_id.startswith("ARXIV:"):
                        paper_id = "ARXIV:" + arxiv_base(paper_id[6:])
                    if paper_id.startswith(("ARXIV:", "DOI:")):
                        found.append(paper_id)
                found = list(dict.fromkeys(found))
                if not found:
                    coverage["unidentified"] += 1
                    continue
                coverage["identified"] += 1
                fragment = item.get("id")
                if not fragment:
                    report(
                        index,
                        "ambiguous",
                        "identified bibliography item without a source fragment",
                    )
                    continue
                arxiv_ids = [paper_id for paper_id in found if paper_id.startswith("ARXIV:")]
                doi_ids = [paper_id for paper_id in found if paper_id.startswith("DOI:")]
                if len(arxiv_ids) > 1 or len(doi_ids) > 1:
                    report(index, "ambiguous", f"{urls[index]}#{fragment}")
                    continue
                items_by_seed[index].append(
                    {
                        "ids": found,
                        "url": urls[index] + "#" + quote(str(fragment), safe="._-:"),
                    }
                )
        for task in pending:
            index = tasks[task]
            recovered[index]["errors"].append("arXiv primary: bibliography lookup timed out")

        unresolved_by_seed: dict[int, list[str]] = {}
        for index, items in items_by_seed.items():
            unresolved_by_seed[index] = list(
                dict.fromkeys(
                    paper_id[6:]
                    for item in items
                    for paper_id in item["ids"]
                    if paper_id.startswith("ARXIV:") and not self.cached(paper_id)
                )
            )
        selected, selected_set, position = [], set(), 0
        while len(selected) < PRIMARY_HYDRATION_LIMIT:
            had_candidate = False
            for index, _ in seeds:
                queue = unresolved_by_seed.get(index, [])
                if position >= len(queue):
                    continue
                had_candidate = True
                arxiv_id = queue[position]
                if arxiv_id not in selected_set:
                    selected.append(arxiv_id)
                    selected_set.add(arxiv_id)
                    if len(selected) == PRIMARY_HYDRATION_LIMIT:
                        break
            if not had_candidate:
                break
            position += 1
        for index, queue in unresolved_by_seed.items():
            omitted = len({paper_id for paper_id in queue if paper_id not in selected_set})
            if omitted:
                recovered[index]["coverage"]["metadata_truncated"] = omitted
                recovered[index]["errors"].append(
                    f"arXiv primary: {omitted} uncached arXiv reference(s) exceed metadata cap"
                )

        if selected:
            try:
                hydrated = await asyncio.wait_for(
                    self._arxiv_batch(selected),
                    max(0, deadline - asyncio.get_running_loop().time()),
                )
            except (RuntimeError, ValueError, TimeoutError, ET.ParseError) as error:
                message = _provider_error("arXiv metadata", error)
                for index, queue in unresolved_by_seed.items():
                    if any(paper_id in selected_set for paper_id in queue):
                        recovered[index]["errors"].append(message)
            else:
                for paper in hydrated:
                    self.remember(paper)

        for index, items in items_by_seed.items():
            seen = set()
            for item in items:
                known = {
                    paper["id"]: paper
                    for paper_id in item["ids"]
                    if (paper := self.cached(paper_id))
                }
                if len(known) > 1:
                    report(index, "ambiguous", item["url"])
                    continue
                if not known:
                    if dois := [value for value in item["ids"] if value.startswith("DOI:")]:
                        report(index, "unresolved", ", ".join(dois))
                    else:
                        report(index, "unresolved", item["url"])
                    continue
                paper = next(iter(known.values()))
                dois = [value for value in item["ids"] if value.startswith("DOI:")]
                if paper["doi"] and dois and "DOI:" + paper["doi"] not in dois:
                    report(index, "ambiguous", item["url"])
                    continue
                unresolved_dois = [value for value in dois if not self.cached(value)]
                if unresolved_dois:
                    report(index, "unresolved", ", ".join(unresolved_dois))
                if paper["id"] in seen:
                    continue
                seen.add(paper["id"])
                matched_id = next(
                    paper_id
                    for paper_id in item["ids"]
                    if (matched := self.cached(paper_id)) and matched["id"] == paper["id"]
                )
                recovered[index]["papers"].append(paper)
                recovered[index]["evidence"].append(
                    {
                        "cited": paper["id"],
                        "matched_id": matched_id,
                        "source": "primary_arxiv",
                        "url": item["url"],
                    }
                )
            recovered[index]["coverage"]["returned"] = len(recovered[index]["papers"])
            for category, label in (
                ("ambiguous", "ambiguous bibliography item(s)"),
                ("unresolved", "unresolved identifier/item(s)"),
            ):
                if examples := issues[index][category]:
                    count = recovered[index]["coverage"][category]
                    recovered[index]["errors"].append(
                        compact(f"arXiv primary: {count} {label}; examples: " + ", ".join(examples))
                    )
        return recovered

    async def _openalex_references(self, paper: dict) -> list[dict]:
        refs = [ref[3:] for ref in paper["refs"] if ref.startswith("oa:")]
        if not refs:
            oa_ids, identity_errors = await self._openalex_identity(paper)
            if not oa_ids:
                if identity_errors:
                    raise RuntimeError("; ".join(str(error) for error in identity_errors))
                raise ValueError("OpenAlex has no identifier for this paper")
            for oa_id in oa_ids:
                raw = await self.web.json(f"{OA}/{oa_id}?select=referenced_works")
                if not isinstance(raw.get("referenced_works"), list):
                    raise ValueError("OpenAlex reference list unavailable")
                refs += [ref.rsplit("/", 1)[-1] for ref in raw["referenced_works"]]
        batches = [refs[i : i + 100] for i in range(0, min(len(refs), 300), 100)]
        pages = await asyncio.gather(
            *(
                self.web.json(
                    f"{OA}?"
                    + urlencode(
                        {
                            "filter": "openalex:" + "|".join(batch),
                            "select": OA_FIELDS,
                            "per_page": 100,
                        }
                    )
                )
                for batch in batches
            )
        )
        return [from_openalex(raw) for page in pages for raw in page["results"]]

    async def citations(self, paper: dict, since: str) -> tuple[list[dict], list[str]]:
        """Citing papers: the newest from Semantic Scholar, the most cited overall and since
        `since` from OpenAlex. Returns papers and per-provider errors; one provider may fail."""
        jobs = []
        if ref := s2_ref(paper):
            jobs.append(
                (
                    "Semantic Scholar",
                    asyncio.wait_for(
                        self.web.json(f"{S2}/paper/{ref}/citations?fields={S2_FIELDS}&limit=1000"),
                        CITATION_PROVIDER_SECONDS,
                    ),
                )
            )
        jobs.append(
            (
                "OpenAlex",
                asyncio.wait_for(self._openalex_citations(paper, since), CITATION_PROVIDER_SECONDS),
            )
        )
        results = await asyncio.gather(*(call for _, call in jobs), return_exceptions=True)
        found, errors, succeeded = [], [], False
        for (provider, _), result in zip(jobs, results, strict=True):
            if isinstance(result, Exception):
                errors.append(_provider_error(provider, result))
            elif provider == "OpenAlex":
                oa_found, oa_errors, oa_succeeded = result
                found += oa_found
                errors += [_provider_error(provider, error) for error in oa_errors]
                succeeded |= oa_succeeded
            else:
                succeeded = True
                found += [
                    from_s2(r["citingPaper"])
                    for r in result.get("data") or []
                    if (r.get("citingPaper") or {}).get("paperId")
                ]
        if not found and errors and not succeeded:
            raise RuntimeError("; ".join(errors))
        return found, errors

    async def _openalex_citations(
        self, paper: dict, since: str
    ) -> tuple[list[dict], list[Exception], bool]:
        oa_ids, errors = await self._openalex_identity(paper)
        oa_jobs = []
        for extra in ("", ",from_publication_date:" + since):
            if oa_ids:
                params = {
                    "filter": "cites:" + "|".join(oa_ids) + extra,
                    "select": OA_FIELDS,
                    "sort": "cited_by_count:desc",
                    "per_page": 50,
                }
                oa_jobs.append(self.web.json(f"{OA}?{urlencode(params)}"))
        found, succeeded = [], False
        for result in await asyncio.gather(*oa_jobs, return_exceptions=True):
            if isinstance(result, Exception):
                errors.append(result)
            else:
                succeeded = True
                found += [from_openalex(raw) for raw in result["results"]]
        return found, errors, succeeded

    async def similar(self, paper: dict) -> list[dict]:
        """Semantic Scholar's content-based recommendations across computer science. They
        surface prominent follow-ups that a newest-first citation list misses."""
        if not (ref := s2_ref(paper)):
            return []
        data = await self.web.json(
            f"https://api.semanticscholar.org/recommendations/v1/papers/forpaper/{ref}"
            f"?from=all-cs&limit=60&fields={S2_FIELDS}"
        )
        return [from_s2(raw) for raw in data.get("recommendedPapers") or [] if raw.get("paperId")]

    async def _openalex_ids(self, paper: dict) -> list[str]:
        """OpenAlex often splits a paper into preprint and publication works; cite both."""
        ids, _ = await self._openalex_identity(paper)
        return ids

    async def _openalex_identity(self, paper: dict) -> tuple[list[str], list[Exception]]:
        ids = {paper["oa"]} if paper["oa"] else set()
        dois = [
            d
            for d in (
                paper["doi"],
                arxiv_base(paper["arxiv"]) and "10.48550/arxiv." + arxiv_base(paper["arxiv"]),
            )
            if d
        ]
        results = await asyncio.gather(
            *(self.web.json(f"{OA}/doi:{doi}?select=id") for doi in dois),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        ids |= {result["id"].rsplit("/", 1)[-1] for result in results if isinstance(result, dict)}
        return sorted(ids), errors

    async def with_references(self, papers: list[dict]) -> list[dict]:
        """Attach Semantic Scholar reference IDs to up to 500 papers in one request."""
        wanted = [p for p in papers if s2_ref(p) and not p["refs"]][:500]
        if not wanted:
            return papers
        rows = await self.web.json(
            f"{S2}/paper/batch?fields=references.paperId", body={"ids": [s2_ref(p) for p in wanted]}
        )
        refs = {p["id"]: from_s2(row)["refs"] for p, row in zip(wanted, rows, strict=False) if row}
        return [p | {"refs": refs.get(p["id"], p["refs"])} for p in papers]


def s2_ref(paper: dict) -> str:
    """How Semantic Scholar addresses a paper even when we never saw its S2 record."""
    if paper["s2"]:
        return paper["s2"]
    if paper["arxiv"]:
        return "arXiv:" + arxiv_base(paper["arxiv"])
    return "DOI:" + paper["doi"] if paper["doi"] else ""


def _matches(paper: dict, value: str) -> bool:
    prefix, tail = value.split(":", 1)
    name = PREFIXES[prefix]
    if name == "arxiv":
        return arxiv_base(paper["arxiv"]) == arxiv_base(tail)
    return paper[name].lower() == tail.lower()
