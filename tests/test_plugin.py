import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from conftest import run, s2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"research_search", "research_expand", "research_graph", "research_read", "research_visual"}


def test_manifests_point_at_the_plugin_and_hold_no_secrets():
    portable = json.loads((ROOT / "plugin.json").read_text())
    claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert portable["name"] == claude["name"] == "citation-lens"
    assert portable["version"] == claude["version"] == version
    assert "name: research" in (ROOT / "skills/research/SKILL.md").read_text()
    for name, variable in (("mcp.json", "PLUGIN_ROOT"), (".mcp.json", "CLAUDE_PLUGIN_ROOT")):
        config = json.loads((ROOT / name).read_text())["mcpServers"]["citation-lens"]
        assert config["args"][-1] == "${" + variable + "}/run.py"
        assert "API_KEY" not in json.dumps(config)
    for market in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
        source = json.loads((ROOT / market).read_text())["plugins"][0]["source"]
        assert (ROOT / (source["path"] if isinstance(source, dict) else source) / "run.py").exists()


def test_launch_is_warning_free():
    result = subprocess.run(
        [sys.executable, "-W", "error", str(ROOT / "run.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Citation Lens" in result.stdout and not result.stderr


def test_stdio_server_lists_tools_and_reads_cached_abstracts(tmp_path):
    store = Store(tmp_path / "cache.sqlite")
    paper = s2(
        "a1", "Fast Exact Attention", 2022, 3000, "Exact attention with tiling.", arxiv="2205.14135"
    )
    store.save("paper:" + paper["id"], paper)
    store.save("alias:arxiv:2205.14135", paper["id"])
    store.save("alias:s2:" + paper["s2"], paper["id"])
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "run.py")],
            env={**os.environ, "CITATION_LENS_DATA": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as s:
            info = await s.initialize()
            assert "untrusted" in info.instructions
            assert {tool.name for tool in (await s.list_tools()).tools} == TOOLS
            result = await s.call_tool("research_read", {"paper_id": ["2205.14135"]})
            text = result.content[0].text
            body = json.loads(text)
            assert not result.isError and result.structuredContent is None
            assert body["papers"][0]["abstract"] == "Exact attention with tiling."
            assert body["missing"] == [] and body["payload_bytes"] == len(text.encode())
            assert (await s.call_tool("research_graph", {"graph_id": "unknown"})).isError
            bad = await s.call_tool("research_expand", {"seed_ids": [], "query": "x"})
            assert bad.isError

    run(exercise())
