# Results

The goal is more relevant papers and trustworthy citations than the host's default web research, under a matched budget.
Quality, completion rate, time and tokens are measured separately.

Each host was tested on 10 held-out tasks, twice each, under matched conditions.
After verification repairs and masking explicit tool names, the order-swapped judge favored Lens in **17 Codex pairs**, with one loss and two ties.
For **Claude Code, Lens won seven, lost ten and tied three**.
It does not meet our Claude quality goal yet: higher paper counts came with lower must-find recall and more input tokens.

| Mean per attempt | Codex | Codex + Lens | Claude | Claude + Lens |
| --- | ---: | ---: | ---: | ---: |
| Directly relevant papers | 13.1 | 19.2 | 17.7 | 20.4 |
| Recent useful papers | 5.6 | 9.1 | 7.7 | 8.1 |
| Verified links between useful papers | 2.7 | 8.5 | 4.2 | 4.6 |
| Must-find recall | 0.707 | 0.732 | 0.848 | 0.778 |
| Completed attempts | 19 / 20 | 18 / 20 | 19 / 20 | 20 / 20 |
| Seconds | 258 | 335 | 131 | 126 |
| Reported input tokens, including cached | 275k | 386k | 47k | 109k |

Failed attempts receive zero quality credit.
Three Codex timeouts lack token usage; token means exclude them.
These are historical keyless measurements with model-based judges, from the packages recorded in each export.
Inspected tasks become regression cases for future changes; new behavior needs fresh held-out evidence.
Three later development candidates failed the saved quality and efficiency requirements.
Version 0.2.2 ships separately verified correctness repairs, including exact DOI recovery; it has no demonstrated end-to-end performance gain.

[Protocol, failures and reproduction](EVALUATION.md) · [Codex evidence](results/codex-heldout.json) · [Claude evidence](results/claude-heldout.json)
[Development comparisons](DEVELOPMENT.md)
[DOI recovery checks](DOI_RECOVERY.md)
