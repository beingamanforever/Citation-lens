# Citation Lens

A literature-research plugin for Codex and Claude Code.
Give it a research question; it finds the defining papers, walks their citation graph forward and backward like [Connected Papers](https://www.connectedpapers.com/about), surfaces the newest preprints, and hands the agent compact previews it can verify and cite.

## How it works

1. **Search broadly, in parallel.** One call runs every query variant on Semantic Scholar, OpenAlex and arXiv at once, merges duplicate versions, and fuses the rankings.
   A recent lane reserves every third card for work from the last two years when available.
2. **Expand the citation graph.** From 3-6 seed papers Lens collects their references, their citing papers (newest from Semantic Scholar, most cited from OpenAlex) and Semantic Scholar's similar papers, then reads the candidates' own reference lists to measure bibliographic coupling and co-citation.
   Neighbors come back in three lanes: **foundation** (prior work the graph cites), **follow-up** (work that cites or resembles the seeds, ranked by shared references) and **recent** (the last two years).
3. **Preview, then read.** A card is about 500 bytes: ID, title, year, citations, URL, why it was chosen, and one verbatim abstract sentence matching the query.
   Abstracts, full text and figures are separate, on-demand calls.
   Snapshots page from a local cache without new requests.
4. **Cite real links.** Edges are citing -> cited pairs taken from reference lists; shared references and similarity are labeled as such and never reported as citations.

The agent stays in charge of judgment: Lens gathers, ranks and previews; the agent screens, verifies and writes.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.
Restart your agent after installing.

```bash
codex plugin marketplace add beingamanforever/Citation-lens
codex plugin add citation-lens@citation-lens
```

```bash
claude plugin marketplace add beingamanforever/Citation-lens
claude plugin install citation-lens@citation-lens
```

Optional API keys can increase provider access; their effect on end-to-end performance is not measured here.
Set `OPENALEX_API_KEY` ([OpenAlex settings](https://openalex.org/settings/api)) and `SEMANTIC_SCHOLAR_API_KEY` ([request form](https://www.semanticscholar.org/product/api#api-key-form)).

## Use

> Use Citation Lens to research memory-efficient exact attention: foundations, the most
> influential follow-ups and the newest work, with citation links.

In Claude Code you can also run `/citation-lens:research <topic>`.
[Tools, standalone MCP and scripting](docs/USAGE.md).

## Results

The goal is more relevant papers and trustworthy citations than the host's default web research, under a matched budget.
Quality, completion rate, time and tokens are measured separately.

A corrected Codex evaluation covers 10 held-out tasks, twice each.
The blinded judge favored Lens in **17 pairs**, with one loss and two ties.
The saved answers were regraded after fixing URL, quote and citation checks ([protocol and limits](docs/EVALUATION.md), [papers and evidence](docs/results/codex-heldout.json)).

| Mean per attempt | Codex | Codex + Lens |
| --- | ---: | ---: |
| Directly relevant papers | 13.1 | 19.2 |
| Recent useful papers | 5.6 | 9.1 |
| Verified links between useful papers | 2.7 | 8.5 |
| Completed attempts | 19 / 20 | 18 / 20 |
| Seconds | 258 | 335 |

Lens took about 30% longer in this run.
Reported input-token usage was also higher; timed-out attempts did not report usage, so total consumed tokens are unknown.
These measurements used keyless providers and an earlier package snapshot.
The paired Claude Code comparison is running; a measured Claude advantage is not established yet.

[Design and research basis](docs/DESIGN.md) · [Privacy](docs/PRIVACY.md) ·
[Development](docs/PUBLISHING.md) · [MIT license](LICENSE)
