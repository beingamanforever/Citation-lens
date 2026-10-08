import json
import os
import sys
from pathlib import Path

from conftest import run, s2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def test_stdio_read_enforces_shared_budget_and_reports_abstract_provenance(tmp_path):
    store = Store(tmp_path / "cache.sqlite")
    ids = []
    for index in range(3):
        paper = s2(
            str(index + 1),
            f"Cached paper {index + 1}",
            2024,
            1,
            f"Abstract {index + 1}",
        )
        if index == 0:
            paper["abstract_source"] = "openalex"
        elif index == 2:
            paper["sources"] = []
            paper["abstract_source"] = None
        paper_id = "S2:" + paper["s2"]
        ids.append(paper_id)
        store.save("paper:" + paper["id"], paper)
        store.save("alias:s2:" + paper["s2"], paper["id"])
        store.save(
            "document:" + paper["id"],
            {
                "text": str(index + 1) * 20_000,
                "figures": [],
                "format": "html",
                "warnings": [],
                "paper_id": paper["id"],
                "source_url": "https://example.test/paper",
                "source_sha256": "source",
                "text_sha256": "text",
                "fallback_errors": [],
            },
        )
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "run.py")],
            env={**os.environ, "CITATION_LENS_DATA": str(tmp_path)},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()

            oversized = await session.call_tool(
                "research_read", {"paper_id": ids, "part": "text", "max_chars": 36_000}
            )
            assert oversized.isError and "12000" in oversized.content[0].text

            too_small = await session.call_tool(
                "research_read", {"paper_id": ids, "part": "text", "max_chars": 2}
            )
            assert too_small.isError and "at least 3" in too_small.content[0].text

            valid = await session.call_tool(
                "research_read", {"paper_id": ids, "part": "text", "max_chars": 12_000}
            )
            body = json.loads(valid.content[0].text)
            assert not valid.isError
            assert sum(len(document["text"]) for document in body["documents"]) <= 12_000

            abstracts = await session.call_tool(
                "research_read", {"paper_id": ids, "part": "abstract"}
            )
            papers = json.loads(abstracts.content[0].text)["papers"]
            assert [paper["abstract_source"] for paper in papers] == [
                "openalex",
                "semantic_scholar",
                None,
            ]

    run(exercise())
