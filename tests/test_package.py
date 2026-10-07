import asyncio
import json
import os
import sys
import tomllib
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from citation_lens.storage import Store

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT


def test_host_manifests_and_marketplace_paths():
    portable = json.loads((PLUGIN / "plugin.json").read_text())
    claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert portable["name"] == claude["name"] == "citation-lens"
    assert portable["version"] == claude["version"] == project["version"]
    skill = (PLUGIN / "skills/research/SKILL.md").read_text()
    assert "name: research" in skill and "research_visual" in skill
    for name, variable in (("mcp.json", "PLUGIN_ROOT"), (".mcp.json", "CLAUDE_PLUGIN_ROOT")):
        config = json.loads((PLUGIN / name).read_text())["mcpServers"]["citation-lens"]
        assert config["type"] == "stdio" and config["command"] == "uv"
        assert config["args"][-1] == "${" + variable + "}/run.py"
        assert config["args"][3].endswith("/requirements-runtime.txt")
        assert "API_KEY" not in json.dumps(config)
    for file in (
        ROOT / ".agents/plugins/marketplace.json",
        ROOT / ".claude-plugin/marketplace.json",
    ):
        market = json.loads(file.read_text())
        source = market["plugins"][0]["source"]
        relative = source["path"] if isinstance(source, dict) else source
        assert (ROOT / relative / "run.py").exists()


def test_packaged_adapter_starts_and_calls_cached_read(tmp_path):
    store = Store(tmp_path / "cache.sqlite")
    store.save("paper:OA:W1", {"id": "OA:W1", "arxiv": "", "provider": "openalex"})
    store.save("document:OA:W1", {"paper_id": "OA:W1", "text": "Packaged evidence", "figures": []})
    store.close()

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(PLUGIN / "run.py")],
            env={**os.environ, "CITATION_LENS_DATA": str(tmp_path)},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool("research_read", {"paper_id": "OA:W1"})
            assert not result.isError and "Packaged evidence" in result.content[0].text

    asyncio.run(exercise())


def test_adapter_launch_is_warning_free():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-W", "error", str(PLUGIN / "run.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Citation Lens" in result.stdout and not result.stderr


def test_programmatic_client_honors_cache_environment(tmp_path):
    import hashlib
    import subprocess
    from urllib.parse import urlencode

    from citation_lens.papers import OA, OA_FIELDS

    query = "cached fixture query"
    url = OA + "?" + urlencode({"search": query, "select": OA_FIELDS, "per_page": 1})
    key = "http:" + hashlib.sha256(json.dumps([url, None], sort_keys=True).encode()).hexdigest()
    store = Store(tmp_path / "cache.sqlite")
    store.put(
        key,
        json.dumps(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Cached client evidence",
                    }
                ],
                "meta": {"count": 1},
            }
        ).encode(),
    )
    store.close()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/search.py"),
            query,
            "--provider",
            "openalex",
            "--limit",
            "1",
            "--show",
            "1",
        ],
        env={**os.environ, "CITATION_LENS_DATA": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["papers"][0]["title"] == "Cached client evidence"
    assert output["http_totals"] == {"requests": 0, "cache_hits": 1}
    store = Store(tmp_path / "cache.sqlite")
    assert store.load("graph:" + output["graph_id"])["nodes"]
    store.close()
