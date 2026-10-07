"""Generate host manifests. The repository root is the self-contained plugin."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def build():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    base = {
        "name": "citation-lens",
        "version": project["version"],
        "description": project["description"],
        "author": {"name": "Aman Behera"},
        "license": "MIT",
        "homepage": "https://github.com/beingamanforever/Citation-lens",
        "repository": "https://github.com/beingamanforever/Citation-lens",
    }
    portable = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        **base,
        "extensions": {
            "com.openai": {
                "interface": {
                    "displayName": "Citation Lens",
                    "shortDescription": "Read a field through citations",
                    "longDescription": project["description"],
                    "developerName": "Null and Novel",
                    "category": "Productivity",
                    "capabilities": ["Read"],
                    "defaultPrompt": [
                        "Research a topic through a citation graph and selected paper evidence."
                    ],
                }
            }
        },
    }
    write(PLUGIN / "plugin.json", portable)
    write(PLUGIN / ".claude-plugin/plugin.json", base)
    for name, root_var, data_var in (
        ("mcp.json", "PLUGIN_ROOT", "PLUGIN_DATA"),
        (".mcp.json", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
    ):
        root = "${" + root_var + "}"
        data = "${" + data_var + "}"
        config = {
            "mcpServers": {
                "citation-lens": {
                    "type": "stdio",
                    "command": "uv",
                    "args": [
                        "run",
                        "--no-project",
                        "--with-requirements",
                        root + "/requirements-runtime.txt",
                        "python",
                        root + "/run.py",
                    ],
                    "env": {"CITATION_LENS_DATA": data + "/data"},
                }
            }
        }
        if name == "mcp.json":
            config["$schema"] = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
        write(PLUGIN / name, config)
    write(
        ROOT / ".agents/plugins/marketplace.json",
        {
            "name": "null-and-novel",
            "interface": {"displayName": "Null and Novel"},
            "plugins": [
                {
                    "name": "citation-lens",
                    "source": {"source": "local", "path": "./"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": "Productivity",
                }
            ],
        },
    )
    write(
        ROOT / ".claude-plugin/marketplace.json",
        {
            "name": "null-and-novel",
            "description": "Research tools by Null and Novel",
            "owner": {"name": "Aman Behera"},
            "plugins": [{**base, "source": "./"}],
        },
    )


if __name__ == "__main__":
    build()
    print("Built portable Codex and Claude Code plugin adapters.")
