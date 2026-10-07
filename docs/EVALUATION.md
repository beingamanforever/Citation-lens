# Paired host research evaluation

The deterministic tests verify software behavior, not the quality of an LLM's
literature review. Run paired host evaluations on fixed dates and provider caches.
Baseline: the same model with its ordinary search workflow. Treatment: Citation
Lens, same time/token budget. Randomize ordering and blind the human graders.

| Task | Required evidence and checks |
| --- | --- |
| Efficient attention under long-context inference | Start from FlashAttention, find backward foundations and later methods; separate prefill, decode and training; inspect speed/memory tables |
| Document OCR with tables and handwriting | Discover distinct approaches; include fresh preprints; compare datasets and output representations; inspect layout/table diagrams |
| Instruction-following RL | Find foundational and recent GRPO variants; read objectives and evaluation protocols; distinguish results from incompatible setups |
| Retrieval for agent tool selection | Search terminology variants; include approaches without a citation connection to the seed; inspect tool-search accuracy and latency evidence |
| A topic outside CS: battery degradation | Use OpenAlex; check reference matching, non-arXiv coverage and unavailable full texts; avoid treating abstract-only results as fully read |
| Newly posted arXiv paper missing from S2 | Search/read native arXiv record; graph failure must be disclosed without losing access to the paper |

For each topic, two domain-aware readers create an adjudicated candidate list
with inclusion criteria and evidence anchors. It is a scoped reference set, not
an assertion of global completeness. Keep evaluation topics separate from tuning.

Measure:

- Screening precision@N and recall against the adjudicated set, with confidence intervals.
- Recent relevant paper inclusion and diversity across competing approaches.
- Citation edge correctness, deduplication precision, explicit missing coverage.
- Claim support rate against cited section/page/figure, and incompatible comparisons.
- Correct interpretation of diagrams/tables/equations, scored by domain readers.
- Search-to-first-card and search-to-N-selected-paper p50/p95; failure/rate-limit rate.
- Actual harness input/output tokens, tool calls, HTTP calls, costs and cache hit rate.
- Time to a useful synthesis and unsupported-SOTA-claim count.

Minimum acceptance: zero fabricated source identifiers or graph edges in reviewed
runs, all unavailable evidence disclosed, no critical factual error in interpreted
visuals, and no budget breach. Record baseline/treatment scores before deciding
whether improvement is demonstrated. Do not substitute unit tests for these gates.

`scripts/benchmark.py` measures fixture citation expansion, cache behavior and
JSON payload size, reference-token counts and paired batch/paged-read efficiency.
Fixtures compare identical retrieval budgets, retained DOI sets and partial failures,
with initial and complete-screening costs reported separately.
`cl100k_base` counts are reference encoding measurements; actual host usage includes
prompts, tool discovery, images and inference and must be measured separately.
`scripts/live_benchmark.py` records live provider behavior in
release CI. Neither runs a host model or measures research quality. The preview
can be distributed with this limitation; quality claims require the paired
evaluations above. Preserve failures rather than substituting fabricated scores.
