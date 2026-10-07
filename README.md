# Citation Lens

**Search wider. Follow citations. Read the evidence.**

A local research plugin for **Codex and Claude Code**, by [Null and Novel](https://github.com/beingamanforever).

## What you get

- **Wider discovery, fewer calls.** Batch up to three queries across OpenAlex, Semantic Scholar and arXiv: nine parallel searches, up to 180 candidates, with identifier deduplication.
- **A citation map you can extend.** Follow references and citing papers, screen candidates, then resume selected branches. Shared papers appear once; sampling and provider failures stay visible.
- **Context spent on selected evidence.** Start with ten cards, page cached candidates, and read chosen text slices. The bundled Python client filters intermediate results before printing; later text pages avoid repeated overviews.
- **Figures you can actually inspect.** Read tables and LaTeX, then request original figures or rendered PDF pages with source provenance and extraction warnings.

Five tools, one shared research skill, a local SQLite cache. Your existing agent screens and synthesizes; no extra model service or vector database.

## Install

Requires **Python 3.11+**, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Git.
Dependencies download on first use. Restart your agent after installation.

**Codex**

```bash
codex plugin marketplace add beingamanforever/Citation-lens
codex plugin add citation-lens@null-and-novel
```

**Claude Code**

```bash
claude plugin marketplace add beingamanforever/Citation-lens
claude plugin install citation-lens@null-and-novel
```

Use `/citation-lens:research <topic>` in Claude Code, or ask either agent:

> Use Citation Lens to research memory-efficient attention. Batch query variants,
> search recent preprints, follow citations both ways, then select ten papers.
> Read their methods, results and key figures. Compare evidence and report gaps.

[Tool examples, programmatic search and standalone MCP](docs/USAGE.md).

## Measured results

| Measurement | Baseline | Citation Lens |
| --- | ---: | ---: |
| Live `FlashAttention` discovery | 5 search hits | 21 indexed papers, 18 citation edges from three seeds |
| Same two readable papers, live | 218,270 bytes of text + metadata | 2,011 bytes of cards: **99.08% smaller** |
| Duplicate-heavy search fixture | 4 calls; about 7,200 reference tokens | 1 call; about 2,100 reference tokens |
| Paged reading fixture, same complete text | About 76,000 reference tokens | About 30,000; **59.15% fewer bytes** |

Live run: **8 October 2026**. Fixture tokens use `cl100k_base`, not actual Codex/Claude billing.
Batch gains depend on overlap: reading every disjoint result adds provenance overhead.
Extra candidates can be irrelevant; research quality and whole-agent latency remain unmeasured.
[Exact results, failures and reproduction](docs/BENCHMARKS.md).

## Access and limits

Optional `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` improve provider access; Jina Reader is opt-in.
Citation indexes lag, graphs are bounded, and PDF extraction can lose structure. Use a vision-capable agent for figures.
Queries go to their providers; cache and snapshots stay local. [Privacy](docs/PRIVACY.md).

**v0.1 preview:** installable GitHub marketplace package, not an official-directory listing or PyPI release.
[Design](docs/DESIGN.md) · [Review](docs/REVIEW.md) · [Development](docs/PUBLISHING.md) · [Releases](https://github.com/beingamanforever/Citation-lens/releases)

MIT licensed.
