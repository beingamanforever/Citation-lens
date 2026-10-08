# Evaluation

Question: does adding Citation Lens make Codex or Claude Code find more relevant, verified papers - including the foundations, influential follow-ups and newest work - and more real citation links than the same host's native web research, under a matched budget?

## Protocol

**Arms.** Each within-host comparison uses the same model and reasoning effort, prompt, output schema, deadline and call budget, and both arms keep native live web research.
The lens arm adds the five Lens tools and their playbook (`skills/research/SKILL.md`).
Each Codex attempt starts from a fresh home.
Claude attempts use fresh working directories, disable session persistence and external settings, and restrict tools to native web search/fetch plus Lens in the Lens arm.

**Tasks.** [`evals/tasks.json`](../evals/tasks.json) holds 14 research requests: 4 development tasks for hill-climbing and 10 held-out tasks run once at the end.
They span four kinds:

| Kind | Tests | Examples |
| --- | --- | --- |
| survey | covering required approaches | exact attention, speculative decoding, quantization, retrieval |
| frontier | what is new in the last 18 months | state-space models, ML weather forecasting |
| lineage | walking a citation tree from one paper | LoRA, DPO |
| cross-domain | journals outside computer science | base/prime editing, battery cycle life, direct air capture |

The task file contains 75 reference anchors, with 23 labeled recent.
Agents never see anchors or the required-approach list.

**Budget.** 420 seconds per attempt, at most 8 research tool calls, at most 25 papers.
The call budget counts host tool calls; a Lens call can query several providers, so upstream request counts are not matched.
The current runner checks the returned schema, call count, paper count and host completion status.
Individual tool failures may be recovered from; host failures and exceeded budgets cannot count as completed answers.
The answer lists papers (title, URL, role, reason, verbatim quote), citing -> cited links, a summary and gaps.

## Grading

[`evals/grade.py`](../evals/grade.py) separates facts from judgment.

Code checks:
- **Identity.** Each paper's URL and title are resolved through scholarly indexes.
  "Not found" means no index knows the title; "wrong URL" means the URL names a different paper.
  Unresolved destinations and title mismatches stay explicitly unverified and receive no useful-paper credit.
  Noncanonical URLs require page metadata or title-page evidence rather than a matching title search alone.
- **Recency.** Publication date within the two-year window, from index metadata.
- **Anchors.** Reference anchors found, matched by stable arXiv, DOI or provider identifiers.
- **Citations.** Match an individual bibliography item by identifier or complete title with author/year evidence; index reference identifiers supply a separately labeled fallback.
  Each verified edge retains its proof source.
- **Quotes.** Each quote must appear in the abstract or the arXiv full text with only whitespace normalized.
  Mathematical signs, operators and punctuation are preserved.

LLM judge (blinded):
- **Relevance.** For each task, every paper either arm returned is pooled, shuffled and labeled once on a 0/1/2 scale from its index title and abstract, with the required approach it covers.
  Labels are keyed by task and paper; a paper can have different relevance in different tasks.
  The judge never sees which arm proposed a paper.
- **Pairwise.** The two answers are compared on coverage, foundations, recency, precision, synthesis and overall.
  Each pair is judged twice with the order swapped; a disagreement is a tie ([position bias][judge]).
  Revision 3 masks explicit product/tool identifiers symmetrically in reasons, summaries and gaps before constructing judge prompts.
  Scientific titles and source answers remain unchanged; workflow and writing style can still reveal an arm.

Metrics per attempt: useful papers (found, right URL, relevance >= 1), core papers (relevance 2), precision, recent useful, prominent useful (>= 100 citations), anchor recall, approach coverage, verified citations between useful papers, quote verbatim rate, not-found and wrong-URL counts, seconds, tool calls, tokens, and bytes returned by Lens.

**Lens beats a host's native research** on a split when it wins more pairwise comparisons than it loses, finds more useful papers, more recent useful papers and more verified useful citations on average, without lower precision (by more than 0.05) or more unfound papers.
Completion rate, latency and token use remain separate outcomes; passing this quality rule does not establish a speed or cost advantage.

## Running

```bash
python evals/run.py --split development --out ../output/evals/dev-1 --jobs 2
python evals/grade.py ../output/evals/dev-1
```

`--reps` repeats each task; `--shared-cache` reuses provider responses across lens attempts (faster and gentler on keyless APIs, and disclosed in the manifest).
Records keep every attempt, including timeouts, with full event logs.
New runs freeze the package, runner, grader, tasks and protocol, record concurrency and cache conditions, and reject incompatible resumes.
New grading keeps the judge prompts, responses and individual order-swapped votes.
Existing grades are preserved; use `--out` to write a correction in a new directory.
Missing usage remains unknown; token means use only attempts with reported usage.

## Initial results, before verification audit

Run on 2026-10-08.
Both agents are Codex with `gpt-6.1-sol` at high effort; the judge is the same model at medium effort.
Keyless conditions were poor all day: Semantic Scholar's shared pool answered few requests, OpenAlex's keyless daily budget ran out, and arXiv throttled at times, so Lens ran degraded throughout (provider health is recorded in each run manifest).

### Development tasks: hill-climbing history

Every iteration is kept in `output/evals-v3/`.
Native Codex was re-run whenever the prompt or deadline changed.

| Iteration | Change | Lens answered | Outcome |
| --- | --- | --- | --- |
| 1 | first version, 300 s deadline | 0 / 8 | the agent paged and read 10-abstract batches until the deadline |
| 2 | playbook: search once, expand once, read once; 30-abstract reads | 0 / 2 (stopped) | still out of time at 300 s while providers throttled |
| 3 | 420 s for both arms; per-provider deadlines; 1000-character batch abstracts | 6 / 8 | better on every quality metric when answered; 2 timeouts |
| 4 | at most 120 edges and 60 cards per page | 8 / 8 | wins 6 of 8 pairwise comparisons, ties 2, loses none |

Iteration 4, mean per attempt (2 repetitions x 4 tasks per arm):

| Metric | Native Codex | Codex + Lens |
| --- | ---: | ---: |
| Useful papers | 17.8 | 24.3 |
| Precision | 0.956 | 0.975 |
| Recent useful papers | 5.5 | 8.3 |
| Prominent useful papers (>= 100 citations) | 5.1 | 6.4 |
| Anchor recall | 0.925 | 0.95 |
| Verified citations between useful papers | 11.3 | 30.6 |
| Quotes matched by the original checker | 88% | 95% |
| Seconds | 249 | 368 |
| Input tokens (including cached) | 318k | 425k |

Lens is slower and uses more tokens; the gain is in what it finds and how well it is linked.

### Held-out tasks

The 10 held-out tasks ran once each per repetition (2 repetitions) on plugin version `850d687aa043`, after development stopped; nothing was tuned on them.
At the start, Semantic Scholar answered 0 of 3 probes and OpenAlex had 980 keyless credits.

| Metric (mean per attempt; a timeout counts as zero) | Native Codex | Codex + Lens |
| --- | ---: | ---: |
| Answered | 19 / 20 | 18 / 20 |
| Pairwise overall (judged twice, order swapped) | 1 win (forfeit) | 18 wins, 1 tie |
| Useful papers | 15.3 | 22.0 |
| Precision (answered attempts) | 0.969 | 0.991 |
| Recent useful papers | 6.1 | 9.5 |
| Prominent useful papers | 2.0 | 3.0 |
| Anchor recall | 0.78 | 0.78 |
| Required approaches covered (answered attempts) | 0.99 | 1.00 |
| Citation links credited by the original checker | 8.2 | 13.6 |
| Wrong URLs per attempt | 0.30 | 0.05 |
| Quotes matched by the original checker | 89% | 96% |
| Seconds | 258 | 335 |
| Recorded input tokens divided by all attempts, original aggregation | 261k | 348k |

Across the 18 tasks-and-repetitions both arms answered, Lens returned more useful papers in all 18 and more recent useful papers in all 18.
Verified citation links were higher in 10, lower in 7 and equal in 1: with Semantic Scholar unavailable, Lens had no reference lists for many recent preprints, so some answers reported few links.
Lens timed out on one table-structure attempt; both arms timed out on the same weather attempt.

The original grader satisfied the stated decision rule.
That is a preliminary result, not a verified release claim: the audit below found false-positive checks.
The time comparison remains measured; token usage is missing for all three timed-out attempts.

Claude Code login and a real Opus 5.5 request succeeded, and the paired comparison is complete below.

### Verification audit

Two independent reviews reproduced the saved arithmetic and found these defects:

- A valid title could receive URL credit even when its destination was never checked.
- Quote normalization removed scientific operators and signs, so altered quotations could pass.
- A citation title prefix could match a different, longer paper title in concatenated bibliography text.
- The report claimed 80 anchors rather than the 75 in the frozen task file.
- Token aggregation treated missing timeout usage as zero.
- Historical runs did not archive the runner, grader, raw judge votes or concurrency setting.
- A later review found product/tool names in narrative fields sent to the pairwise judge.
  Answer labels alone did not hide these identifiers; both hosts require the revision-3 pairwise correction.

The responsible checks are repaired and covered by regression tests.
Original runs and grades remain intact; corrected grading uses a new output directory and the unchanged tasks.
Historical judge votes cannot be reconstructed, and an offline correction may lack evidence for URLs whose pages were never cached.
The measured package was `850d687aa043`; production fixes after that snapshot need their own runs.

### Corrected Codex grading

The unchanged held-out answers were checked again with the repaired verifier and fresh pooled relevance labels.
An additional facts-only pass with the final verifier produced identical scores, papers, anchors and edge evidence.
Revision 3 retained those relevance labels and reran all 36 pairwise judgments with explicit tool identifiers masked.
The new grades preserve identical paper, citation, anchor and per-attempt scores; all earlier grades and judge archives remain intact.
The [public results](results/codex-heldout.json) include the answers, paper-level labels, citation proofs and failure denominators.

| Mean per attempt | Native Codex | Codex + Lens |
| --- | ---: | ---: |
| Useful papers, including adjacent work | 14.0 | 21.0 |
| Directly relevant papers | 13.05 | 19.15 |
| Recent useful papers | 5.60 | 9.05 |
| Verified links between useful papers | 2.70 | 8.45 |
| Precision, answered attempts | 0.884 | 0.948 |
| Anchor recall | 0.707 | 0.732 |
| Completed attempts | 19 / 20 | 18 / 20 |
| Seconds | 258 | 335 |
| Input tokens, attempts with reported usage | 275k | 386k |
| Attempts missing token usage | 1 | 2 |

The revision-3 overall judge records **17 Lens wins, 1 web win and 2 ties**.
Of 238 verified claimed edges, 155 use index reference records, 62 use identifiers in primary bibliography items, and 21 use complete title/author/year matches in primary items.
These are distinct proof sources, not a claim that every link was checked in full text.
The offline pass leaves 29 web and 19 Lens entries unverified; missing evidence is not proof that a URL is wrong.

Lens meets the stated quality decision rule for these saved Codex attempts.
It is slower, has one fewer completed answer, and its observed input-token mean is higher.
This supports a bounded Codex quality advantage on this dataset, with higher time and observed token use.

### Claude Code results

The same ten tasks ran twice per arm using `claude-opus-5-5` at high effort and frozen package `f48d89af2e38`.
The GPT judge used medium effort; revision-3 masking and order swapping apply to both arms.
All 20 relevance and 38 pairwise requests and responses are saved locally.
The [public evidence](results/claude-heldout.json) includes every source answer, paper label, citation proof and swap vote.

| Mean per attempt | Native Claude | Claude + Lens |
| --- | ---: | ---: |
| Useful papers, including adjacent work | 21.05 | 23.35 |
| Directly relevant papers | 17.65 | 20.35 |
| Recent useful papers | 7.70 | 8.10 |
| Verified links between useful papers | 4.20 | 4.60 |
| Precision, answered attempts | 0.909 | 0.944 |
| Anchor recall | 0.848 | 0.778 |
| Quotes matched verbatim | 73.8% | 94.7% |
| Completed attempts | 19 / 20 | 20 / 20 |
| Seconds | 131 | 126 |
| Input tokens, including cached | 47k | 109k |

The overall judge records **7 Lens wins, 10 web wins and 3 ties**.
Lens fails the declared quality rule on pairwise judgment despite passing its other conditions.
Native Claude also recovers more reference anchors and prominent papers; Lens uses about 2.31 times the processed input tokens.
Cached tokens are included, so this is not a measured billing comparison.
The native battery-cycle-life repetition exceeded eight calls; its original answer remains saved but receives zero quality credit and forfeits.
No Claude token usage is missing.

Of 198 verified claimed edges, 25 use index references, 116 use primary bibliography identifiers and 57 use primary title/author/year evidence.
The verification cache reuses earlier paper evidence equally for both arms; inference caches remain cold and separate.
These inspected tasks now serve as regression cases for future product changes, rather than independent held-out confirmation.

```bash
python evals/grade.py ../output/evals-v3/heldout-codex --out ../output/evals-v3/heldout-codex-correction --offline
python evals/run.py --agent claude --split held_out --reps 2 --out ../output/evals-v3/heldout-claude
python evals/grade.py ../output/evals-v3/heldout-claude --out ../output/evals-v3/heldout-claude-correction
python scripts/render_results.py
```

The renderer creates `../output/citation-lens-results.html` from the exported results, with Codex and Claude tabs.
It calculates every metric from the saved data and labels regressions explicitly.
Until a host's graded export exists, its tab shows a pending message without estimated metrics.

## Later development and fresh confirmation

Three later development candidates were rejected by the rule saved before grading.
All four versions ran the original four development tasks twice in both arms using the same frozen inference runner, common revision-4 grader, 420-second deadline, eight-call budget, 25-paper ceiling, cold caches and two concurrent attempts.
All 64 attempts remain preserved, including six exceeded-budget failures across the candidate versions.
The final candidate completed seven of eight Lens attempts, used 235,463 versus 100,729 mean processed input tokens for the baseline, and tied its native control four to four.
It also returned fewer verified useful citation links than native research.
[Development results](DEVELOPMENT.md) include all candidates, failures, selection rules, source packages and public answer-level evidence.

Version 0.2.1 retains independently verified correctness repairs and returns to the earlier few-call plan with general wording.
Its final guidance differs from the measured candidates and has not had a paired end-to-end evaluation.
Historical Codex and Claude scores remain attached to their original packages.
Smaller cached reads or recovered bibliography links do not establish an overall quality, latency or token advantage.

The audited ten topics are now regression cases.
A separate private version-4 draft contains ten new topics and 42 reference anchors selected before candidate answers, including surveys, lineages, frontier work and cross-domain tasks.
Separate model-based agents proposed and reviewed it against primary sources, with knowledge of the earlier failure analysis.
It is not an externally sampled benchmark or a guarantee across research domains.
Anchors measure reference-anchor recall, not an exhaustive reading list.
The draft has not been exposed to inference or published, and no fresh confirmation is run on a rejected performance candidate.
A future selected candidate must be frozen before confirmation, with no topic replacement, grader tuning or paper-specific changes based on held-out outcomes.

Grading revision 4 applies equally to all four development versions.
It recognizes versioned bioRxiv and medRxiv URLs as their canonical DOIs while retaining independent destination verification, and bounds recency by the run date as well as its lower cutoff.
Title checks recognize Unicode and simple unsigned math-subscript renderings; unrelated title suffixes and verbatim quotes retain their identity requirements.
A year-only date earns recent credit only when the entire year lies inside the window; overlapping boundary years are flagged as unverified and receive no recent credit.
Dates are resolved index metadata, not proof of the earliest appearance across every preprint and journal version.
The historical revision-3 exports remain unchanged; the two boundary-year entries found there already lack valid URL credit and do not affect scored recent-paper counts.
The judge criteria, blinding and quality rule are unchanged.
The later runner validation rejects unsafe or colliding task names before writing output and does not change inference prompts.

## Limits

- **Model-based judgment.** The Codex comparison uses a GPT judge from the agents' own model family.
  The Claude comparison uses that GPT judge for both Opus arms.
  Order swapping controls position bias; labels are not human-calibrated and other judge biases can remain.
- **Sampling.** One or two runs per task measure an average, not a guarantee.
- **Throttling.** Keyless providers can throttle mid-run; affected attempts are kept and reported, not dropped.
- **Incomplete anchors.** Anchors are a small must-have set; other relevant papers earn credit only through the relevance judge.

[judge]: https://arxiv.org/abs/2306.05685
