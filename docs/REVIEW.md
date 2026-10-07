# Implementation review

Reviewed the provider adapters, graph mutation, cache, text/visual extraction,
MCP serialization and distribution assets after implementation.

## Issues corrected

- Preserved directed edges during forward traversal; tested cycles/shared ancestors.
- Rejected missing graph IDs instead of silently creating a replacement graph.
- Made node-limited expansion retryable, and re-expand when neighbor budget grows.
- Deduplicated DOI/arXiv aliases across providers; avoided title-only fuzzy merges.
- Added native arXiv discovery and retained full-text access when citation indexing fails.
- Preserved explicitly requested arXiv revisions without changing shared metadata.
- Kept recent low-citation candidates in the selection instead of sorting only by citations.
- Coalesced duplicate requests; cached bytes no longer need repeat DNS resolution.
- Pinned external fetches to validated public IPs while preserving HTTPS SNI/Host;
  checked redirects and removed credentials on cross-origin redirection.
- Fixed PDFium page cleanup after the actual renderer test exposed unsupported
  page context-manager use. Verified colored diagram pixels and extracted text.
- Sent one compact JSON text block through MCP instead of duplicate text and
  structuredContent; checked its exact UTF-8 payload measurement through the SDK.
- Preserved LaTeX before Markdown escaping and rejected generic HTML error pages.
- Pinned SDK-compatible settings dependencies after the packaged launch exposed
  a forward-reference warning in a newer settings release; verified a warning-free launch.
- Kept partial provider errors, sampling bounds and extraction warnings visible.
- Serialized PDFium rendering with a process-wide lock after concurrent rendering
  crashed the process; added direct 24-request and MCP 12-request concurrency checks.
- Replaced the Claude-only command with one shared research skill for both hosts.
- Restored mathematical symbols in figure captions after a real FlashAttention-2
  figure request exposed internal extraction placeholders.
- Excluded image-only PDFs from readable-paper payload benchmarks while retaining
  their warnings and failure records.
- Restricted cache mutation locking to each graph, allowing independent graphs
  to proceed concurrently. Added an expiry index for cache cleanup.

## Verification evidence

Fifty-three tests, lint/format, wheel/sdist build and Claude manifest/marketplace
validation passed locally. GitHub Actions records release checks. Tests exercise provider contracts with
synthetic HTTP fixtures, actual PDF rendering and official SDK stdio clients.
The generated plugin adapter is independently launched through the SDK.
Native Codex and Claude Code marketplace installs passed in isolated configuration directories.
The live OpenAlex search, citation expansion and paired arXiv full-text benchmark passed;
see BENCHMARKS.md and live-benchmark.json for exact results and limitations.
All five tools also passed through the generated uv MCP launch against real sources,
including a selected FlashAttention-2 text slice and its actual first diagram.
Batch search also passed through the actual SDK client: two query variants across
OpenAlex and arXiv, four HTTP requests, twelve retained candidates and no errors.
Independent review covered concurrent search, transitive identity merging, partial
outages, lossless paged reading and configuration propagation into the client.
The review caught and fixed loss of an alternate provider's readable full-text
record during deduplication; the selected representative now preserves one provider's complete metadata.
Efficiency fixtures retain identical DOI sets and report both initial and complete
screening costs, including higher complete costs for disjoint results.

Synthetic benchmark: see benchmark.json. Twenty metadata cards reduce discovery
payload by approximately 98.3% versus twenty fabricated full-text records. Thirty
warm-cache rounds of eight queries perform zero additional upstream requests.
These are software measurements, not real-network or research-quality results.

## Remaining release gates and ceilings

- OpenAlex and selected arXiv text retrieval passed with anonymous live access.
  Semantic Scholar, all provider quota tiers and every publisher are not verified.
- Whole-host research accuracy and token usage remain unmeasured; EVALUATION.md
  specifies paired tasks and human adjudication. Offline correctness cannot prove
  a better literature review.
- Both native CLI installs passed; desktop workflow and paired host research
  quality evaluations remain unmeasured.
- Cross-platform CI is configured but has not run here. Local verification is
  macOS ARM64, Python 3.12.13, MCP SDK 1.28.1.
- Graph writes are serialized per graph within one server process. Concurrent
  mutation of the same graph ID across multiple processes can overwrite snapshots;
  separate data directories or single-writer usage are required for v0.1.
- PDF parsing runs in a thread, not a resource-isolated worker; public multi-user
  hosting needs worker isolation, authentication and per-user state/quotas.
- Metadata/text TTLs trade freshness for latency. A new scan needs fresh queries
  and a new graph; snapshots are not continuously updated literature reviews.
- Reference/citation sampling is disclosed and deliberately incomplete. No claim
  of reproducing Connected Papers' co-citation/bibliographic-coupling similarity.
- Distribution is through the public GitHub repo marketplace and gated preview
  releases. No official-directory submission or PyPI publication is included.
