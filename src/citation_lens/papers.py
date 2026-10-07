"""Two citation providers, one small paper record. Provider edges are never inferred."""

import asyncio
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

from .network import Web

OA = "https://api.openalex.org/works"
S2 = "https://api.semanticscholar.org/graph/v1/paper"
OA_FIELDS = (
    "id,doi,title,publication_year,publication_date,cited_by_count,referenced_works,"
    "abstract_inverted_index,primary_location,best_oa_location,ids"
)
S2_FIELDS = (
    "title,year,publicationDate,abstract,citationCount,referenceCount,externalIds,openAccessPdf,url"
)


def identifier(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\.pdf$", "", value)
    value = re.sub(r"^https?://(?:www\.)?arxiv.org/(?:abs|pdf)/", "ARXIV:", value)
    value = re.sub(r"^https?://doi.org/", "DOI:", value)
    value = re.sub(r"^https?://openalex.org/", "OA:", value)
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", value):
        value = "ARXIV:" + value
    if re.fullmatch(r"W\d+", value):
        value = "OA:" + value
    if re.fullmatch(r"10\.\d{4,9}/\S+", value):
        value = "DOI:" + value
    if re.fullmatch(r"[a-f0-9]{40}", value):
        value = "S2:" + value
    if not re.fullmatch(
        r"(?:OA:W\d+|S2:[a-f0-9]{40}|DOI:10\.\d{4,9}/[^\s?#]+|"
        r"ARXIV:(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?)",
        value,
    ):
        raise ValueError("Use an OA:W..., S2:hash, DOI:..., or ARXIV:... paper identifier")
    return value


def normalize(raw: dict, provider: str) -> dict:
    if provider == "openalex":
        positions = raw.get("abstract_inverted_index") or {}
        abstract = " ".join(
            w for _, w in sorted((i, w) for w, ids in positions.items() for i in ids)
        )
        location = raw.get("best_oa_location") or raw.get("primary_location") or {}
        doi = (raw.get("doi") or "").removeprefix("https://doi.org/").lower()
        paper = {
            "id": "OA:" + raw["id"].rsplit("/", 1)[-1],
            "provider": provider,
            "title": raw.get("title") or "Untitled",
            "year": raw.get("publication_year"),
            "date": raw.get("publication_date"),
            "citations": raw.get("cited_by_count") or 0,
            "abstract": abstract,
            "doi": doi,
            "arxiv": "",
            "references": raw.get("referenced_works") or [],
            "pdf": location.get("pdf_url"),
            "url": location.get("landing_page_url") or raw["id"],
        }
        for loc in (raw.get("primary_location") or {}, location):
            match = re.search(
                r"arxiv.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)",
                loc.get("landing_page_url") or loc.get("pdf_url") or "",
            )
            if match:
                paper["arxiv"] = match[1]
    else:
        ext = raw.get("externalIds") or {}
        paper = {
            "id": "S2:" + raw["paperId"],
            "provider": provider,
            "title": raw.get("title") or "Untitled",
            "year": raw.get("year"),
            "date": raw.get("publicationDate"),
            "citations": raw.get("citationCount") or 0,
            "abstract": raw.get("abstract") or "",
            "doi": (ext.get("DOI") or "").lower(),
            "arxiv": ext.get("ArXiv") or "",
            "reference_count": raw.get("referenceCount"),
            "pdf": (raw.get("openAccessPdf") or {}).get("url"),
            "url": raw.get("url"),
        }
    return paper


def aliases(paper: dict) -> set[str]:
    result = set(paper.get("aliases", [])) | {paper["id"]}
    if paper.get("doi"):
        result.add("DOI:" + paper["doi"].lower())
    if paper.get("arxiv"):
        result.add("ARXIV:" + re.sub(r"v\d+$", "", paper["arxiv"]))
    return result


class Papers:
    def __init__(self, web: Web):
        self.web = web
        self.store = web.store

    def remember(self, paper: dict):
        self.store.save("paper:" + paper["id"], paper, ttl=86400 * 7)
        for alias in aliases(paper):
            self.store.save("alias:" + alias, paper["id"], ttl=86400 * 7)
        return paper

    async def resolve(self, value: str):
        value = identifier(value)
        canonical = self.store.load("alias:" + value) or value
        if paper := self.store.load("paper:" + canonical):
            return paper
        if value.startswith(("OA:", "DOI:")):
            tail = value[3:] if value.startswith("OA:") else "https://doi.org/" + value[4:]
            raw = await self.web.json(
                OA + "/" + quote(tail, safe="") + "?" + urlencode({"select": OA_FIELDS})
            )
            paper = normalize(raw, "openalex")
        else:
            tail = value[3:] if value.startswith("S2:") else value
            raw = await self.web.json(
                S2 + "/" + quote(tail, safe="") + "?" + urlencode({"fields": S2_FIELDS})
            )
            paper = normalize(raw, "semantic_scholar")
        self.store.save("alias:" + value, paper["id"], ttl=86400 * 7)
        return self.remember(paper)

    async def search(self, query: str, provider: str, limit: int, recent: bool, year: int | None):
        if provider == "arxiv":
            url = "https://export.arxiv.org/api/query?" + urlencode(
                {
                    "search_query": (
                        "all:"
                        + query
                        + (f" AND submittedDate:[{year}01010000 TO 210012312359]" if year else "")
                    ),
                    "start": 0,
                    "max_results": limit,
                    "sortBy": "submittedDate" if recent else "relevance",
                    "sortOrder": "descending",
                }
            )
            data = ET.fromstring(await self.web.fetch(url, max_bytes=2_000_000))
            ns = {
                "a": "http://www.w3.org/2005/Atom",
                "o": "http://a9.com/-/spec/opensearch/1.1/",
                "x": "http://arxiv.org/schemas/atom",
            }
            found = []
            for entry in data.findall("a:entry", ns):
                url = entry.findtext("a:id", "", ns).replace("http:", "https:")
                if "/abs/" not in url:
                    raise ValueError("arXiv returned an error rather than paper records")
                arxiv = url.split("/abs/")[1]
                date = entry.findtext("a:published", "", ns)
                if year and int(date[:4]) < year:
                    continue
                p = {
                    "id": "ARXIV:" + arxiv,
                    "provider": "arxiv",
                    "arxiv": arxiv,
                    "title": " ".join(entry.findtext("a:title", "", ns).split()),
                    "abstract": " ".join(entry.findtext("a:summary", "", ns).split()),
                    "year": int(date[:4]),
                    "date": date[:10],
                    "citations": 0,
                    "doi": entry.findtext("x:doi", "", ns).lower(),
                    "pdf": "https://arxiv.org/pdf/" + arxiv,
                    "url": url,
                    "citation_status": "not yet resolved in citation index",
                }
                found.append(self.remember(p))
            return found, int(data.findtext("o:totalResults", "0", ns))
        if provider == "openalex":
            params = {"search": query, "select": OA_FIELDS, "per_page": limit}
            if recent:
                params["sort"] = "publication_date:desc"
            if year:
                params["filter"] = f"from_publication_date:{year}-01-01"
            data = await self.web.json(OA + "?" + urlencode(params))
            raw, total = data["results"], data["meta"]["count"]
        else:
            params = {"query": query, "fields": S2_FIELDS, "limit": limit}
            if year:
                params["year"] = f"{year}:"
            if recent:
                # Relevance search does not expose a date sort; use bulk retrieval explicitly.
                params.pop("limit")
                params["sort"] = "publicationDate:desc"
                path = "/search/bulk"
            else:
                path = "/search"
            data = await self.web.json(S2 + path + "?" + urlencode(params))
            raw, total = data.get("data", [])[:limit], data.get("total", 0)
        return [
            self.remember(normalize(p, provider)) for p in raw if p.get("id") or p.get("paperId")
        ], total

    async def neighbors(self, paper: dict, direction: str, limit: int):
        if paper["provider"] == "arxiv":
            raw = await self.web.json(
                S2 + "/" + quote(paper["id"], safe="") + "?" + urlencode({"fields": S2_FIELDS})
            )
            paper = self.remember(normalize(raw, "semantic_scholar"))
        if paper["provider"] == "openalex":
            if direction == "backward":
                refs = paper["references"]
                # ponytail: sample at most 200 refs; add cursor hydration for very large surveys.
                groups = [refs[i : i + 100] for i in range(0, min(len(refs), 200), 100)]
                pages = await asyncio.gather(
                    *[
                        self.web.json(
                            OA
                            + "?"
                            + urlencode(
                                {
                                    "filter": "openalex:"
                                    + "|".join(r.rsplit("/", 1)[-1] for r in group),
                                    "select": OA_FIELDS,
                                    "per_page": 100,
                                }
                            )
                        )
                        for group in groups
                    ]
                )
                raw = [r for page in pages for r in page["results"]]
                coverage = {
                    "indexed": len(refs),
                    "examined_ids": min(len(refs), 200),
                    "returned_records": len(raw),
                    "sampled": len(refs) > 200,
                    "ordering": "reference ID sample; selected by local heuristic",
                }
            else:
                params = {
                    "filter": "cites:" + paper["id"][3:],
                    "select": OA_FIELDS,
                    "per_page": limit,
                }
                pages = await asyncio.gather(
                    *[
                        self.web.json(OA + "?" + urlencode({**params, "sort": sort}))
                        for sort in ("cited_by_count:desc", "publication_date:desc")
                    ]
                )
                raw = [r for page in pages for r in page["results"]]
                coverage = {
                    "indexed": pages[0]["meta"]["count"],
                    "sampled": True,
                    "ordering": "union of prominent and recent pages",
                }
            candidates = [normalize(r, "openalex") for r in raw]
        else:
            endpoint = "references" if direction == "backward" else "citations"
            data = await self.web.json(
                S2
                + "/"
                + paper["id"][3:]
                + "/"
                + endpoint
                + "?"
                + urlencode(
                    {
                        "fields": S2_FIELDS + ",isInfluential,intents",
                        "limit": min(limit * 3, 100),
                        "offset": 0,
                    }
                )
            )
            key = "citedPaper" if direction == "backward" else "citingPaper"
            candidates = []
            for row in data.get("data", []):
                if (raw := row.get(key)) and raw.get("paperId"):
                    p = normalize(raw, "semantic_scholar")
                    p["edge_evidence"] = {
                        "isInfluential": row.get("isInfluential"),
                        "intents": (row.get("intents") or [])[:3],
                    }
                    candidates.append(p)
            coverage = {
                "sampled": "next" in data,
                "next_offset": data.get("next"),
                "ordering": "provider page, not globally most cited",
            }
        unique = {p["id"]: self.remember(p) for p in candidates}
        coverage["retrieved_unique"] = len(unique)
        return list(unique.values()), coverage
