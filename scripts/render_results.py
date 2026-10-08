#!/usr/bin/env python3
"""Render the audited Citation Lens evaluation report from saved results."""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS = ROOT / "evals/tasks.json"
DEFAULT_TEMPLATE = ROOT / "docs/results/report-template.html"
DEFAULT_OUTPUT = ROOT.parent / "output/citation-lens-results.html"
GITHUB_ROOT = "https://github.com/beingamanforever/Citation-lens"
HOSTS = (
    ("codex", "Codex", ROOT / "docs/results/codex-heldout.json"),
    ("claude", "Claude Code", ROOT / "docs/results/claude-heldout.json"),
)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def compare_answered(scores: list[dict], metric: str) -> tuple[int, int, int]:
    by_attempt = {(score["task"], score["rep"], score["mode"]): score for score in scores}
    lens_higher = web_higher = tied = 0
    pairs = {(task, rep) for task, rep, _ in by_attempt}
    for task, rep in pairs:
        web = by_attempt.get((task, rep, "web"))
        lens = by_attempt.get((task, rep, "lens"))
        if not web or not lens or web["status"] != "ok" or lens["status"] != "ok":
            continue
        if lens[metric] > web[metric]:
            lens_higher += 1
        elif web[metric] > lens[metric]:
            web_higher += 1
        else:
            tied += 1
    return lens_higher, web_higher, tied


def percent_delta(new: float, old: float) -> int | None:
    if old == 0:
        return 0 if new == 0 else None
    return round((new / old - 1) * 100)


def change_text(new: float, old: float) -> str:
    delta = percent_delta(new, old)
    if delta is None:
        return f"{new:g} vs 0"
    return f"{delta:+d}%" if delta else "no change"


def relative_phrase(new: float, old: float, measure: str) -> str:
    delta = percent_delta(new, old)
    if delta is None:
        return f"{new:g} {measure} versus none"
    if delta == 0:
        return f"the same {measure}"
    direction = "more" if delta > 0 else "fewer"
    return f"{abs(delta)}% {direction} {measure}"


def result_class(new: float, old: float, higher_is_better: bool) -> str:
    if new == old:
        return "neutral"
    improved = new > old if higher_is_better else new < old
    return "win" if improved else "cost"


def count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def format_statuses(attempts: list[dict], mode: str) -> str:
    selected = [attempt for attempt in attempts if attempt["mode"] == mode]
    failures = Counter(attempt["status"] for attempt in selected if attempt["status"] != "ok")
    answered = len(selected) - sum(failures.values())
    if not failures:
        return f"{answered}/{len(selected)} answered; no failed attempts"
    details = ", ".join(
        count_label(count, status.replace("_", " ")) for status, count in sorted(failures.items())
    )
    return f"{answered}/{len(selected)} answered; {details}"


def model_family(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("gpt"):
        return "GPT"
    if lowered.startswith("claude"):
        return "Claude"
    return lowered.split("-", 1)[0]


def build_report(host_id: str, label: str, path: Path, results: dict, task_set: dict) -> dict:
    grading = results["grading"]
    if grading.get("grading_revision") not in (2, 3) or grading.get("pairwise_rerun") is not True:
        raise ValueError(f"{path} requires revision-2/3 grading with pairwise_rerun=true")
    if not isinstance(grading.get("offline"), bool):
        raise ValueError(f"{path} must record whether grading was offline")

    protocol = results["protocol"]
    agent = str(protocol["agent"]).lower()
    if not agent.startswith(host_id):
        raise ValueError(f"{path} records agent {protocol['agent']!r}, expected {host_id!r}")

    summary = grading["summary"]
    arms = summary["arms"]
    web, lens = arms["web"], arms["lens"]
    web_answered, lens_answered = arms["web_answered"], arms["lens_answered"]
    pairwise = summary["pairwise"]["overall"]
    pair_count = sum(pairwise.values())
    if pair_count != len(grading["pairs"]):
        raise ValueError(f"{path} pairwise summary does not match its judgments")
    judged_outcomes = Counter(pair["winner"]["overall"] for pair in grading["pairs"])
    if any(judged_outcomes[outcome] != pairwise[outcome] for outcome in ("lens", "web", "tie")):
        raise ValueError(f"{path} pairwise totals do not match its saved outcomes")
    if web["attempts"] != lens["attempts"] or web["attempts"] != pair_count:
        raise ValueError(f"{path} attempt totals do not match pairwise judgments")

    attempts = results["attempts"]
    repetitions = sorted({attempt["rep"] for attempt in attempts})
    heldout_tasks = {attempt["task"] for attempt in attempts}
    expected_attempts = {
        (task, repetition, mode)
        for task in heldout_tasks
        for repetition in repetitions
        for mode in ("web", "lens")
    }
    actual_attempts = {(attempt["task"], attempt["rep"], attempt["mode"]) for attempt in attempts}
    if actual_attempts != expected_attempts or len(attempts) != len(actual_attempts):
        raise ValueError(f"{path} does not contain a complete task/rep/mode grid")

    canonical_heldout = {task["name"] for task in task_set["tasks"] if task["split"] == "held_out"}
    if canonical_heldout != heldout_tasks:
        raise ValueError(f"{path} tasks do not match the canonical held-out set")

    packages = set(results["packages"])
    packages.update(attempt["package"] for attempt in attempts)
    if len(packages) != 1:
        raise ValueError(f"{path} combines attempts from different package snapshots")

    ok_by_mode = Counter(attempt["mode"] for attempt in attempts if attempt["status"] == "ok")
    if ok_by_mode["web"] != web["answered"] or ok_by_mode["lens"] != lens["answered"]:
        raise ValueError(f"{path} answered totals do not match attempt statuses")

    evidence_sources = Counter(
        (edge.get("evidence") or {}).get("source")
        for edges in grading["edges"].values()
        for edge in edges
        if edge.get("verified")
    )
    primary_identifier = evidence_sources.pop("primary_identifier", 0)
    primary_title = evidence_sources.pop("primary_title_authors_year", 0)
    index_reference = evidence_sources.pop("index_reference", 0)
    other_proof = sum(evidence_sources.values())
    verified_proof = primary_identifier + primary_title + index_reference + other_proof

    useful_comparison = compare_answered(grading["scores"], "useful")
    recent_comparison = compare_answered(grading["scores"], "recent_useful")
    citation_comparison = compare_answered(grading["scores"], "citations_useful")
    both_answered = sum(useful_comparison)

    quote_checks = Counter()
    for score in grading["scores"]:
        quote_checks[score["mode"]] += score["quotes_checked"]

    judge = grading["judge"]
    return {
        "id": host_id,
        "label": label,
        "path": path,
        "source_name": path.name,
        "source_url": f"{GITHUB_ROOT}/blob/main/docs/results/{path.name}",
        "protocol": protocol,
        "grading": grading,
        "web": web,
        "lens": lens,
        "web_answered": web_answered,
        "lens_answered": lens_answered,
        "pairwise": pairwise,
        "attempts": attempts,
        "repetitions": len(repetitions),
        "heldout_tasks": len(heldout_tasks),
        "package": packages.pop(),
        "verified_proof": verified_proof,
        "primary_identifier": primary_identifier,
        "primary_title": primary_title,
        "index_reference": index_reference,
        "other_proof": other_proof,
        "useful_comparison": useful_comparison,
        "recent_comparison": recent_comparison,
        "citation_comparison": citation_comparison,
        "both_answered": both_answered,
        "quote_checks": quote_checks,
        "judge": judge,
        "same_judge_family": model_family(str(protocol["model"]))
        == model_family(str(judge["model"])),
    }


def chip(label: str, new: float, old: float, *, higher_is_better: bool) -> str:
    css_class = result_class(new, old, higher_is_better)
    return (
        f'<span class="chip {css_class}">{html.escape(label)} '
        f"<b>{html.escape(change_text(new, old))}</b></span>"
    )


def outcome(new: float, old: float, *, higher_is_better: bool) -> tuple[str, str]:
    css_class = result_class(new, old, higher_is_better)
    if css_class == "neutral":
        return "", "No change"
    return ("better", "Improved") if css_class == "win" else ("worse", "Regressed")


def table_row(
    label: str,
    web_display: str,
    lens_display: str,
    web_value: float,
    lens_value: float,
    *,
    higher_is_better: bool,
    comparison: str | None = None,
) -> str:
    if comparison is None:
        row_class, comparison = outcome(lens_value, web_value, higher_is_better=higher_is_better)
    else:
        row_class = ""
    lens_class = "n lensv" if row_class else "n"
    return (
        f'<tr class="{row_class}"><td>{html.escape(label)}</td>'
        f'<td class="n">{html.escape(web_display)}</td>'
        f'<td class="{lens_class}">{html.escape(lens_display)}</td>'
        f'<td class="outcome {row_class}">{html.escape(comparison)}</td></tr>'
    )


def render_table(report: dict) -> str:
    web, lens = report["web"], report["lens"]
    pairwise = report["pairwise"]
    web_tokens = (
        "not reported"
        if web["token_usage_missing"] == web["attempts"]
        else f"{web['input_tokens']:,.0f}"
    )
    lens_tokens = (
        "not reported"
        if lens["token_usage_missing"] == lens["attempts"]
        else f"{lens['input_tokens']:,.0f}"
    )
    token_comparison = (
        None
        if web["token_usage_missing"] == 0 and lens["token_usage_missing"] == 0
        else "Incomplete"
    )
    web_quotes = (
        f"{web['quote_verbatim_rate'] * 100:.1f}%"
        if report["quote_checks"]["web"]
        else "not available"
    )
    lens_quotes = (
        f"{lens['quote_verbatim_rate'] * 100:.1f}%"
        if report["quote_checks"]["lens"]
        else "not available"
    )
    quote_comparison = (
        None if all(report["quote_checks"][mode] for mode in ("web", "lens")) else "Not comparable"
    )
    precision_comparison = None if web["answered"] and lens["answered"] else "Not comparable"
    web_precision = (
        f"{report['web_answered']['precision']:.3f}" if web["answered"] else "not available"
    )
    lens_precision = (
        f"{report['lens_answered']['precision']:.3f}" if lens["answered"] else "not available"
    )

    def metric_row(label: str, key: str, digits: int, higher_is_better: bool) -> str:
        return table_row(
            label,
            f"{web[key]:.{digits}f}",
            f"{lens[key]:.{digits}f}",
            web[key],
            lens[key],
            higher_is_better=higher_is_better,
        )

    rows = [
        table_row(
            "Pairwise wins (ties reported below)",
            str(pairwise["web"]),
            str(pairwise["lens"]),
            pairwise["web"],
            pairwise["lens"],
            higher_is_better=True,
        ),
        metric_row("Useful papers", "useful", 1, True),
        metric_row("Core useful papers", "core", 2, True),
        metric_row("Recent useful papers", "recent_useful", 2, True),
        metric_row("Prominent useful papers", "prominent_useful", 1, True),
        metric_row("Verified links between useful papers", "citations_useful", 2, True),
        table_row(
            "Precision, answered attempts",
            web_precision,
            lens_precision,
            report["web_answered"]["precision"],
            report["lens_answered"]["precision"],
            higher_is_better=True,
            comparison=precision_comparison,
        ),
        metric_row("Must-find anchor recall", "anchor_recall", 3, True),
        metric_row("Wrong URLs", "wrong_url", 2, False),
        metric_row("Unverified URLs", "url_unverified", 2, False),
        table_row(
            "Quotes matched verbatim",
            web_quotes,
            lens_quotes,
            web["quote_verbatim_rate"],
            lens["quote_verbatim_rate"],
            higher_is_better=True,
            comparison=quote_comparison,
        ),
        table_row(
            f"Answered within {report['protocol']['deadline_seconds']} s",
            f"{web['answered']} / {web['attempts']}",
            f"{lens['answered']} / {lens['attempts']}",
            web["answered"],
            lens["answered"],
            higher_is_better=True,
        ),
        metric_row("Seconds", "seconds", 1, False),
        table_row(
            "Input tokens, including cached",
            web_tokens,
            lens_tokens,
            web["input_tokens"],
            lens["input_tokens"],
            higher_is_better=False,
            comparison=token_comparison,
        ),
        table_row(
            "Attempts missing token usage",
            str(web["token_usage_missing"]),
            str(lens["token_usage_missing"]),
            web["token_usage_missing"],
            lens["token_usage_missing"],
            higher_is_better=False,
        ),
    ]
    return "\n".join(rows)


def render_host_panel(report: dict, *, active: bool) -> str:
    web, lens = report["web"], report["lens"]
    pairwise = report["pairwise"]
    label = html.escape(report["label"])
    hidden = "" if active else " hidden"
    pair_class = result_class(pairwise["lens"], pairwise["web"], True)
    pair_chip = (
        f'<span class="chip {pair_class}">Pairwise '
        f"<b>{pairwise['lens']}-{pairwise['web']}</b> "
        f"({count_label(pairwise['tie'], 'tie')})</span>"
    )
    mean_summary = ", ".join(
        (
            relative_phrase(lens["useful"], web["useful"], "useful papers"),
            relative_phrase(lens["recent_useful"], web["recent_useful"], "recent useful papers"),
            relative_phrase(
                lens["citations_useful"], web["citations_useful"], "verified citation links"
            ),
            relative_phrase(lens["seconds"], web["seconds"], "elapsed time"),
        )
    )
    if pairwise["lens"] > pairwise["web"]:
        pair_summary = "Citation Lens led the pairwise result"
    elif pairwise["lens"] < pairwise["web"]:
        pair_summary = "Web search led the pairwise result"
    else:
        pair_summary = "The pairwise result was even"

    matched = report["both_answered"]
    if matched:
        useful = report["useful_comparison"]
        recent = report["recent_comparison"]
        citations = report["citation_comparison"]
        matched_note = (
            f"In {matched} matched runs where both arms answered, Lens returned more useful "
            f"papers in {useful[0]}, web returned more in {useful[1]}, and {useful[2]} tied. "
            f"For recent useful papers the counts were {recent[0]} Lens, {recent[1]} web, "
            f"and {recent[2]} ties; for verified useful citation links they were "
            f"{citations[0]} Lens, {citations[1]} web, and {citations[2]} ties."
        )
    else:
        matched_note = "No matched run had both arms answer, so per-run quality is unavailable."

    proof_parts = [
        f"{report['primary_identifier']} matched cited-paper identifiers",
        f"{report['primary_title']} matched complete title-author-year evidence",
        f"{report['index_reference']} used separately labelled index reference IDs",
    ]
    if report["other_proof"]:
        proof_parts.append(f"{report['other_proof']} used another recorded evidence source")
    proof_text = "; ".join(proof_parts)
    grading_mode = "offline" if report["grading"]["offline"] else "online"
    judge_relation = (
        "the same named model family as the agent"
        if report["same_judge_family"]
        else "a different named model family from the agent"
    )
    anonymization = report["grading"].get("pairwise_anonymization")
    if anonymization == "explicit_tool_names":
        masking_note = (
            "Explicit tool and product identifiers were masked in narrative reason, summary "
            "and gap fields before pairwise judging. Paper titles, scientific content and source "
            "answers were retained. Workflow and writing-style cues may still reveal an arm."
        )
    elif anonymization:
        masking_note = (
            f"The export records pairwise anonymization as {anonymization!r}; this report does "
            "not infer what it removed. Workflow and writing-style cues may still reveal an arm."
        )
    else:
        masking_note = (
            "This export records no explicit-identifier masking method. Pairwise judgments may "
            "reflect product names as well as residual workflow and writing-style cues."
        )
    source_url = html.escape(report["source_url"], quote=True)
    web_status = html.escape(format_statuses(report["attempts"], "web"))
    lens_status = html.escape(format_statuses(report["attempts"], "lens"))
    package = html.escape(report["package"])
    model = html.escape(str(report["protocol"]["model"]))
    effort = html.escape(str(report["protocol"]["effort"]))
    judge_model = html.escape(str(report["judge"]["model"]))
    judge_effort = html.escape(str(report["judge"]["effort"]))
    chips_html = "\n        ".join(
        (
            pair_chip,
            chip("Useful papers", lens["useful"], web["useful"], higher_is_better=True),
            chip(
                "Recent papers",
                lens["recent_useful"],
                web["recent_useful"],
                higher_is_better=True,
            ),
            chip(
                "Verified links",
                lens["citations_useful"],
                web["citations_useful"],
                higher_is_better=True,
            ),
            chip("Time", lens["seconds"], web["seconds"], higher_is_better=False),
        )
    )

    return f"""
  <section class="host-panel" id="panel-{report["id"]}" role="tabpanel"
           aria-labelledby="tab-{report["id"]}" tabindex="0"{hidden}>
    <div class="host-heading">
      <h2>{label}</h2>
      <p class="lede">
        <b>{pair_summary}: {count_label(pairwise["lens"], "Lens win")},
        {count_label(pairwise["web"], "web win")} and
        {count_label(pairwise["tie"], "tie")}.</b>
        Across all attempts, Lens averaged {html.escape(mean_summary)}.
        It answered {lens["answered"]} of {lens["attempts"]} attempts;
        web answered {web["answered"]} of {web["attempts"]}.
      </p>
      <div class="chips">
        {chips_html}
      </div>
    </div>

    <section>
      <h3 class="section-title">Mean per attempt</h3>
      <div class="panel">
        <div class="legend">
          <span style="--c: var(--codex)">{label} + web</span>
          <span style="--c: var(--lens)">{label} + Citation Lens</span>
        </div>
        <div class="multiples" id="multiples-{report["id"]}"></div>
      </div>
      <p class="note">
        Each chart has its own scale. Failed attempts count as zero papers.
        Web: {web_status}. Lens: {lens_status}.
      </p>
    </section>

    <section>
      <h3 class="section-title">Audited measures</h3>
      <div class="panel scroll">
        <table>
          <caption class="sr-only">
            {label} held-out results, mean per attempt unless stated otherwise
          </caption>
          <thead><tr>
            <th>Measure</th><th class="n">Web</th><th class="n">Lens</th>
            <th>Lens result</th>
          </tr></thead>
          <tbody>{render_table(report)}</tbody>
        </table>
      </div>
      <p class="note">
        Pairwise comparison was run twice with answer order swapped; the final label
        reconciles both votes. {html.escape(matched_note)}
      </p>
    </section>

    <section>
      <h3 class="section-title">Run record and limits</h3>
      <ul>
        <li><b>Frozen snapshot.</b> Package <code>{package}</code>;
        {report["heldout_tasks"]} held-out tasks, {report["repetitions"]} repetitions per arm,
        <code>{model}</code> at {effort} effort, {report["protocol"]["tool_budget"]} tool calls
        and a {report["protocol"]["deadline_seconds"]} s deadline. Saved Lens tool results
        averaged {lens["lens_bytes"] / 1000:.0f} KB per attempt.</li>
        <li><b>Failures.</b> Web: {web_status}. Lens: {lens_status}.</li>
        <li><b>Missing usage.</b>
        {count_label(web["token_usage_missing"], "web attempt")} and
        {count_label(lens["token_usage_missing"], "Lens attempt")} lack token usage.
        Token means exclude
        those attempts.</li>
        <li><b>Citation proof.</b> Across saved attempt-level edge checks,
        {report["verified_proof"]} links were verified: {html.escape(proof_text)}.
        These counts can repeat the same link across attempts.</li>
        <li><b>Pairwise masking.</b> {html.escape(masking_note)}</li>
        <li><b>Judge.</b> Revision {report["grading"]["grading_revision"]} used
        {grading_mode} grading and
        <code>{judge_model}</code> at {judge_effort} effort, {judge_relation}.
        Model-based labels are not human-calibrated.</li>
        <li><b>Evidence.</b> <a href="{source_url}">
        {html.escape(report["source_name"])}</a> contains the frozen protocol, attempts,
        grading, paper checks and judge reasons.</li>
      </ul>
    </section>
  </section>"""


def render_pending_panel(host_id: str, label: str, *, active: bool) -> str:
    hidden = "" if active else " hidden"
    return f"""
  <section class="host-panel" id="panel-{host_id}" role="tabpanel"
           aria-labelledby="tab-{host_id}" tabindex="0"{hidden}>
    <div class="host-heading">
      <h2>{html.escape(label)}</h2>
      <div class="panel pending">
        <b>Grading pending</b>
        <p>The audited export is not available yet. This tab shows no estimated metrics or
        placeholder zeros. Regenerating after
        <code>docs/results/{host_id}-heldout.json</code> exists will add the matched results.</p>
      </div>
    </div>
  </section>"""


def render(template: str, reports: list[dict], task_set: dict) -> str:
    tabs = []
    panels = []
    chart_data = {}
    source_links = []
    for index, report in enumerate(reports):
        active = index == 0
        status = "Complete" if report["complete"] else "Pending"
        tabs.append(
            f'<button type="button" class="tab" role="tab" id="tab-{report["id"]}" '
            f'aria-controls="panel-{report["id"]}" aria-selected="{str(active).lower()}" '
            f'tabindex="{0 if active else -1}">{html.escape(report["label"])} '
            f"<span>{status}</span></button>"
        )
        if report["complete"]:
            panels.append(render_host_panel(report, active=active))
            chart_data[report["id"]] = [
                {
                    "name": "Useful papers",
                    "note": "more is better",
                    "web": report["web"]["useful"],
                    "lens": report["lens"]["useful"],
                    "digits": 1,
                },
                {
                    "name": "Recent useful papers",
                    "note": "last two years",
                    "web": report["web"]["recent_useful"],
                    "lens": report["lens"]["recent_useful"],
                    "digits": 2,
                },
                {
                    "name": "Verified citation links",
                    "note": "between useful papers",
                    "web": report["web"]["citations_useful"],
                    "lens": report["lens"]["citations_useful"],
                    "digits": 2,
                },
                {
                    "name": "Seconds per attempt",
                    "note": "lower is better",
                    "web": report["web"]["seconds"],
                    "lens": report["lens"]["seconds"],
                    "digits": 1,
                },
            ]
            source_links.append(
                f'<li><a href="{html.escape(report["source_url"], quote=True)}">'
                f"{html.escape(report['label'])} result export</a></li>"
            )
        else:
            panels.append(render_pending_panel(report["id"], report["label"], active=active))

    tasks = task_set["tasks"]
    split_counts = Counter(task["split"] for task in tasks)
    anchor_count = sum(len(task["anchors"]) for task in tasks)
    recent_anchor_count = sum(
        anchor.get("role") == "recent" for task in tasks for anchor in task["anchors"]
    )
    values = {
        "HOST_TABS": "\n      ".join(tabs),
        "HOST_PANELS": "\n".join(panels),
        "REPORT_JSON": json.dumps(chart_data, ensure_ascii=False).replace("<", "\\u003c"),
        "RESULT_LINKS": "\n        ".join(source_links),
        "TOTAL_TASKS": str(len(tasks)),
        "DEVELOPMENT_TASKS": str(split_counts["development"]),
        "HELDOUT_TASKS": str(split_counts["held_out"]),
        "TOTAL_ANCHORS": str(anchor_count),
        "RECENT_ANCHORS": str(recent_anchor_count),
    }
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace(f"{{{{{name}}}}}", value)
    unresolved = sorted(set(re.findall(r"{{[A-Z0-9_]+}}", rendered)))
    if unresolved:
        raise ValueError(f"unresolved template values: {', '.join(unresolved)}")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-results", type=Path, default=HOSTS[0][2])
    parser.add_argument("--claude-results", type=Path, default=HOSTS[1][2])
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if output is not current")
    args = parser.parse_args()

    task_set = load_json(args.tasks)
    reports = []
    for host_id, label, default_path in HOSTS:
        path = getattr(args, f"{host_id}_results")
        if path.exists():
            report = build_report(host_id, label, path, load_json(path), task_set)
            report["complete"] = True
        else:
            report = {"id": host_id, "label": label, "path": default_path, "complete": False}
        reports.append(report)

    rendered = render(args.template.read_text(encoding="utf-8"), reports, task_set)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale report: run {Path(__file__).name}")
        print(f"verified {args.output}")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"rendered {args.output}")


if __name__ == "__main__":
    main()
