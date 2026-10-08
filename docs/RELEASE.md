# Citation Lens 0.2.1

Correctness and resilience improvements for Codex and Claude Code:

- Stop downloads and retries when their last caller cancels; pause throttled providers before another request.
- Preserve complete search terms, publisher DOI aliases and full cached paper evidence.
- Read sufficient cached abstracts locally while still resolving missing evidence.
- Recover some backward references from primary arXiv bibliographies when indexes fail, with explicit identifiers, source fragments and visible gaps.
- Keep requested-version bibliography evidence separate from canonical paper metadata.

The five tool names and signatures remain unchanged.
The shared playbook keeps the few-call research plan and removes paper-specific examples.
Restart the host after updating.

Three later development candidates failed the saved quality and efficiency requirements; all answers, failures and scores are retained in the [development evidence](https://github.com/beingamanforever/Citation-lens/blob/main/docs/DEVELOPMENT.md).
These repairs have passing caller-level checks, but this release has no demonstrated end-to-end speed, token or research-quality gain.
Historical audited results remain 17 Codex Lens wins, one loss and two ties; Claude records seven wins, ten losses and three ties and does not meet the quality goal.
Those results belong to the packages recorded in their exports, not this release.

[Install and use](https://github.com/beingamanforever/Citation-lens#install) · [Protocol and results](https://github.com/beingamanforever/Citation-lens/blob/main/docs/EVALUATION.md)
