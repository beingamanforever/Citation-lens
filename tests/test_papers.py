import asyncio
import json

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


def test_arxiv_feed_parsing_skips_error_entries():
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>7</opensearch:totalResults>
      <entry><id>http://arxiv.org/abs/2401.00001v2</id><title>New
      Attention</title><summary> Fresh   preprint. </summary>
      <published>2026-05-01T00:00:00Z</published><author><name>A. Author</name></author></entry>
      <entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title></entry>
    </feed>"""
    papers, total = from_arxiv(feed)
    assert total == 7 and len(papers) == 1
    assert papers[0]["id"] == "ARXIV:2401.00001" and papers[0]["date"] == "2026-05-01"
    assert papers[0]["title"] == "New Attention" and papers[0]["abstract"] == "Fresh preprint."
    assert papers[0]["abstract_source"] == "arxiv"


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


def test_strict_providers_get_the_first_content_words():
    assert strict_terms("the exact attention for memory and online softmax") == [
        "exact",
        "attention",
        "memory",
        "online",
    ]
    assert strict_terms('"online softmax" normalizer') == ['"online softmax"', "normalizer"]


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
        a = s2("x", "Seed A", arxiv="2205.14135") | {"s2": ""}
        b = s2("y", "Seed B", arxiv="2112.05682") | {"s2": ""}
        refs_a, refs_b = await papers.references([a, b])
        assert [p["title"] for p in refs_a] == ["Online Softmax"]
        assert isinstance(refs_b, Exception)
        assert "openalex" in str(refs_b)
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
        assert await papers.references([seed]) == [[]]
        assert openalex_requests == 0
        await papers.web.close()

    run(exercise())


def test_references_preserve_an_authoritative_empty_openalex_result(store):
    async def respond(request):
        if request.url.path.endswith("/paper/batch"):
            return httpx.Response(503)
        if request.url.path.endswith("/works/doi:10.1000/seed"):
            return httpx.Response(200, json={"id": "https://openalex.org/W1"})
        if request.url.path.endswith("/works/W1"):
            return httpx.Response(200, json={"referenced_works": []})
        raise AssertionError(str(request.url))

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
        assert await papers.references([seed]) == [[]]
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
        assert isinstance(result, Exception)
        assert "openalex" in str(result)
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
        assert isinstance(result, Exception)
        assert "semantic scholar" in str(result).casefold()
        assert "openalex" in str(result).casefold()
        await papers.web.close()

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
    monkeypatch.setattr(papers_module, "CITATION_PROVIDER_SECONDS", 0.02)
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
            found, errors = await asyncio.wait_for(papers.citations(seed, "2024-01-01"), 0.1)
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
