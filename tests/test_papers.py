import asyncio
import json
from pathlib import Path

import httpx
import pytest
from conftest import run, s2

import citation_lens.papers as papers_module
from citation_lens.network import Web
from citation_lens.papers import (
    Papers,
    from_arxiv,
    from_openalex,
    from_s2,
    identifier,
    keys,
    merge,
    strict_terms,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2205.14135", "ARXIV:2205.14135"),
        ("https://arxiv.org/abs/2205.14135v2", "ARXIV:2205.14135v2"),
        ("https://arxiv.org/pdf/2205.14135.pdf", "ARXIV:2205.14135"),
        ("arXiv:hep-th/9901001", "ARXIV:hep-th/9901001"),
        ("10.48550/arXiv.2205.14135", "ARXIV:2205.14135"),
        ("https://doi.org/10.1038/S41560-019-0356-8", "DOI:10.1038/s41560-019-0356-8"),
        ("W4281758439", "OA:W4281758439"),
        ("https://openalex.org/W1", "OA:W1"),
        ("87c5b281fa43e6f27191b20a8dd694eda1126336", "S2:87c5b281fa43e6f27191b20a8dd694eda1126336"),
    ],
)
def test_identifier(value, expected):
    assert identifier(value) == expected


@pytest.mark.parametrize("value", ["", "attention", "https://example.com/paper", "../etc/passwd"])
def test_identifier_rejects_non_identifiers(value):
    with pytest.raises(ValueError):
        identifier(value)


def test_openalex_record_rebuilds_abstract_and_arxiv_identity():
    paper = from_openalex(
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.48550/arxiv.2205.14135",
            "title": "FlashAttention",
            "publication_date": "2022-05-27",
            "cited_by_count": 458,
            "abstract_inverted_index": {"Exact": [0], "attention.": [1]},
            "referenced_works": ["https://openalex.org/W2"],
        }
    )
    assert paper["id"] == "ARXIV:2205.14135" and paper["doi"] == ""
    assert paper["abstract"] == "Exact attention." and paper["refs"] == ["oa:W2"]
    assert paper["abstract_source"] == "openalex"
    assert paper["url"] == "https://arxiv.org/abs/2205.14135" and paper["year"] == 2022


def test_provider_whitespace_is_not_treated_as_abstract_evidence():
    paper = from_s2(
        {
            "paperId": "a" * 40,
            "title": "Paper awaiting evidence",
            "abstract": " \n\t ",
            "externalIds": {},
        }
    )
    assert paper["abstract"] == "" and paper["abstract_source"] is None


def test_arxiv_feed_parsing_skips_error_entries():
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
      <opensearch:totalResults>7</opensearch:totalResults>
      <entry><id>http://arxiv.org/abs/2401.00001v2</id><title>New
      Attention</title><summary> Fresh   preprint. </summary>
      <published>2026-05-01T00:00:00Z</published><author><name>A. Author</name></author>
      <arxiv:doi> HTTPS://DOI.ORG/10.1234/Published-Version </arxiv:doi></entry>
      <entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title></entry>
    </feed>"""
    papers, total = from_arxiv(feed)
    assert total == 7 and len(papers) == 1
    assert papers[0]["id"] == "ARXIV:2401.00001" and papers[0]["date"] == "2026-05-01"
    assert papers[0]["title"] == "New Attention" and papers[0]["abstract"] == "Fresh preprint."
    assert papers[0]["abstract_source"] == "arxiv"
    assert papers[0]["doi"] == "10.1234/published-version"
    assert "doi:10.1234/published-version" in keys(papers[0])
    publication = from_openalex(
        {
            "id": "https://openalex.org/W9",
            "title": "Published New Attention",
            "doi": "https://doi.org/10.1234/published-version",
        }
    )
    merged, owners = merge([papers[0], publication])
    assert len(merged) == 1 and owners == ["ARXIV:2401.00001", "ARXIV:2401.00001"]
    assert merged[0]["oa"] == "W9" and merged[0]["doi"] == "10.1234/published-version"


def test_merge_joins_versions_and_maps_every_input():
    preprint = s2("a", "Exact Attention With Tiling", 2022, 3000, "From S2.", arxiv="2205.14135")
    indexed = from_openalex(
        {
            "id": "https://openalex.org/W9",
            "title": "Exact attention with tiling",
            "doi": "10.1000/published",
            "ids": {"arxiv": "https://arxiv.org/abs/2205.14135"},
            "cited_by_count": 500,
            "publication_year": 2023,
        }
    )
    other = s2("b", "A Different Paper Entirely Here", 2021)
    papers, owners = merge([preprint, other, indexed])
    assert len(papers) == 2 and owners == ["ARXIV:2205.14135", other["id"], "ARXIV:2205.14135"]
    joined = papers[0]
    assert joined["citations"] == 3000 and joined["oa"] == "W9" and joined["year"] == 2022
    assert joined["abstract_source"] == "semantic_scholar"
    assert {"oa:W9", "doi:10.1000/published", "arxiv:2205.14135"} <= keys(joined)


def test_merge_never_joins_distinct_arxiv_papers_by_title():
    first = s2("a", "Attention Is All You Need Again", arxiv="2101.00001")
    second = s2("b", "Attention Is All You Need Again", arxiv="2102.00002")
    papers, owners = merge([first, second])
    assert len(papers) == 2 and owners == ["ARXIV:2101.00001", "ARXIV:2102.00002"]


def test_merge_rejects_title_only_match_with_conflicting_identity():
    def record(key, doi, author, year):
        return from_s2(
            {
                "paperId": key * 40,
                "title": "A Shared Long Exact Paper Title",
                "year": year,
                "authors": [{"name": author}],
                "externalIds": {"DOI": doi},
            }
        )

    first = record("a", "10.1000/first", "Alice Author", 2024)
    second = record("b", "10.1000/second", "Bob Writer", 2023)
    papers, owners = merge([first, second])
    assert len(papers) == 2
    assert owners == ["DOI:10.1000/first", "DOI:10.1000/second"]


def test_title_only_record_does_not_bridge_incompatible_groups():
    def record(key, doi, author):
        return from_s2(
            {
                "paperId": key * 40,
                "title": "A Shared Long Exact Paper Title",
                "year": 2024,
                "authors": [{"name": author}],
                "externalIds": {"DOI": doi},
            }
        )

    first = record("a", "10.1000/first", "Alice Author")
    second = record("b", "10.1000/second", "Bob Writer")
    bridge = from_openalex(
        {
            "id": "https://openalex.org/W3",
            "title": "A Shared Long Exact Paper Title",
            "publication_year": 2024,
        }
    )
    papers, owners = merge([first, second, bridge])
    assert len(papers) == 2
    assert owners[0] != owners[1]


def test_merge_accepts_compatible_title_only_match():
    semantic_scholar = from_s2(
        {
            "paperId": "a" * 40,
            "title": "A Shared Long Exact Paper Title",
            "year": 2024,
            "authors": [{"name": "A. Researcher"}, {"name": "B. Writer"}],
            "externalIds": {},
        }
    )
    openalex = from_openalex(
        {
            "id": "https://openalex.org/W1",
            "title": "A Shared Long Exact Paper Title",
            "publication_year": 2024,
            "authorships": [
                {"author": {"display_name": "Alice Researcher"}},
                {"author": {"display_name": "Bob Writer"}},
            ],
        }
    )
    papers, owners = merge([semantic_scholar, openalex])
    assert len(papers) == 1
    assert owners == [semantic_scholar["id"], semantic_scholar["id"]]


def test_merge_keeps_shared_stable_id_authoritative():
    first = from_s2(
        {
            "paperId": "a" * 40,
            "title": "Preprint Title",
            "year": 2022,
            "authors": [{"name": "Alice Author"}],
            "externalIds": {"DOI": "10.1000/shared"},
        }
    )
    publication = from_openalex(
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/shared",
            "title": "Changed Publication Title",
            "publication_year": 2024,
            "authorships": [{"author": {"display_name": "Bob Writer"}}],
        }
    )
    papers, owners = merge([first, publication])
    assert len(papers) == 1
    assert owners == ["DOI:10.1000/shared", "DOI:10.1000/shared"]


def test_merge_tracks_the_provider_of_the_selected_abstract():
    without_abstract = from_s2(
        {
            "paperId": "a" * 40,
            "title": "One Paper With Provider Metadata",
            "abstract": None,
            "externalIds": {"DOI": "10.1000/shared"},
        }
    )
    with_abstract = from_openalex(
        {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1000/shared",
            "title": "One Paper With Provider Metadata",
            "abstract_inverted_index": {"OpenAlex": [0], "abstract.": [1]},
        }
    )
    paper = merge([without_abstract, with_abstract])[0][0]
    assert paper["abstract"] == "OpenAlex abstract."
    assert paper["abstract_source"] == "openalex"


def test_strict_providers_remove_stopwords_and_keep_phrases():
    assert strict_terms("the exact attention for memory and online softmax") == [
        "exact",
        "attention",
        "memory",
        "online",
        "softmax",
    ]
    assert strict_terms('"online softmax" normalizer') == ['"online softmax"', "normalizer"]


def test_strict_provider_search_keeps_all_terms_phrases_and_one_request_per_query(
    store, monkeypatch
):
    requests = []
    empty_feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>0</opensearch:totalResults></feed>"""

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        requests.append((request.headers["host"], dict(request.url.params)))
        if request.headers["host"] == "api.openalex.org":
            return httpx.Response(200, json={"results": [], "meta": {"count": 0}})
        return httpx.Response(200, content=empty_feed)

    async def exercise():
        search = provider(store, respond)
        long_query = '"adaptive state" routing for sparse sequence recall'
        short_query = "routing and recall"
        assert await search.search(long_query, "openalex", 25) == ([], 0)
        assert await search.search(long_query, "arxiv", 25) == ([], 0)
        assert await search.search(short_query, "openalex", 25) == ([], 0)
        search.web.next_at["export.arxiv.org"] = -1e9
        assert await search.search(short_query, "arxiv", 25) == ([], 0)
        await search.web.close()

    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())

    assert len(requests) == 4
    assert requests[0][1]["search.title_and_abstract"] == (
        '"adaptive state" routing sparse sequence recall'
    )
    assert requests[1][1]["search_query"] == (
        '((ti:"adaptive state" OR abs:"adaptive state") AND '
        '(ti:"routing" OR abs:"routing") AND (ti:"sparse" OR abs:"sparse") AND '
        '(ti:"sequence" OR abs:"sequence") AND (ti:"recall" OR abs:"recall"))'
    )
    assert requests[2][1]["search.title_and_abstract"] == "routing recall"
    assert requests[3][1]["search_query"] == (
        '((ti:"routing" OR abs:"routing") AND (ti:"recall" OR abs:"recall"))'
    )


def test_search_provider_failure_remains_explicit_without_fallback(store):
    request_count = 0

    async def respond(_request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(404)

    async def exercise():
        search = provider(store, respond)
        with pytest.raises(RuntimeError, match="api.openalex.org: HTTP 404"):
            await search.search("routing recall", "openalex", 25)
        await search.web.close()

    run(exercise())
    assert request_count == 1


def test_a_conflicting_record_cannot_steal_a_shared_title():
    first = s2("a", "One Title Shared By Two Papers", arxiv="1111.1111")
    clash = s2("b", "One Title Shared By Two Papers", arxiv="2222.2222")
    again = s2("c", "One Title Shared By Two Papers", arxiv="1111.1111")
    papers, owners = merge([first, clash, again])
    assert len({p["id"] for p in papers}) == len(papers) == 2
    assert owners == ["ARXIV:1111.1111", "ARXIV:2222.2222", "ARXIV:1111.1111"]


def provider(store, respond):
    """Real Papers over a mock transport; S2 pacing is skipped so tests stay fast."""
    web = Web(store, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    web.next_at = {"api.semanticscholar.org": -1e9, "export.arxiv.org": -1e9}
    return Papers(web)


def test_batched_references_map_back_by_input_order_and_fall_back(store):
    def s2_raw(key, title):
        return {"paperId": key.ljust(40, "0"), "title": title, "externalIds": {}}

    async def respond(request):
        if request.url.path.endswith("/paper/batch"):
            ids = json.loads(request.content)["ids"]
            assert ids == ["arXiv:2205.14135", "arXiv:2112.05682"]
            rows = [{"references": [s2_raw("f1", "Online Softmax")]}, None]
            return httpx.Response(200, json=rows)  # the second paper is unknown to S2
        if "api.openalex.org" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(429)  # arXiv throttled

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        a = s2("x", "Seed A", arxiv="2205.14135") | {"s2": ""}
        b = s2("y", "Seed B", arxiv="2112.05682") | {"s2": ""}
        refs_a, refs_b = await papers.references([a, b])
        assert [p["title"] for p in refs_a["papers"]] == ["Online Softmax"]
        assert refs_a["coverage"] == {"source": "semantic_scholar", "returned": 1}
        assert any("openalex" in error.casefold() for error in refs_b["errors"])
        await papers.web.close()

    run(exercise())


def test_references_preserve_an_authoritative_empty_s2_result(store):
    openalex_requests = 0

    async def respond(request):
        nonlocal openalex_requests
        if request.url.path.endswith("/paper/batch"):
            return httpx.Response(200, json=[{"references": []}])
        openalex_requests += 1
        return httpx.Response(500)

    async def exercise():
        papers = provider(store, respond)
        seed = s2("a", "Seed Paper")
        result = (await papers.references([seed]))[0]
        assert result == {
            "papers": [],
            "errors": [],
            "coverage": {"source": "semantic_scholar", "returned": 0},
            "evidence": [],
        }
        assert openalex_requests == 0
        await papers.web.close()

    run(exercise())


def test_non_arxiv_reference_lookup_uses_the_bounded_provider_stage(store, monkeypatch):
    async def respond(request):
        if request.url.path.endswith("/paper/batch"):
            await asyncio.sleep(0.05)
            return httpx.Response(200, json=[])
        return httpx.Response(503)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Journal seed",
                "externalIds": {"DOI": "10.1000/journal"},
            }
        )
        result = (await papers.references([seed]))[0]
        assert result["papers"] == []
        assert result["coverage"]["source"] == "unavailable"
        assert any("semantic scholar" in error.casefold() for error in result["errors"])
        await papers.web.close()

    monkeypatch.setattr(papers_module, "REFERENCE_PROVIDER_SECONDS", 0.01)
    run(exercise())


def test_mixed_healthy_seeds_share_one_semantic_scholar_batch(store):
    s2_requests = 0

    async def respond(request):
        nonlocal s2_requests
        assert request.url.path.endswith("/paper/batch")
        s2_requests += 1
        return httpx.Response(
            200,
            json=[
                {"references": [{"paperId": "c" * 40, "title": "arXiv reference"}]},
                {"references": [{"paperId": "d" * 40, "title": "Journal reference"}]},
            ],
        )

    async def exercise():
        papers = provider(store, respond)
        arxiv_seed = s2("a", "arXiv seed", arxiv="2205.14135")
        journal_seed = from_s2(
            {
                "paperId": "b" * 40,
                "title": "Journal seed",
                "externalIds": {"DOI": "10.1000/journal"},
            }
        )
        results = await papers.references([arxiv_seed, journal_seed])
        assert [result["coverage"]["source"] for result in results] == [
            "semantic_scholar",
            "semantic_scholar",
        ]
        assert [result["papers"][0]["title"] for result in results] == [
            "arXiv reference",
            "Journal reference",
        ]
        await papers.web.close()

    run(exercise())
    assert s2_requests == 1


def test_malformed_s2_reference_only_falls_back_for_its_seed(store):
    async def respond(request):
        if request.url.path.endswith("/paper/batch"):
            return httpx.Response(
                200,
                json=[
                    {"references": [{"paperId": "c" * 40, "title": "Valid S2 reference"}]},
                    {
                        "references": [
                            {
                                "paperId": "d" * 40,
                                "title": "Invalid S2 reference",
                                "publicationDate": "unknown",
                            }
                        ]
                    },
                ],
            )
        if request.url.path.endswith("/works/Wseed"):
            return httpx.Response(
                200, json={"referenced_works": ["https://openalex.org/Wfallback"]}
            )
        if request.url.path == "/works":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "https://openalex.org/Wfallback",
                            "title": "Fallback OpenAlex reference",
                            "publication_year": 2022,
                        }
                    ]
                },
            )
        raise AssertionError(str(request.url))

    async def exercise():
        papers = provider(store, respond)
        valid_seed = s2("a", "Valid seed")
        malformed_seed = s2("b", "Malformed seed") | {"refs": ["oa:Wseed"]}
        valid, recovered = await papers.references([valid_seed, malformed_seed])
        assert [paper["title"] for paper in valid["papers"]] == ["Valid S2 reference"]
        assert valid["coverage"]["source"] == "semantic_scholar"
        assert [paper["title"] for paper in recovered["papers"]] == ["Fallback OpenAlex reference"]
        assert recovered["coverage"]["source"] == "openalex"
        assert any("reference metadata" in error.casefold() for error in recovered["errors"])
        await papers.web.close()

    run(exercise())


def test_references_preserve_an_authoritative_empty_openalex_result(store):
    primary_requests = 0

    async def respond(request):
        nonlocal primary_requests
        if request.url.path.endswith("/paper/batch"):
            return httpx.Response(503)
        if request.url.path.endswith("/works/doi:10.48550/arxiv.2205.14135"):
            return httpx.Response(200, json={"id": "https://openalex.org/W1"})
        if request.url.path.endswith("/works/W1"):
            return httpx.Response(200, json={"referenced_works": []})
        if request.headers["host"] == "arxiv.org":
            primary_requests += 1
        raise AssertionError(str(request.url))

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Seed Paper",
                "externalIds": {"ArXiv": "2205.14135"},
            }
        )
        result = (await papers.references([seed]))[0]
        assert result["papers"] == [] and result["evidence"] == []
        assert result["coverage"] == {"source": "openalex", "returned": 0}
        assert primary_requests == 0
        await papers.web.close()

    run(exercise())


def test_references_return_openalex_identity_failure(store):
    async def respond(request):
        if request.url.path.endswith("/paper/batch"):
            return httpx.Response(200, json=[None])
        return httpx.Response(503)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Seed Paper",
                "externalIds": {"DOI": "10.1000/seed"},
            }
        )
        result = (await papers.references([seed]))[0]
        assert result["papers"] == []
        assert any("openalex" in error.casefold() for error in result["errors"])
        await papers.web.close()

    run(exercise())


def test_references_report_both_provider_failures(store):
    async def respond(request):
        return httpx.Response(503)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Seed Paper",
                "externalIds": {"DOI": "10.1000/seed"},
            }
        )
        result = (await papers.references([seed]))[0]
        message = "; ".join(result["errors"]).casefold()
        assert "semantic scholar" in message
        assert "openalex" in message
        await papers.web.close()

    run(exercise())


def test_references_recover_explicit_primary_arxiv_identifiers_after_index_outage(
    store, monkeypatch
):
    requests = []
    bibliography = (FIXTURES / "arxiv-bibliography.html").read_bytes()
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>1</opensearch:totalResults>
      <entry><id>http://arxiv.org/abs/2101.00001v4</id>
      <title>Hydrated primary reference</title><arxiv:doi>10.1000/alias</arxiv:doi>
      <published>2021-01-01T00:00:00Z</published></entry>
    </feed>"""

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        host = request.headers["host"]
        requests.append((host, request.url.path, dict(request.url.params)))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[{}])
        if host == "api.openalex.org":
            if request.url.path.endswith("/works/doi:10.48550/arxiv.2205.14135"):
                return httpx.Response(200, json={"id": "https://openalex.org/W1"})
            return httpx.Response(200, json={})  # missing referenced_works is unavailable
        if host == "arxiv.org":
            return httpx.Response(200, content=bibliography)
        if host == "export.arxiv.org":
            return httpx.Response(200, content=feed)
        raise AssertionError(str(request.url))

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        papers.remember(
            from_s2(
                {
                    "paperId": "c" * 40,
                    "title": "Cached DOI reference",
                    "externalIds": {"DOI": "10.1000/cached"},
                }
            )
        )
        seed = s2("a", "Versioned Seed", arxiv="2205.14135v3") | {"s2": ""}
        result = (await papers.references([seed]))[0]

        assert [paper["id"] for paper in result["papers"]] == [
            "ARXIV:2101.00001",
            "DOI:10.1000/cached",
        ]
        assert result["coverage"] == {
            "source": "primary_arxiv",
            "returned": 2,
            "total": 6,
            "inspected": 6,
            "identified": 5,
            "unidentified": 1,
            "ambiguous": 1,
            "unresolved": 1,
            "truncated": 0,
            "metadata_truncated": 0,
        }
        assert result["evidence"] == [
            {
                "cited": "ARXIV:2101.00001",
                "matched_id": "ARXIV:2101.00001",
                "source": "primary_arxiv",
                "url": "https://arxiv.org/html/2205.14135v3#bib.primary",
            },
            {
                "cited": "DOI:10.1000/cached",
                "matched_id": "DOI:10.1000/cached",
                "source": "primary_arxiv",
                "url": "https://arxiv.org/html/2205.14135v3#bib.cached-doi",
            },
        ]
        message = "; ".join(result["errors"])
        assert "ambiguous bibliography" in message
        assert "DOI:10.1000/unresolved" in message
        await papers.web.close()

    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())

    assert [host for host, _, _ in requests] == [
        "api.semanticscholar.org",
        "api.openalex.org",
        "api.openalex.org",
        "arxiv.org",
        "export.arxiv.org",
    ]
    assert requests[-1][2]["id_list"] == "2101.00001"


def test_primary_reference_fallback_reports_missing_bibliography_markup(store, monkeypatch):
    metadata_requests = 0

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        nonlocal metadata_requests
        host = request.headers["host"]
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[{}])
        if host == "api.openalex.org":
            return httpx.Response(503)
        if host == "arxiv.org":
            return httpx.Response(200, content=b"<html><p>malformed bibliography")
        metadata_requests += 1
        return httpx.Response(500)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = s2("a", "Seed", arxiv="2205.14135") | {"s2": ""}
        result = (await papers.references([seed]))[0]
        assert result["papers"] == [] and result["evidence"] == []
        assert any("no bibliography items" in error for error in result["errors"])
        await papers.web.close()

    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())
    assert metadata_requests == 0


def test_primary_reference_diagnostics_bound_long_identifiers_and_fragments(store, monkeypatch):
    long_suffix = "x" * 5000
    html = f"""
    <ol>
      <li class="ltx_bibitem" id="doi-item">
        <a href="https://doi.org/10.1000/{long_suffix}">DOI</a>
      </li>
      <li class="ltx_bibitem" id="{long_suffix}">
        <a href="https://arxiv.org/abs/2101.00001">first</a>
        <a href="https://arxiv.org/abs/2102.00002">second</a>
      </li>
    </ol>
    """.encode()

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        assert request.headers["host"] == "arxiv.org"
        return httpx.Response(200, content=html)

    async def exercise():
        papers = provider(store, respond)
        seed = s2("a", "Seed", arxiv="2205.14135")
        result = (await papers._primary_references([(0, seed)]))[0]
        assert result["coverage"]["unresolved"] == 1
        assert result["coverage"]["ambiguous"] == 1
        assert all(len(error) <= 160 for error in result["errors"])
        assert all("[truncated]" in error for error in result["errors"])
        await papers.web.close()

    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())


def test_primary_reference_caps_are_round_robin_across_seeds(store, monkeypatch):
    metadata_batches = []

    def bibliography(prefix):
        items = "".join(
            f'<li class="ltx_bibitem" id="bib.{number}">'
            f'<a href="https://arxiv.org/abs/{prefix}.{number:05d}">ref</a></li>'
            for number in range(1, 102)
        )
        return f"<html><ol>{items}</ol></html>".encode()

    def feed(ids):
        entries = "".join(
            f"<entry><id>http://arxiv.org/abs/{paper_id}</id><title>{paper_id}</title>"
            "<published>2020-01-01T00:00:00Z</published></entry>"
            for paper_id in ids
        )
        return (
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            f"<opensearch:totalResults>{len(ids)}</opensearch:totalResults>{entries}</feed>"
        ).encode()

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        host = request.headers["host"]
        if host == "api.semanticscholar.org":
            return httpx.Response(200, json=[{}, {}])
        if host == "api.openalex.org":
            return httpx.Response(503)
        if host == "arxiv.org":
            prefix = "2401" if request.url.path.endswith("2501.00001") else "2402"
            return httpx.Response(200, content=bibliography(prefix))
        ids = request.url.params["id_list"].split(",")
        metadata_batches.append(ids)
        return httpx.Response(200, content=feed(ids))

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seeds = [
            s2("a", "First Seed", arxiv="2501.00001") | {"s2": ""},
            s2("b", "Second Seed", arxiv="2502.00001") | {"s2": ""},
        ]
        results = await papers.references(seeds)
        assert [len(result["papers"]) for result in results] == [50, 50]
        assert all(result["coverage"]["inspected"] == 100 for result in results)
        assert all(
            any("inspected 100/101" in error for error in result["errors"]) for result in results
        )
        assert all(any("50 uncached" in error for error in result["errors"]) for result in results)
        assert all(len(result["errors"]) < 8 for result in results)
        await papers.web.close()

    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())

    assert len(metadata_batches) == 1 and len(metadata_batches[0]) == 100
    assert sum(paper_id.startswith("2401.") for paper_id in metadata_batches[0]) == 50
    assert sum(paper_id.startswith("2402.") for paper_id in metadata_batches[0]) == 50


def test_primary_metadata_timeout_keeps_cached_doi_references(store, monkeypatch):
    bibliography = (FIXTURES / "arxiv-bibliography.html").read_bytes()
    never_respond = asyncio.Event()

    async def public_ip(_url):
        return "93.184.216.34"

    async def respond(request):
        host = request.headers["host"]
        if host in {"api.semanticscholar.org", "api.openalex.org"}:
            return httpx.Response(503)
        if host == "arxiv.org":
            return httpx.Response(200, content=bibliography)
        await never_respond.wait()
        return httpx.Response(200)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        papers.remember(
            from_s2(
                {
                    "paperId": "c" * 40,
                    "title": "Cached DOI reference",
                    "externalIds": {"DOI": "10.1000/cached"},
                }
            )
        )
        seed = s2("a", "Seed", arxiv="2205.14135") | {"s2": ""}
        result = (await papers.references([seed]))[0]
        assert [paper["id"] for paper in result["papers"]] == ["DOI:10.1000/cached"]
        assert any("arxiv metadata" in error.casefold() for error in result["errors"])
        assert not papers.web.pending
        await papers.web.close()

    monkeypatch.setattr(papers_module, "PRIMARY_REFERENCE_SECONDS", 0.05)
    monkeypatch.setattr("citation_lens.network.public_url", public_ip)
    run(exercise())


def test_citations_keep_s2_results_and_report_openalex_deadline(store):
    async def respond(request):
        if "semanticscholar" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citingPaper": {
                                "paperId": "b" * 40,
                                "title": "Citing Paper",
                                "externalIds": {},
                            }
                        }
                    ]
                },
            )
        raise TimeoutError("OpenAlex identity deadline")

    async def exercise():
        papers = provider(store, respond)
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Seed Paper",
                "externalIds": {"DOI": "10.1000/seed"},
            }
        )
        found, errors = await papers.citations(seed, "2024-01-01")
        assert [paper["title"] for paper in found] == ["Citing Paper"]
        assert len(errors) == 1 and "openalex" in errors[0].casefold()
        assert "deadline" in errors[0].casefold()
        await papers.web.close()

    run(exercise())


def test_citations_finish_before_caller_deadline_when_openalex_hangs(store, monkeypatch):
    # Allow cold SQLite I/O and Windows timer resolution; the hung request remains blocked
    # indefinitely, so returning its timeout still requires the provider deadline to work.
    monkeypatch.setattr(papers_module, "CITATION_PROVIDER_SECONDS", 1.0)
    openalex_started = asyncio.Event()
    release_openalex = asyncio.Event()

    async def respond(request):
        if "semanticscholar" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citingPaper": {
                                "paperId": "b" * 40,
                                "title": "Healthy S2 Citation",
                                "externalIds": {},
                            }
                        }
                    ]
                },
            )
        openalex_started.set()
        await release_openalex.wait()
        return httpx.Response(200, json={"id": "https://openalex.org/W1"})

    async def exercise():
        papers = provider(store, respond)
        seed = from_s2(
            {
                "paperId": "a" * 40,
                "title": "Seed Paper",
                "externalIds": {"DOI": "10.1000/seed"},
            }
        )
        try:
            found, errors = await asyncio.wait_for(papers.citations(seed, "2024-01-01"), 5.0)
            assert openalex_started.is_set()
            assert [paper["title"] for paper in found] == ["Healthy S2 Citation"]
            assert len(errors) == 1 and "openalex" in errors[0].casefold()
            assert "timeout" in errors[0].casefold()
        finally:
            release_openalex.set()
            await papers.web.close()

    run(exercise())


def test_citations_keep_known_openalex_id_when_alias_lookup_fails(store):
    citation_queries = []

    async def respond(request):
        if "semanticscholar" in str(request.url):
            return httpx.Response(200, json={"data": []})
        if request.url.path.endswith("/works/doi:10.1000/seed"):
            return httpx.Response(404)
        citation_queries.append(request.url.params["filter"])
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W2",
                        "title": "OpenAlex Citation",
                    }
                ]
            },
        )

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        seed = from_openalex(
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1000/seed",
                "title": "Seed Paper",
            }
        )
        found, errors = await papers.citations(seed, "2024-01-01")
        assert {paper["title"] for paper in found} == {"OpenAlex Citation"}
        assert citation_queries == ["cites:W1", "cites:W1,from_publication_date:2024-01-01"]
        assert len(errors) == 1 and "openalex" in errors[0].casefold()
        assert "404" in errors[0]
        await papers.web.close()

    run(exercise())


def test_resolve_many_falls_back_to_free_openalex_when_s2_is_throttled(store):
    async def respond(request):
        if "semanticscholar" in str(request.url):
            return httpx.Response(429, headers={"retry-after": "0"})
        if "api.openalex.org/works/doi:10.48550/arxiv.2205.14135" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "https://openalex.org/W1",
                    "title": "FA",
                    "doi": "https://doi.org/10.48550/arxiv.2205.14135",
                },
            )
        return httpx.Response(404)

    async def exercise():
        papers = provider(store, respond)
        papers.web.attempts = 1
        found = await papers.resolve_many(["ARXIV:2205.14135"])
        assert [p["id"] for p in found] == ["ARXIV:2205.14135"] and found[0]["oa"] == "W1"
        assert papers.cached("2205.14135")["title"] == "FA"
        await papers.web.close()

    run(exercise())


def test_resolve_many_reuses_complete_cached_native_abstracts_without_http(store):
    async def respond(request):
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        papers = provider(store, respond)
        arxiv = papers.remember(
            from_openalex(
                {
                    "id": "https://openalex.org/W1",
                    "title": "Complete cached preprint",
                    "ids": {"arxiv": "https://arxiv.org/abs/2401.00001"},
                    "abstract_inverted_index": {"Complete": [0], "evidence.": [1]},
                }
            )
        )
        openalex = papers.remember(
            from_openalex(
                {
                    "id": "https://openalex.org/W2",
                    "title": "Complete cached journal paper",
                    "abstract_inverted_index": {"Journal": [0], "evidence.": [1]},
                }
            )
        )
        found = await papers.resolve_many(["ARXIV:2401.00001v3", "OA:W2"])
        assert [paper["id"] for paper in found] == [arxiv["id"], openalex["id"]]
        assert [paper["abstract"] for paper in found] == [
            "Complete evidence.",
            "Journal evidence.",
        ]
        assert papers.web.requests == 0
        await papers.web.close()

    run(exercise())


def test_resolve_many_enriches_only_missing_evidence_in_a_mixed_batch(store):
    requests = []

    async def respond(request):
        requests.append(str(request.url))
        if request.url.path.endswith("/paper/batch"):
            assert json.loads(request.content)["ids"] == [
                "arXiv:2402.00002",
                "DOI:10.1234/uncached",
            ]
            return httpx.Response(
                200,
                json=[
                    {
                        "paperId": "a" * 40,
                        "title": "Enriched cached preprint",
                        "abstract": "Recovered preprint abstract.",
                        "externalIds": {"ArXiv": "2402.00002"},
                    },
                    {
                        "paperId": "b" * 40,
                        "title": "New DOI paper",
                        "abstract": "Resolved DOI abstract.",
                        "externalIds": {"DOI": "10.1234/uncached"},
                    },
                ],
            )
        if request.url.path == "/works/W3":
            return httpx.Response(
                200,
                json={
                    "id": "https://openalex.org/W3",
                    "title": "Refreshed OpenAlex paper",
                    "abstract_inverted_index": {"Recovered": [0], "journal": [1], "abstract.": [2]},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def exercise():
        papers = provider(store, respond)
        complete = papers.remember(
            from_openalex(
                {
                    "id": "https://openalex.org/W1",
                    "title": "Already complete",
                    "ids": {"arxiv": "https://arxiv.org/abs/2401.00001"},
                    "abstract_inverted_index": {"Cached": [0], "abstract.": [1]},
                }
            )
        )
        incomplete_arxiv = papers.remember(
            from_openalex(
                {
                    "id": "https://openalex.org/W2",
                    "title": "Incomplete preprint",
                    "ids": {"arxiv": "https://arxiv.org/abs/2402.00002"},
                }
            )
        )
        incomplete_openalex = papers.remember(
            from_openalex({"id": "https://openalex.org/W3", "title": "Incomplete journal paper"})
        )
        found = await papers.resolve_many(
            [complete["id"], incomplete_arxiv["id"], incomplete_openalex["id"], "10.1234/uncached"]
        )
        assert [paper["id"] for paper in found] == [
            complete["id"],
            incomplete_arxiv["id"],
            incomplete_openalex["id"],
            "DOI:10.1234/uncached",
        ]
        assert [paper["abstract"] for paper in found] == [
            "Cached abstract.",
            "Recovered preprint abstract.",
            "Recovered journal abstract.",
            "Resolved DOI abstract.",
        ]
        await papers.web.close()

    run(exercise())
    assert len(requests) == 2
    assert not any("2401.00001" in request or "W1" in request for request in requests)
