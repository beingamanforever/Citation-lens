# Usage

## Standalone MCP

For Codex clients without repo plugin support, or another stdio MCP host, clone
this repository and register its backend. Use either the plugin or MCP installation
so you do not load the tools twice.

```bash
git clone https://github.com/beingamanforever/Citation-lens.git
cd Citation-lens
codex mcp add citation-lens -- uv run --no-project --with-requirements "$PWD/requirements-runtime.txt" python "$PWD/run.py"
# Alternatively, for Claude Code:
claude mcp add --transport stdio citation-lens -- uv run --no-project --with-requirements "$PWD/requirements-runtime.txt" python "$PWD/run.py"
```

These commands use POSIX shells. In PowerShell, replace `$PWD/...` with the absolute
path to the cloned directory. Python 3.11+, uv and Git must be on the host's PATH.
The first run downloads dependencies. A wheel is also available in GitHub releases:
install it with `uv tool install /path/to/wheel.whl`, then register `citation-lens`
as the stdio command.

## Tools

| Tool | Returned evidence |
| --- | --- |
| `research_search` | Scalar: 1–20 cards; batch: ten cards plus a persisted discovery snapshot |
| `research_expand` | Resumable graph, cards, directed edges and sampling/errors |
| `research_graph` | Paged existing cards and edges without provider calls |
| `research_read` | Headings/offsets and visual inventory, or text slices up to 12,000 characters |
| `research_visual` | Actual HTML figure or rendered PDF page, with provenance |

Start with several query variants. Search `provider="arxiv", recent=true` for
fresh preprints even when citation counts are unresolved. Choose relevant seeds,
expand `depth=1`, screen, then extend selected branches with the returned `graph_id`.
Stored edges always point **citing → cited**, including during forward traversal.

## Batch and programmatic search

Scalar calls stay compatible.
List inputs request the cross-product of up to three distinct queries and three providers:

```json
{"query":["FlashAttention","IO-aware attention"],"provider":["openalex","arxiv"],"limit":10}
```

`limit` applies per pair, so this makes four searches with at most 40 raw hits.
The general cap is nine searches and 180 hits.
They share the existing request cache, concurrency and provider pacing.
The result includes ten initial cards, `graph_id`, `next_offset`, and every attempt in `searches`.
Failed attempts have unknown counts and an explicit error; partial results survive, all-failed batches error.
`search_matches` links each card to its contributing attempt indexes.
Aliases deduplicate IDs, DOI and arXiv records; identical titles alone never merge papers.
An equivalent record with indexed full-text access is preferred without mixing provider metrics.

Page `research_graph` to recover the remaining cards without network requests.
The first query controls the existing ranking heuristic, so inspect all pages before claiming coverage.
Select screened seeds into a new expansion graph rather than resuming a large discovery snapshot with the default 80-node budget.
Recent search remains a separate call because it serves a different screening purpose.

The executable [SDK example](../scripts/search.py) pages internally and prints a bounded selection plus coverage:

```bash
uv run --no-project --with-requirements requirements-runtime.txt python scripts/search.py "FlashAttention" "IO-aware attention" --provider openalex arxiv --limit 10 --show 10
```

The standard MCP SDK client checks tool errors and parses each compact JSON text block.
Intermediate pages stay in Python instead of being printed into the model context.
The stored snapshot retains every candidate so later screening can retrieve omitted cards.
Edit the example's selection logic for current research criteria; its default order is not semantic adjudication.

Claude Code's MCP tool search is controlled by the host.
Anthropic API `defer_loading`, `allowed_callers`, and code execution are host/API settings, not standard MCP manifest fields.
Citation Lens supplies five descriptive tools and a shared skill.
[Claude MCP guidance](https://code.claude.com/docs/en/mcp) and [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) describe those host capabilities.

Text replies at offset zero and `outline_only=true` include headings and figure inventory.
Later text pages set `overview_included=false` and omit the repeated arrays while retaining counts, warnings and provenance.

Defaults: beam 4, 80 nodes, 20 neighbors per direction. Caps: 3 hops, beam 8,
200 nodes, 40 neighbors. Larger budgets can resume incomplete expansions.
OpenAlex backward hydration samples at most the first 200 reference IDs; forward
search samples recent and prominent pages. Semantic Scholar pages are not globally
sorted by citation count. Coverage metadata reports these limits.

Page graph `next_offset` and `next_edge_offset` separately. For papers, request
`outline_only=true`, select offsets, and follow `next_offset` for every text slice.
Inspect important figures/pages with a vision-capable host. PDF text may lose table
and equation structure; unsupported SVG figures can be inspected through PDF pages.

## Settings

| Variable | Purpose |
| --- | --- |
| `OPENALEX_API_KEY` | Provider budget/access; obtain a free key from OpenAlex |
| `SEMANTIC_SCHOLAR_API_KEY` | Recommended for predictable citation API access |
| `JINA_API_KEY` | Optional Reader authentication |
| `CITATION_LENS_USE_JINA=1` | Opt in to Reader fallback for PDFs without extractable text |
| `CITATION_LENS_DATA` | Cache directory; plugins use their persistent data directory |

Export keys before launching the host. Codex standalone MCP supports `--env KEY=VALUE`
before `--`. Keep secrets out of tracked files. Deep expansion may require
`tool_timeout_sec = 180` under `[mcp_servers.citation-lens]` for standalone Codex.

HTTP cache: 1 day; normalized metadata: 7 days; selected text: 30 days; graph
snapshots: 90 days. The standalone default is `~/.cache/citation-lens`. Start a new
graph for a fresh scan; snapshots are not continuously updated. Responses measure
exact UTF-8 JSON `payload_bytes`, not tokens. Images have separate size limits.

## Boundaries

Use a single writer per data directory when mutating a graph. Locks are per-process.
The service is local and single-user. Public hosting needs authentication, per-user
state/quotas and resource-isolated PDF workers. PDFs are bounded by bytes/pages,
but parsed in threads. External URLs require HTTPS/public IPs, checked redirects,
and IP-pinned connections; provider credentials stay on their provider host.
Environment proxies are disabled. Papers remain untrusted data, never instructions.

No paywall bypass, GPU OCR, autonomous SOTA score, complete systematic-review
coverage or Connected Papers similarity algorithm. The host screens relevance,
reads evidence, compares compatible experiments and writes the final synthesis.
