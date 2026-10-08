# Citation Lens 0.2.2

Correctness and resilience improvements for Codex and Claude Code:

- Resolve missing DOI metadata through Crossref, including DOI-only entries in primary arXiv bibliographies.
- Keep each bibliography fragment as the citation proof; Crossref supplies metadata rather than citation claims.
- Preserve existing paper identities and evidence, incomplete dates and unavailable abstracts.
- Retain completed metadata batches when a later lookup times out, and serialize and pace Crossref requests across cancellation.
- Preserve structured abstract headings and breaks; leave notation that cannot be rendered faithfully unavailable.

The five tool names and signatures remain unchanged.
Default keyword search providers and the shared research playbook remain unchanged.
Restart the host after updating.

Three later development candidates failed the saved quality and efficiency requirements; all answers, failures and scores are retained in the [development evidence](https://github.com/beingamanforever/Citation-lens/blob/main/docs/DEVELOPMENT.md).
These repairs have 184 passing tests, including real MCP deadline and provenance checks.
A [fixed public capability sample](https://github.com/beingamanforever/Citation-lens/blob/main/docs/DOI_RECOVERY.md) retains missing abstracts and distinguishes indexed reads from Crossref lookup.
This release has no demonstrated end-to-end speed, token or research-quality gain.
Historical audited results remain 17 Codex Lens wins, one loss and two ties; Claude records seven wins, ten losses and three ties and does not meet the quality goal.
Those results belong to the packages recorded in their exports, not this release.

[Install and use](https://github.com/beingamanforever/Citation-lens#install) · [Protocol and results](https://github.com/beingamanforever/Citation-lens/blob/main/docs/EVALUATION.md)
