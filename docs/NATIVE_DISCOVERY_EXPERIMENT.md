# Native discovery with selective Lens enrichment

Status: authentication blocked; the candidate was not selected or shipped.
The original preregistration was saved before inference and remains unchanged in the local study archive.
This is one new workflow hypothesis after the released v0.2.2 correctness repairs.
The three earlier performance candidates and their exhausted stopping rule remain rejected and unchanged.
This study does not reopen that experiment or relax its retention requirements.

## Hypothesis

Native discovery may preserve the host's stronger candidate selection while Lens adds checkable citation structure and compact evidence reads.
The candidate guide starts with native discovery across the user's approaches and time range, then uses the existing Lens tools for specific gaps, complementary seeds and unresolved evidence.
It reviews both native and graph candidates before reading or writing.
It does not require Lens search or a large expansion for every question.

Saved development traces show current-year cards available before an older-work-heavy read batch.
Those provider dates and omissions do not establish relevance or prove that every omitted paper belonged in the answer.
Historical Claude results show higher Lens paper counts with lower anchor recall and worse pairwise outcomes.
These observations motivate the hypothesis; they do not prove its explanation.

Runtime code, the five signatures, default providers, ranking weights, evaluator, task requests and grading criteria stay unchanged.
The research skill is the only candidate product change.
There are no paper identifiers, topic branches, hidden grading fields or mandatory paper quotas in it.

## Fixed development comparison

- Original four development tasks, each run twice in both arms for both hosts: 32 attempts.
- Codex: `gpt-6.1-sol`, high effort.
- Claude Code: `claude-opus-5-5`, high effort.
- Both arms retain native research; only the Lens arm receives the same frozen candidate tools and guide.
- Existing limits: 420 seconds, eight research calls and at most 25 papers.
- Cold inference caches, two concurrent attempts, existing alternating arm order and UTC run dates recorded by the unchanged runner.
- Hosts run sequentially, Claude first, then Codex; attempts within each host use the same frozen request date.
- Keyless providers; record availability and throttling without replacing adverse attempts.
- Revision-4 factual checks and the existing blinded, order-swapped judge; `gpt-6.1-sol`, medium effort, four judge jobs.
- Each host's grading starts from a separate copy of the archived initial verification cache, with its origin recorded before grading.

Freeze source, guide, runner, grader, tasks and this plan before inference.
Save every attempt, event, original answer, usage record, provider condition, judge request and vote.
Missing usage remains unknown.
There are no extra repetitions, favorable retries, source repairs during this study or changes to grading after outcomes.
Authentication or organization-spend failures remain recorded; do not switch accounts or raise limits.
If such an external failure prevents continued inference, stop the process, preserve completed and unstarted attempts separately, and make no completed-study claim.
Resume only the unchanged frozen study after external access returns, without rerunning retained failures.

## Selection rule

Each host must pass separately, using all-attempt means and the existing summary arithmetic.
Failed attempts receive zero quality credit; answered-only metrics remain supplementary.

1. More overall pairwise wins than losses.
2. Higher mean useful papers, recent useful papers and verified links between useful papers than its matched native control.
3. Precision at least native minus 0.05, and no more unfound papers.
4. No lower mean core papers, anchor recall or approach coverage, and no lower quote-verification rate.
5. All eight Lens attempts complete within the original budgets.

Missing evidence for a selection predicate cannot establish a pass.
No pooled cross-host score can rescue a failing host.
Inspect actual tool order and shortlist behavior to see whether the guide influenced the workflow; tool order itself is not a correctness grader because valid research paths differ.

Reject this candidate if either host fails.
Keep all results and stop this workflow candidate without tuning or adding attempts.
The separate private confirmation set remains unread and unused unless both hosts earn selection.
A selected candidate must be frozen before confirmation, with the same grading and per-host safeguards, all 10 confirmation tasks and two repetitions.
Any confirmation failure remains visible and cannot trigger task replacement, grader tuning or paper-specific changes.

Time, input tokens, cached tokens and payload bytes are separate outcomes, including regressions.
A quality pass does not establish a speed or cost improvement.
This two-arm design tests the candidate against native research; it is not a contemporaneous ablation against the released Lens-first guide.
The existing small development set and model-based judge limit generalization; human calibration is not claimed.

## Research basis

[Anthropic's agent-evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) emphasizes repeated trials, inspecting failure transcripts and separating evaluator faults from agent failures.
[Its tool-design guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) recommends realistic tasks and compact useful responses while allowing multiple valid strategies.
The existing [evaluation protocol](EVALUATION.md) and [rejected development evidence](DEVELOPMENT.md) remain authoritative for their historical experiments.

## Outcomes

Claude's 16 development attempts all failed before research with `Failed to authenticate: OAuth session expired and could not be refreshed`.
Neither arm made a research call or returned an answer.
The runner completed these immediate failures before the next status inspection; every original attempt and event log is preserved.
Codex's 16 planned attempts were not started after the authentication failure was observed.
No relevance or pairwise grading ran because there were no research answers.

The CLI reported zero input and output tokens for these pre-inference failures.
Those counts and their wall times do not measure research cost or latency.
Provider preflight recorded Semantic Scholar at 1/3 successes, OpenAlex at 3/3 with a remaining keyless budget of zero, and arXiv at 3/3.
These checks do not establish successful research access after authentication.

The candidate did not earn selection; this study supplies no evidence about its research quality.
The eight retained Claude Lens failures prevent this battery from meeting the all-eight-complete selection requirement.
Restored access cannot replace these failures or unlock confirmation from this battery.
The released guide was restored, and the experimental guide, patch, source, initial cache copies and plan remain in `output/evals-native-discovery-development/` outside the repository.
The private confirmation set remains unread and unused.
Authentication access must be restored before further Claude inference; none of these failed attempts may be replaced silently.

[All 16 failure receipts and frozen conditions](results/native-discovery-development.json).
