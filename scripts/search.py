"""Search and expand outside the model context; print only the cards you keep.

    python scripts/search.py "FlashAttention" "ring attention" --expand 3 --show 15

Runs research_search, optionally expands the top N results as seeds, and prints compact JSON.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    server = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "run.py")], env=dict(os.environ)
    )
    async with stdio_client(server) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()

        async def call(tool, arguments):
            result = await session.call_tool(tool, arguments)
            if result.isError:
                raise RuntimeError(result.content[0].text)
            return json.loads(result.content[0].text)

        found = await call("research_search", {"query": args.query, "limit": args.show})
        output = {"search": found["papers"], "searches": found["searches"]}
        if args.expand:
            seeds = [card["id"] for card in found["papers"][: args.expand]]
            graph = await call(
                "research_expand",
                {"seed_ids": seeds, "query": args.query[0], "limit": args.show},
            )
            output |= {
                "expanded": graph["papers"],
                "edges": graph["edges"],
                "errors": graph["errors"],
            }
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("query", nargs="+", help="1-4 query variants")
    parser.add_argument("--expand", type=int, default=0, help="expand the top N search results")
    parser.add_argument("--show", type=int, default=15)
    asyncio.run(main(parser.parse_args()))
