import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

from grade import (
    Resolver,
    bibliography_match,
    check_facts,
    describe_answer,
    mask_tool_names,
    quote_text,
    same_title,
    score,
    summarize,
    url_identifier,
)  # noqa: E402


def test_pair_description_masks_tool_names_symmetrically_without_mutating_answers():
    import copy

    record = {
        "answer": {
            "summary": "Citation Lens found O(n^2) attention; WebSearch returned 25 papers.",
            "gaps": ["research_expand failed; WebFetch failed; sources remain partial."],
        },
        "entries": [
            {
                "title": "Lens Imaging with O(n^2) Attention",
                "paper": {"year": 2025},
                "role": "foundation",
                "reason": "mcp__citation-lens__research_search found -5% memory usage.",
            }
        ],
    }
    original = copy.deepcopy(record)
    web = dict(record, mode="web")
    lens = dict(record, mode="lens")
    description = describe_answer(web, {})
    assert description == describe_answer(lens, {})
    assert "Citation Lens" not in description and "research_expand" not in description
    assert "WebSearch" not in description and "WebFetch" not in description
    assert "mcp__" not in description
    assert "Lens Imaging with O(n^2) Attention" in description
    assert "-5% memory usage" in description
    assert "sources remain partial" in description
    assert record == original


def test_tool_masking_covers_contract_names_without_masking_scientific_lenses():
    names = "research_search research_expand research_graph research_read research_visual"
    assert mask_tool_names(names) == " ".join(["[research tool]"] * 5)
    assert mask_tool_names("citation-lens CITATION_LENS web.run web_search") == " ".join(
        ["[research tool]"] * 4
    )
    assert mask_tool_names("An optical lens focuses light.") == "An optical lens focuses light."
    assert mask_tool_names("Lens reference edges remain partial.") == (
        "[research tool] reference edges remain partial."
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://arxiv.org/abs/2205.14135v2", "ARXIV:2205.14135v2"),
        ("https://www.nature.com/articles/s41586-020-1994-5", "DOI:10.1038/s41586-020-1994-5"),
        ("https://aclanthology.org/2022.naacl-main.272/", "DOI:10.18653/v1/2022.naacl-main.272"),
        ("https://dl.acm.org/doi/10.1145/3600006.3613165", "DOI:10.1145/3600006.3613165"),
        ("https://www.science.org/doi/10.1126/science.adi2336", "DOI:10.1126/science.adi2336"),
        ("https://openreview.net/forum?id=abc", None),
    ],
)
def test_url_identifier(url, expected):
    assert url_identifier(url) == expected


def test_same_title_tolerates_case_and_punctuation_but_not_other_papers():
    assert same_title(
        "Mamba: Linear-Time Sequence Modeling", "mamba linear-time sequence modeling."
    )
    assert not same_title(
        "FlashAttention-2", "FlashAttention-2: Faster Attention with Better Parallelism"
    )
    assert not same_title("FlashAttention", "FlashAttention-2: Faster Attention")
    assert not same_title("", "Anything")


def test_score_counts_only_found_relevant_papers_and_verified_links():
    paper = {"citations": 500, "year": 2025}
    entries = [
        {"key": "a", "status": "ok", "paper": paper, "recent": True, "quote_found": True},
        {"key": "b", "status": "ok", "paper": paper, "recent": False, "quote_found": False},
        {"key": "c", "status": "wrong_url", "paper": paper, "recent": True, "quote_found": None},
        {"key": "missing:x", "status": "not_found", "paper": None},
    ]
    record = {
        "task": "t",
        "mode": "lens",
        "rep": 1,
        "status": "ok",
        "seconds": 90.0,
        "tool_calls": 4,
        "calls": [{"result_bytes": 1000}],
        "usage": {"input_tokens": 10},
        "entries": entries,
        "anchors_found": ["ARXIV:1"],
        "edges": [
            {"citing": "a", "cited": "b", "verified": True},
            {"citing": "a", "cited": "c", "verified": True},
            {"citing": "b", "cited": "a", "verified": False},
        ],
    }
    labels = {
        "a": {"relevance": 2, "approach": "GPU I/O"},
        "b": {"relevance": 1, "approach": "none"},
        "c": {"relevance": 2, "approach": "GPU I/O"},
    }
    task = {"anchors": [1, 2], "required_approaches": ["GPU I/O", "memory reduction"]}
    result = score(record, labels, task)
    assert (result["papers"], result["useful"], result["core"]) == (4, 2, 1)
    assert result["precision"] == 0.5 and result["recent_useful"] == 1
    assert result["not_found"] == 1 and result["wrong_url"] == 1
    assert result["citations_verified"] == 2 and result["citations_useful"] == 1
    assert result["anchor_recall"] == 0.5 and result["approach_coverage"] == 0.5
    assert (result["quotes_checked"], result["quotes_verbatim"]) == (2, 1)


def test_title_prefix_is_not_paper_identity():
    assert not same_title(
        "Self-attention Does Not Need Quadratic Memory",
        "Self-attention Does Not Need Quadratic Memory for Graph Transformers",
    )


def test_quote_normalization_preserves_math_and_signs():
    assert quote_text("A\n  real quote.") == "A real quote."
    assert quote_text("Complexity is O(n-2), with -5% gain.") != quote_text(
        "Complexity is O(n^2), with +5% gain."
    )


def test_unknown_url_needs_destination_evidence():
    class FakeResolver(Resolver):
        def __init__(self):
            pass

        async def by_title(self, title):
            return {"id": "S2:a", "title": title}

        async def page_matches(self, paper, url):
            return False

    paper, status = asyncio.run(
        FakeResolver().resolve("A real research paper", "https://example.org/unrelated")
    )
    assert paper and status == "url_unverified"


def test_bibliography_requires_full_identity_in_one_item():
    from conftest import s2

    target = s2("1", "Self-attention Does Not Need Quadratic Memory", 2021)
    target["authors"] = ["Alice Researcher"]
    wrong = {
        "ids": [],
        "title": target["title"] + " for Graph Transformers",
        "text": "Alice Researcher. 2021.",
    }
    assert bibliography_match([wrong], target) is None
    exact = wrong | {"title": target["title"]}
    assert bibliography_match([exact], target)["source"] == "primary_title_authors_year"


def test_fact_checker_rejects_altered_quote_and_different_cited_paper():
    from conftest import s2

    citing = s2("1", "Citing research paper", 2024, abstract="Complexity is O(n^2), with +5% gain.")
    cited = s2("2", "Self-attention Does Not Need Quadratic Memory", 2021)

    class FakeResolver:
        async def prefetch(self, ids):
            pass

        async def resolve(self, title, url):
            return (citing if title == citing["title"] else cited), "ok"

        async def bibliography(self, paper):
            return [
                {
                    "ids": [],
                    "title": cited["title"] + " for Graph Transformers",
                    "text": "2021 Alice Researcher",
                }
            ]

        async def references(self, paper):
            return set()

    record = {
        "task": "t",
        "status": "ok",
        "answer": {
            "papers": [
                {
                    "title": citing["title"],
                    "url": citing["url"],
                    "quote": "Complexity is O(n-2), with -5% gain.",
                },
                {"title": cited["title"], "url": cited["url"]},
            ],
            "citations": [{"citing_url": citing["url"], "cited_url": cited["url"]}],
        },
    }
    asyncio.run(check_facts([record], {"t": {"anchors": []}}, FakeResolver(), "2024-01-01"))
    assert record["entries"][0]["quote_found"] is False
    assert record["edges"][0]["verified"] is False


def test_missing_usage_is_not_measured_zero():
    from grade import DIMENSIONS

    row = {
        name: 0
        for name in [
            "useful",
            "core",
            "precision",
            "recent_useful",
            "prominent_useful",
            "anchor_recall",
            "approach_coverage",
            "citations_useful",
            "citations_verified",
            "not_found",
            "wrong_url",
            "url_unverified",
            "seconds",
            "tool_calls",
            "input_tokens",
            "output_tokens",
            "lens_bytes",
            "quotes_checked",
            "quotes_verbatim",
        ]
    }
    rows = [
        row | {"mode": "web", "status": "ok", "input_tokens": 100},
        row | {"mode": "web", "status": "timeout", "input_tokens": None},
    ]
    result = summarize(rows, [{"winner": dict.fromkeys(DIMENSIONS, "tie")}])
    assert result["arms"]["web"]["input_tokens"] == 100
    assert result["arms"]["web"]["token_usage_missing"] == 1


def test_malformed_failed_answers_stay_in_the_denominator():
    class FakeResolver:
        async def prefetch(self, ids):
            assert ids == []

    record = {"task": "t", "status": "invalid_answer", "answer": {"papers": [{}]}}
    asyncio.run(check_facts([record], {"t": {"anchors": []}}, FakeResolver(), "2024-01-01"))
    assert record["entries"] == record["edges"] == record["anchors_found"] == []


def test_pdf_title_prefix_does_not_verify_destination():
    import io

    from conftest import s2
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata(
        {
            "/Title": "Attention Is All You Need for Graph Transformers",
            "/Author": "Alice Researcher",
        }
    )
    buffer = io.BytesIO()
    writer.write(buffer)

    class FakeWeb:
        async def fetch(self, url, **kwargs):
            return buffer.getvalue()

    resolver = object.__new__(Resolver)
    resolver.web = FakeWeb()
    paper = s2("1", "Attention Is All You Need", 2017)
    paper["authors"] = ["Alice Researcher"]
    assert not asyncio.run(resolver.page_matches(paper, "https://example.org/another.pdf"))
