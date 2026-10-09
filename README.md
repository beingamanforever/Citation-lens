<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo-light.svg" alt="Citation Lens" width="440">
  </picture>
</p>

<h3 align="center">Web search finds pages. Research needs a graph.</h3>

<p align="center">
  Literature research for your coding agent: a citation graph with verified links, for Codex and Claude Code.
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#see-it-work">Watch</a> ·
  <a href="demo/index.html">Side-by-side demo</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="#results">Results</a>
</p>

<p align="center">
  <video src="https://github.com/beingamanforever/Citation-lens/raw/main/video/citation-lens.mp4" poster="video/poster.png" controls muted width="860">
    <a href="video/citation-lens.mp4"><img src="video/poster.png" alt="Citation Lens in 47 seconds" width="860"></a>
  </video>
</p>

<p align="center"><sub>47 seconds, with voice-over. <a href="video/citation-lens.mp4">Open the video file</a> if it does not play here.</sub></p>

A literature-research plugin for Codex and Claude Code.
Give it a research question; it finds the defining papers, walks their citation graph forward and backward like [Connected Papers](https://www.connectedpapers.com/about), surfaces the newest preprints, and hands the agent compact previews it can verify and cite.

## How it works

1. **Search broadly, in parallel.** One call runs every query variant on Semantic Scholar, OpenAlex and arXiv at once, merges duplicate versions, and fuses the rankings.
   A recent lane reserves every third card for work from the last two years when available.
2. **Expand the citation graph.** From 3-6 seed papers Lens collects their references, their citing papers (newest from Semantic Scholar, most cited from OpenAlex) and Semantic Scholar's similar papers, then reads the candidates' own reference lists to measure bibliographic coupling and co-citation.
   Neighbors come back in three lanes: **foundation** (prior work the graph cites), **follow-up** (work that cites or resembles the seeds, ranked by shared references) and **recent** (the last two years).
3. **Preview, then read.** Compact cards identify the paper, explain its selection and carry a verbatim abstract excerpt.
   Abstracts, full text and figures are separate, on-demand calls.
   Snapshots page from a local cache without new requests.
   Complete cached abstracts read locally; publisher DOI aliases stay attached to arXiv records.
   Shared downloads, provider deadlines and throttling pauses avoid repeated work and bound waits.
4. **Cite real links.** Edges are citing -> cited pairs taken from reference lists.
   If citation indexes fail, arXiv bibliographies can supply backward links with a source fragment and matched paper ID; unresolved references remain visible.
   Crossref can resolve exact DOI metadata for those links and missing-evidence reads; it supplies paper metadata while the bibliography remains the edge proof.
   Shared references and similarity never become citation links.

The agent stays in charge of judgment: Lens gathers, ranks and previews; the agent screens, verifies and writes.

## See it work

Frames from the video, from one recorded Claude Code run on "What's new in linear-time sequence modeling?"

| 1. Search wide | 2. Pick seeds |
| :---: | :---: |
| ![One call fans every query variant out to Semantic Scholar, OpenAlex and arXiv](assets/screens/search.png) | ![The agent picks the seed papers; Lens only gathers](assets/screens/seeds.png) |
| **3. Walk the graph** | **4. Verify links** |
| ![Seeds in the middle, foundations on the left, follow-ups on the right, recent work on top](assets/screens/graph.png) | ![A claimed link is confirmed in the paper's own reference list](assets/screens/verify.png) |

**5. Compare.** The same agent with and without Citation Lens.
In this run verified links went from 7 to 23 and tool calls from 8 to 5, at the cost of 30 more seconds.
Across all 18 paired runs Lens verified more links in 13 and fewer in 4; the overall judge still preferred Claude alone, see [Results](#results).

![Claude Code alone against Claude Code with Citation Lens](assets/screens/compare.png)

Open [`demo/index.html`](demo/index.html) for a side-by-side replay of recorded runs, or read the [architecture diagrams](docs/ARCHITECTURE.md) for the whole system, a typical session and what `research_expand` does.
Verified DOI or arXiv IDs found through native search can go straight to reading, without another title search.

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

Each host was tested on 10 held-out tasks, twice each, under matched conditions.
After verification repairs and masking explicit tool names, the order-swapped judge favored Lens in **17 Codex pairs**, with one loss and two ties.
For **Claude Code, Lens won seven, lost ten and tied three**.
It does not meet our Claude quality goal yet: higher paper counts came with lower must-find recall and more input tokens.

| Mean per attempt | Codex | Codex + Lens | Claude | Claude + Lens |
| --- | ---: | ---: | ---: | ---: |
| Directly relevant papers | 13.1 | 19.2 | 17.7 | 20.4 |
| Recent useful papers | 5.6 | 9.1 | 7.7 | 8.1 |
| Verified links between useful papers | 2.7 | 8.5 | 4.2 | 4.6 |
| Must-find recall | 0.707 | 0.732 | 0.848 | 0.778 |
| Completed attempts | 19 / 20 | 18 / 20 | 19 / 20 | 20 / 20 |
| Seconds | 258 | 335 | 131 | 126 |
| Reported input tokens, including cached | 275k | 386k | 47k | 109k |

Failed attempts receive zero quality credit.
Three Codex timeouts lack token usage; token means exclude them.
These are historical keyless measurements with model-based judges, from the packages recorded in each export.
Inspected tasks become regression cases for future changes; new behavior needs fresh held-out evidence.
Three later development candidates failed the saved quality and efficiency requirements.
Version 0.2.2 ships separately verified correctness repairs, including exact DOI recovery; it has no demonstrated end-to-end performance gain.

[Protocol, failures and reproduction](docs/EVALUATION.md) · [Codex evidence](docs/results/codex-heldout.json) · [Claude evidence](docs/results/claude-heldout.json)
[Development comparisons](docs/DEVELOPMENT.md)
[DOI recovery checks](docs/DOI_RECOVERY.md)

[Design and research basis](docs/DESIGN.md) · [Privacy](docs/PRIVACY.md) ·
[Development](docs/PUBLISHING.md) · [MIT license](LICENSE)
