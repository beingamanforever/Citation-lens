# DOI recovery

An arXiv bibliography can cite a journal paper only by DOI.
If scholarly indexes are unavailable, that entry previously stayed unresolved.
Crossref now supplies exact DOI metadata, while the bibliography item remains the citation proof.
Missing abstracts, unsupported notation and unresolved identities stay visible.

This is a retrieval capability, not a demonstrated improvement in end-to-end research quality, latency or tokens.
The earlier development candidates remain rejected.

## Fixed live checks

Three DOI cases and one arXiv seed were fixed before any candidate requests, using an official API example, two publisher URLs and the first result of an independent arXiv search.
The sample was not selected from benchmark tasks or filtered for working providers.
Every selected case is retained; none was replaced.
Calls used frozen source, fresh local caches and no provider keys or model inference.
Live observations precede the final deadline, cancellation-pacing and structured-abstract repairs found in review; caller tests cover those repairs.

| Fixed DOI | Normal plugin read | Separate Crossref batch |
| --- | --- | --- |
| [10.1037/0003-066x.59.1.29](https://doi.org/10.1037/0003-066x.59.1.29) | Abstract from OpenAlex | Exact metadata; no abstract or full date |
| [10.1371/journal.pone.0000308](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0000308) | Abstract from Semantic Scholar | Exact metadata; no abstract |
| [10.1038/nature12373](https://www.nature.com/articles/nature12373) | Abstract from Semantic Scholar; arXiv alias retained | Exact metadata; no abstract |

The three initial reads made four HTTP requests in total.
Repeating all three made zero new requests.
The separate Crossref batch resolved all three requested identities in one HTTP request, with zero abstracts.
These observations do not establish a general speed advantage or abstract coverage rate.

Backward expansion of [Cavity QED based on strongly localized modes](https://arxiv.org/abs/2509.04739) returned 63 indexed references and 48 ranked graph records.
Semantic Scholar succeeded, so primary bibliography fallback was not exercised by this live call.
Paging the graph made no new requests.
Reading its first DOI-primary node, [10.1038/nature06234](https://doi.org/10.1038/nature06234), made three requests and still returned no abstract.
That missing evidence is retained.

The [metadata-only receipt](results/doi-recovery-capability.json) records each call, wall time, payload bytes, cumulative request counters and provenance.
Raw responses and the frozen source remain in the local capability archive; paper abstracts are not republished here.

## What the caller tests establish

Real MCP tests cover exact DOI reads after index failures, DOI-only bibliography recovery with the original source fragment, local graph and abstract reuse, and unavailable unsafe abstracts.
An MCP timeout regression retains 20 completed DOI records and their primary bibliography proofs while a later record times out, with the gap still reported.
Provider tests cover unordered identity matching, malformed records, shared hydration limits, partial results, cancellation and Crossref request serialization.
Windows CI exposed an early timer wakeup that shortened request spacing; pacing now rechecks its deadline, with the original spacing assertion retained and a deterministic regression for Crossref and arXiv.
The five public tools and default keyword providers are unchanged.

Crossref metadata does not prove a citation edge, supply forward citation discovery or guarantee full text.
Its pacing applies per plugin process, so another client can still share the public rate limit.
See the [Crossref API contract](https://github.com/CrossRef/rest-api-doc) and [evaluation protocol](EVALUATION.md).
