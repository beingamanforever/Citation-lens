# Development comparisons

Three bounded Claude development candidates failed the saved retention rule.
None establishes a quality, latency or token improvement over the published baseline.
The fresh confirmation corpus remains private and unused.

## Matched setup

Each version ran the same four development tasks twice in both arms: 16 attempts, Claude Opus 5.5 at high effort, 420 seconds, eight research calls and a 25-paper ceiling.
Both arms retained native web research; only the Lens arm received the plugin and its playbook.
Inference caches were cold, with two concurrent attempts.
The frozen inference runner and common revision-4 grader were identical across versions.
Relevance was pooled and blinded within each version; pairwise judgments ran twice with answer order swapped.
Original answers, usage and exceeded budgets are preserved; failed attempts receive zero quality credit.

The saved retention rule required lower mean processed input without lower useful, core or recent papers, anchor recall, useful citation links or quote verification.
Precision had a 0.05 allowance, and completion could not fall below the baseline's eight answers.
The separate native-quality rule is unchanged from the [evaluation protocol](EVALUATION.md#grading).

## Results

| Mean per Lens attempt, failures included | Baseline | Candidate 1 | Candidate 2 | Candidate 3 |
| --- | ---: | ---: | ---: | ---: |
| Completed | 8 / 8 | 6 / 8 | 7 / 8 | 7 / 8 |
| Useful papers | 24.5 | 18.375 | 21.5 | 21.0 |
| Directly relevant papers | 23.75 | 17.75 | 21.0 | 20.375 |
| Recent useful papers | 6.125 | 5.125 | 6.0 | 5.5 |
| Reference-anchor recall | 1.0 | 0.725 | 0.8 | 0.825 |
| Verified useful citation links | 11.125 | 9.375 | 12.5 | 11.625 |
| Precision | 0.980 | 0.740 | 0.860 | 0.840 |
| Quotes verified verbatim | 0.911 | 0.971 | 0.957 | 0.954 |
| Seconds | 198.637 | 236.938 | 212.825 | 206.8 |
| Processed input tokens, cached included | 100,728.5 | 163,559.5 | 157,763.375 | 235,462.625 |

The baseline had three Lens wins, three native wins and two ties; it also failed the native-quality rule on these development tasks.
Candidate 1 had five wins and three losses but fewer useful papers and verified useful links than its native control.
Candidate 2 tied three to three with two ties and also fell below its native control on useful papers and links.
Candidate 3 tied four to four; Lens returned 21 versus 19.25 useful papers, but 11.625 versus 13.25 useful citation links and 0.5 versus 0.125 unfound papers.
All three candidates fail both the retention rule and their within-version native-quality rule.

### Preserved failures

| Version | Attempt | Calls / budget | Original papers | Treatment |
| --- | --- | ---: | ---: | --- |
| Candidate 1 | Lens gene editing, repetition 1 | 11 / 8 | 25 | zero quality credit |
| Candidate 1 | Lens gene editing, repetition 2 | 9 / 8 | 25 | zero quality credit |
| Candidate 1 | Native document geometry, repetition 2 | 9 / 8 | 25 | zero quality credit |
| Candidate 2 | Lens gene editing, repetition 1 | 9 / 8 | 25 | zero quality credit |
| Candidate 3 | Lens gene editing, repetition 2 | 9 / 8 | 25 | zero quality credit |
| Candidate 3 | Native document geometry, repetition 2 | 9 / 8 | 19 | zero quality credit |

Candidate 3's failed Lens answer still records 328,461 input tokens; its failed native answer records 65,842.
Its seven answered Lens attempts average 24 useful papers and 13.286 useful links, but these answered-only means do not replace the eight-attempt denominator.
Every attempt has a usage receipt; processed input includes cached tokens and is not billed cost.

## What was tested

- **Candidate 1:** cancellation and throttling repairs, complete query terms, and coverage-driven guidance.
- **Candidate 2:** direct native-identifier reads and primary arXiv backward-reference recovery when indexes fail.
- **Candidate 3:** reuse of complete cached abstracts and fused graph records, normalized publisher DOI aliases, version-safe bibliography provenance, and guidance to reuse snippets and saved graphs.

Independent offline audits reproduced attempt receipts, score arithmetic and blinded votes.
Source snapshots, original logs and judge requests remain in the local `output/evals-v4/` archives.
The public exports below retain every source answer, score, paper label, citation proof and pairwise vote.

## Limits and release decision

All versions reported an exhausted OpenAlex keyless search budget.
Semantic Scholar preflight succeeded on 1/3 baseline probes, 0/3 for candidate 1, 1/3 for candidate 2 and 2/3 for candidate 3; all arXiv probes succeeded.
Sequential provider conditions and separately pooled relevance labels limit causal attribution to any single code change.
Grading candidates 2 and 3 starts from the same archived 3,341-row verification snapshot; baseline starting-cache equality is execution-observed rather than recorded in an initial receipt.
Verification caches never enter inference.
An offline replay from candidate 3's final cache changes one paper's citation count from 91 to 106 and its venue wording, increasing its mean prominent-paper count from 2.875 to 3.125.
Identity, quotes, dates, edges, anchors, label mappings and every acceptance predicate remain unchanged; this is not an exact full-fact replay.

The published baseline playbook contained FlashAttention paper examples while exact attention was a development task.
That may advantage its development anchor recall; later candidates removed those identifiers.
This disclosure does not override the saved rejection rule, and the shipped playbook contains no benchmark paper examples.

Version 0.2.1 retains separately demonstrated correctness and capability repairs: cancellation stops abandoned downloads, complete query terms survive, sufficient cached evidence reads locally, DOI aliases persist, and recovered bibliography edges carry checkable provenance.
It restores the earlier few-call plan with general wording and evidence constraints.
This final guidance differs from every measured snapshot and has not had a paired agent evaluation.
The release makes no new end-to-end performance claim.
No fresh held-out run is started on a rejected performance candidate, and no favorable repetition replaces a failure.
The Claude quality goal remains unmet.

A subsequent [native-discovery workflow study](NATIVE_DISCOVERY_EXPERIMENT.md) was preregistered separately after v0.2.2.
All 16 Claude attempts failed before research because the OAuth session had expired; Codex was left unrun and the prototype was not shipped.
Its failure receipts are retained, and it does not reopen the three rejected performance candidates or establish a quality improvement.

## Evidence and reproduction

[Baseline](results/development/baseline.json) · [Candidate 1](results/development/candidate-1.json) · [Candidate 2](results/development/candidate-2.json) · [Candidate 3](results/development/candidate-3.json)

Each export records its frozen package identifier and conditions.
Raw host logs and frozen source directories remain local rather than in the release; the public exports cannot reproduce model or provider nondeterminism.
To collect a new comparable development run on the current source:

```bash
python evals/run.py --agent claude --split development --reps 2 --jobs 2 --out ../output/evals/new-development
python evals/grade.py ../output/evals/new-development --out ../output/evals/new-development-graded
python scripts/export_results.py ../output/evals/new-development --grades ../output/evals/new-development-graded/grades.json --out ../output/evals/new-development.json
```

Use a new output directory and save the decision rule before running.
Do not replace the historical evidence or treat inspected tasks as fresh confirmation.
