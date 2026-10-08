# Usage

## Tools

| Tool | Does | Returns |
| --- | --- | --- |
| `research_search` | 1-4 query variants on Semantic Scholar, OpenAlex and arXiv at once | fused, deduplicated cards; every third from the last two years; per-search errors |
| `research_expand` | one citation hop from 1-8 seeds: references, citing papers, similar papers | foundation / follow-up / recent cards, citing -> cited edges, per-seed coverage |
| `research_graph` | pages a stored search or graph snapshot, no network | the next cards and the edges they complete |
| `research_read` | `part="abstract"` for up to 30 papers; `"outline"` or `"text"` for up to 3 | abstracts and metadata, or headings, figures and text slices |
| `research_visual` | an HTML figure or rendered PDF page | the image, for vision-capable hosts |

Paper IDs are `ARXIV:2205.14135`, `DOI:10.1038/...`, `OA:W...` or `S2:<hash>`; arXiv, DOI, OpenAlex and Semantic Scholar URLs also work.

A card looks like this (about 500 bytes):

```json
{"id": "ARXIV:2310.03294", "title": "DISTFLASHATTN: Distributed Memory-efficient Attention for Long-context LLMs Training",
 "year": 2023, "cites": 48, "url": "https://arxiv.org/abs/2310.03294", "lane": "follow-up",
 "why": "cites 3/3 seeds; shares 10 seed refs; cited by 3 graph papers",
 "snippet": "In this paper, we introduce DISTFLASHATTN, a distributed memory-efficient attention mechanism optimized for long-context LLMs training."}
```

`snippet` is the abstract sentence that best matches the query, verbatim.
`edges` are `[citing, cited]` pairs from reference lists; each appears once across pages.

## Standalone MCP

Register the server directly instead of the plugin (not both):

```bash
git clone https://github.com/beingamanforever/Citation-lens.git && cd Citation-lens
```

```bash
codex mcp add citation-lens -- uv run --no-project --with-requirements "$PWD/requirements-runtime.txt" python "$PWD/run.py"
```

```bash
claude mcp add --transport stdio citation-lens -- uv run --no-project --with-requirements "$PWD/requirements-runtime.txt" python "$PWD/run.py"
```

Set `OPENALEX_API_KEY` and `SEMANTIC_SCHOLAR_API_KEY` in the environment the host passes to the server.
`CITATION_LENS_DATA` moves the local cache (default `~/.cache/citation-lens`).

## Scripting

With code execution, run discovery outside the model context and print only what you keep:

```bash
python scripts/search.py "FlashAttention" "ring attention" --expand 3 --show 15
```
