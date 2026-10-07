# Citation Lens 0.1 preview

Local research plugin for Codex and Claude Code. Search papers, expand a bounded
forward/backward citation graph, screen compact cards, and selectively read paper
text and actual figures/PDF pages. Shared backend, five tools, SQLite caching.
Batch up to nine query/provider searches, deduplicate identities and page results;
use the bundled SDK client to filter intermediate cards outside model context.

Install instructions are in the repository README. This release includes the
self-contained plugin ZIP, Python wheel/sdist, synthetic benchmark results,
available live-provider results and SHA-256 checksums. The wheel's command is
`citation-lens`; repo marketplaces install the plugin directly from this repository.

Local verification: 53 tests, Ruff lint/format, MCP SDK stdio integration and actual
PDF image rendering passed. Claude plugin and marketplace validation passed.
Native Codex and Claude Code installs passed in isolated configurations.
Release publication additionally waits for the six-platform/Python CI matrix.

Synthetic fixture: two search hits expand to six papers/seven edges. Twenty cards
are 98.34% smaller in UTF-8 JSON bytes than twenty fabricated full texts. Warm-cache
requests make no additional upstream calls. These do not measure research accuracy,
real host token savings or live latency. See BENCHMARKS.md for definitions.

Live OpenAlex benchmark: five search hits expanded from three seeds to 21 papers
and 18 citation edges. Cards for the same two readable FlashAttention papers were
99.08% smaller than extracted text plus metadata (2,011 versus 218,270 UTF-8 bytes).
This measures provider workflow and payload size, not model tokens or research quality.

Paired efficiency fixtures: overlapping search results reduce four calls to one
and approximately 7,200 to 2,100 reference tokens; thirteen-page reading retains
the same complete text while reducing approximately 76,000 to 30,000 reference tokens.
These use cl100k_base, not actual host billing; disjoint complete screening costs more.

Preview limits: citation sampling and provider coverage are incomplete; PDFs can
lose text structure; paired host research quality remains unverified.
Live-provider JSON retains anonymous-access/extraction failures.
Not submitted to either official plugin directory or PyPI. Local single-user stdio
server only; public hosting needs additional isolation and authentication.
