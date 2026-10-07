# Citation Lens

A research plugin for Codex and Claude Code.
Search papers, follow citations, and read text and figures from the papers you choose.

## About

Citation Lens searches OpenAlex, Semantic Scholar and arXiv, then lets your agent explore the papers behind a topic.
It supports broader searches by limiting what gets fetched and what enters the agent's context.

- **Search together.** Run query variants across providers concurrently, with rate limits, and merge duplicate papers by their identifiers.
- **Reuse the work.** Cache provider responses locally and save citation graphs so searches, result pages and graph branches can be revisited without starting over.
- **Read selectively.** Screen short paper cards before fetching text or figures.
  Read selected passages without repeating the paper overview on every page.
  The bundled Python client can filter results before they reach the agent.
- **Follow the evidence.** Explore references and citing papers, inspect tables, equations and original figures, and keep source links, sampling limits and provider errors visible.

These choices reduce repeated requests and unnecessary context while leaving the agent in control of what to read next.
Savings depend on the search and how much evidence you read.

## Install

Requires **Python 3.11+**, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Git.
Dependencies download on first use.
Restart your agent after installation.

**Codex**

```bash
codex plugin marketplace add beingamanforever/Citation-lens
codex plugin add citation-lens@citation-lens
```

**Claude Code**

```bash
claude plugin marketplace add beingamanforever/Citation-lens
claude plugin install citation-lens@citation-lens
```

## Use

Ask either agent:

> Use Citation Lens to research memory-efficient attention.
> Search across providers, follow references and citing papers, then read the most relevant methods, results and figures.
> Compare the evidence and explain the gaps.

In Claude Code, you can also use `/citation-lens:research <topic>`.

[Tool examples, programmatic search and standalone MCP](docs/USAGE.md).

## Results

[Benchmarks](docs/BENCHMARKS.md) contain exact measurements, failures, cases where batching costs more, and reproduction commands.
They distinguish local fixtures and live provider runs from unmeasured whole-agent latency, token billing and research quality.

## Access and limits

Optional `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` improve provider access; Jina Reader is opt-in.
Citation indexes can lag, graphs have size limits, and PDF extraction can lose structure.
Use a vision-capable agent for figures.
Queries go to their providers; cache and snapshots stay local.
[Privacy](docs/PRIVACY.md).

[Design](docs/DESIGN.md) · [Development](docs/PUBLISHING.md) · [Releases](https://github.com/beingamanforever/Citation-lens/releases)

[MIT license](LICENSE).
