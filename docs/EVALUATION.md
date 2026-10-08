# Evaluation

Question: does adding Citation Lens make Codex find more relevant, verified papers - including the foundations, the most influential follow-ups and the newest work - and more real citation links than Codex's own web search, under the same budget?

## Protocol

**Arms.** Both use the same Codex model and reasoning effort, prompt, output schema, deadline and call budget, and both keep native live web search.
The lens arm adds the five Lens tools and their playbook (`skills/research/SKILL.md`).
Each attempt starts from a fresh Codex home.

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

Metrics per attempt: useful papers (found, right URL, relevance >= 1), core papers (relevance 2), precision, recent useful, prominent useful (>= 100 citations), anchor recall, approach coverage, verified citations between useful papers, quote verbatim rate, not-found and wrong-URL counts, seconds, tool calls, tokens, and bytes returned by Lens.

**Lens beats native Codex** on a split when it wins more pairwise comparisons than it loses, finds more useful papers, more recent useful papers and more verified useful citations on average, without lower precision (by more than 0.05) or more unfound papers.

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

Claude Code login and a real Opus 5.5 request now succeed.
The paired Claude comparison uses the same held-out tasks and within-host matched conditions; results will be reported after both arms and grading complete.

### Verification audit

Two independent reviews reproduced the saved arithmetic and found these defects:

- A valid title could receive URL credit even when its destination was never checked.
- Quote normalization removed scientific operators and signs, so altered quotations could pass.
- A citation title prefix could match a different, longer paper title in concatenated bibliography text.
- The report claimed 80 anchors rather than the 75 in the frozen task file.
- Token aggregation treated missing timeout usage as zero.
- Historical runs did not archive the runner, grader, raw judge votes or concurrency setting.

The responsible checks are repaired and covered by regression tests.
Original runs and grades remain intact; corrected grading uses a new output directory and the unchanged tasks.
Historical judge votes cannot be reconstructed, and an offline correction may lack evidence for URLs whose pages were never cached.
The measured package was `850d687aa043`; production fixes after that snapshot need their own runs.

### Corrected Codex grading

The unchanged held-out answers were checked again with the repaired verifier and freshly blinded relevance and order-swapped judges.
An additional facts-only pass with the final verifier produced identical scores, papers, anchors and edge evidence.
All 56 new judge requests and responses are saved locally; the 36 pairwise responses reproduce every retained swap vote.
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

The fresh overall judge records **17 Lens wins, 1 web win and 2 ties**.
Of 238 verified claimed edges, 155 use index reference records, 62 use identifiers in primary bibliography items, and 21 use complete title/author/year matches in primary items.
These are distinct proof sources, not a claim that every link was checked in full text.
The offline pass leaves 29 web and 19 Lens entries unverified; missing evidence is not proof that a URL is wrong.

Lens meets the stated quality decision rule for these saved Codex attempts.
It is slower, has one fewer completed answer, and its observed input-token mean is higher.
This supports a bounded quality advantage on this dataset, not a general speed advantage or a measured Claude advantage.

```bash
python evals/grade.py ../output/evals-v3/heldout-codex --out ../output/evals-v3/heldout-codex-audit-full --offline
python evals/run.py --agent claude --split held_out --reps 2 --out ../output/evals-v3/heldout-claude
python evals/grade.py ../output/evals-v3/heldout-claude
```

## Limits

- **Same model family.** The judge is the same model as both agents.
  Self-preference therefore affects both arms alike, but labels are not human-calibrated.
- **Sampling.** One or two runs per task measure an average, not a guarantee.
- **Throttling.** Keyless providers can throttle mid-run; affected attempts are kept and reported, not dropped.
- **Incomplete anchors.** Anchors are a small must-have set; other relevant papers earn credit only through the relevance judge.

[judge]: https://arxiv.org/abs/2306.05685
