"""Run and page a bounded search in code; print only selected cards and coverage."""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def search(arguments):
    started = time.perf_counter()
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "run.py")], env=dict(os.environ)
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()

        async def call(tool, parameters):
            result = await session.call_tool(tool, parameters)
            if result.isError:
                raise RuntimeError(result.content[0].text)
            return json.loads(result.content[0].text)

        page = await call(
            "research_search",
            {
                "query": arguments.query,
                "provider": arguments.provider,
                "limit": arguments.limit,
            },
        )
        first, papers, calls = page, list(page["papers"]), 1
        while page["next_offset"] is not None:
            page = await call(
                "research_graph",
                {
                    "graph_id": first["graph_id"],
                    "offset": page["next_offset"],
                },
            )
            papers.extend(page["papers"])
            calls += 1
        return {
            "recorded_at": datetime.now(UTC).isoformat(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "timing_scope": "SDK startup, search and cached paging; excludes host inference.",
            "tool_calls": calls,
            "http_totals": page["http_totals"],
            "graph_id": first["graph_id"],
            "total_candidates": first["node_count"],
            "searches": first["searches"],
            "errors": first["errors"],
            "papers": papers[: arguments.show],
            "selection": "Existing relevance/recency/prominence order; screen before reading. "
            "Remaining cards are retained in graph_id, not discarded.",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="+")
    parser.add_argument(
        "--provider",
        nargs="+",
        choices=["openalex", "semantic_scholar", "arxiv"],
        default=["openalex"],
    )
    parser.add_argument("--limit", type=int, choices=range(1, 21), default=10)
    parser.add_argument("--show", type=int, choices=range(1, 21), default=10)
    print(json.dumps(asyncio.run(search(parser.parse_args())), separators=(",", ":")))
