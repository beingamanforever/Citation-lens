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
DEFAULT_RESULTS = ROOT / "docs/results/codex-heldout.json"
DEFAULT_TASKS = ROOT / "evals/tasks.json"
DEFAULT_TEMPLATE = ROOT / "docs/results/report-template.html"
DEFAULT_OUTPUT = ROOT.parent / "output/citation-lens-results.html"


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


def percent_change(new: float, old: float) -> str:
    return f"{round((new / old - 1) * 100):+d}%"


def format_number(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def build_values(results: dict, task_set: dict) -> tuple[dict[str, str], list[dict]]:
    grading = results["grading"]
    if grading.get("grading_revision") != 2 or grading.get("offline") is not True:
        raise ValueError("report requires the audited offline revision-2 grading export")

    summary = grading["summary"]
    arms = summary["arms"]
    web, lens = arms["web"], arms["lens"]
    web_answered, lens_answered = arms["web_answered"], arms["lens_answered"]
    pairwise = summary["pairwise"]["overall"]
    pair_count = sum(pairwise.values())
    if pair_count != len(grading["pairs"]):
        raise ValueError("pairwise summary does not match saved pair judgments")
    if web["attempts"] != lens["attempts"] or web["attempts"] != pair_count:
        raise ValueError("attempt totals do not match pairwise judgments")

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
        raise ValueError("saved attempts do not form a complete task/rep/mode grid")

    packages = set(results["packages"])
    packages.update(attempt["package"] for attempt in attempts)
    if len(packages) != 1:
        raise ValueError("report cannot combine attempts from different package snapshots")
    package = packages.pop()

    tasks = task_set["tasks"]
    anchor_count = sum(len(task["anchors"]) for task in tasks)
    recent_anchor_count = sum(
        anchor.get("role") == "recent" for task in tasks for anchor in task["anchors"]
    )
    split_counts = Counter(task["split"] for task in tasks)
    canonical_heldout_tasks = {task["name"] for task in tasks if task["split"] == "held_out"}
    if canonical_heldout_tasks != heldout_tasks:
        raise ValueError("canonical held-out tasks do not match the saved attempts")

    evidence_sources = Counter(
        (edge.get("evidence") or {}).get("source")
        for edges in grading["edges"].values()
        for edge in edges
        if edge.get("verified")
    )
    primary_proof = sum(
        count
        for source, count in evidence_sources.items()
        if source and source.startswith("primary_")
    )
    primary_identifier_proof = evidence_sources["primary_identifier"]
    primary_title_proof = evidence_sources["primary_title_authors_year"]
    index_proof = evidence_sources["index_reference"]
    verified_proof = sum(evidence_sources.values())

    useful_comparison = compare_answered(grading["scores"], "useful")
    recent_comparison = compare_answered(grading["scores"], "recent_useful")
    citation_comparison = compare_answered(grading["scores"], "citations_useful")
    both_answered = sum(useful_comparison)

    protocol = results["protocol"]
    judge = grading["judge"]
    values = {
        "HELDOUT_TASKS": str(len(heldout_tasks)),
        "REPETITIONS": str(len(repetitions)),
        "ATTEMPTS_PER_ARM": str(web["attempts"]),
        "PAIR_LENS": str(pairwise["lens"]),
        "PAIR_WEB": str(pairwise["web"]),
        "PAIR_TIES": str(pairwise["tie"]),
        "USEFUL_CHANGE": percent_change(lens["useful"], web["useful"]),
        "RECENT_CHANGE": percent_change(lens["recent_useful"], web["recent_useful"]),
        "CITATION_CHANGE": percent_change(lens["citations_useful"], web["citations_useful"]),
        "TIME_CHANGE": percent_change(lens["seconds"], web["seconds"]),
        "WEB_USEFUL": format_number(web["useful"], 1),
        "LENS_USEFUL": format_number(lens["useful"], 1),
        "WEB_CORE": format_number(web["core"]),
        "LENS_CORE": format_number(lens["core"]),
        "WEB_RECENT": format_number(web["recent_useful"]),
        "LENS_RECENT": format_number(lens["recent_useful"]),
        "WEB_PROMINENT": format_number(web["prominent_useful"], 1),
        "LENS_PROMINENT": format_number(lens["prominent_useful"], 1),
        "WEB_CITATIONS": format_number(web["citations_useful"]),
        "LENS_CITATIONS": format_number(lens["citations_useful"]),
        "WEB_PRECISION": format_number(web_answered["precision"], 3),
        "LENS_PRECISION": format_number(lens_answered["precision"], 3),
        "WEB_ANCHOR": format_number(web["anchor_recall"], 3),
        "LENS_ANCHOR": format_number(lens["anchor_recall"], 3),
        "WEB_WRONG_URL": format_number(web["wrong_url"]),
        "LENS_WRONG_URL": format_number(lens["wrong_url"]),
        "WEB_QUOTES": f"{web['quote_verbatim_rate'] * 100:.1f}%",
        "LENS_QUOTES": f"{lens['quote_verbatim_rate'] * 100:.1f}%",
        "WEB_ANSWERED": str(web["answered"]),
        "LENS_ANSWERED": str(lens["answered"]),
        "WEB_SECONDS": str(round(web["seconds"])),
        "LENS_SECONDS": str(round(lens["seconds"])),
        "WEB_INPUT_TOKENS": f"{round(web['input_tokens'] / 1000):,}k",
        "LENS_INPUT_TOKENS": f"{round(lens['input_tokens'] / 1000):,}k",
        "WEB_TOKEN_MISSING": str(web["token_usage_missing"]),
        "LENS_TOKEN_MISSING": str(lens["token_usage_missing"]),
        "BOTH_ANSWERED": str(both_answered),
        "USEFUL_LENS_HIGHER": str(useful_comparison[0]),
        "RECENT_LENS_HIGHER": str(recent_comparison[0]),
        "CITATION_LENS_HIGHER": str(citation_comparison[0]),
        "CITATION_WEB_HIGHER": str(citation_comparison[1]),
        "CITATION_TIES": str(citation_comparison[2]),
        "LENS_BYTES_KB": f"{lens['lens_bytes'] / 1000:.0f} KB",
        "TOTAL_TASKS": str(len(tasks)),
        "DEVELOPMENT_TASKS": str(split_counts["development"]),
        "TOTAL_ANCHORS": str(anchor_count),
        "RECENT_ANCHORS": str(recent_anchor_count),
        "MODEL": str(protocol["model"]),
        "EFFORT": str(protocol["effort"]),
        "TOOL_BUDGET": str(protocol["tool_budget"]),
        "DEADLINE": str(protocol["deadline_seconds"]),
        "VERIFIED_PROOF": str(verified_proof),
        "PRIMARY_PROOF": str(primary_proof),
        "PRIMARY_IDENTIFIER_PROOF": str(primary_identifier_proof),
        "PRIMARY_TITLE_PROOF": str(primary_title_proof),
        "INDEX_PROOF": str(index_proof),
        "JUDGE_MODEL": str(judge["model"]),
        "JUDGE_EFFORT": str(judge["effort"]),
        "PACKAGE": str(package),
    }
    metrics = [
        {
            "name": "Useful papers",
            "note": "more is better",
            "codex": web["useful"],
            "lens": lens["useful"],
            "digits": 1,
        },
        {
            "name": "Recent useful papers",
            "note": "last two years",
            "codex": web["recent_useful"],
            "lens": lens["recent_useful"],
            "digits": 2,
        },
        {
            "name": "Verified citation links",
            "note": "between useful papers",
            "codex": web["citations_useful"],
            "lens": lens["citations_useful"],
            "digits": 2,
        },
        {
            "name": "Seconds per attempt",
            "note": "lower is better",
            "codex": web["seconds"],
            "lens": lens["seconds"],
            "digits": 0,
        },
    ]
    return values, metrics


def render(template: str, values: dict[str, str], metrics: list[dict]) -> str:
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace(f"{{{{{name}}}}}", html.escape(value))
    rendered = rendered.replace("{{METRICS_JSON}}", json.dumps(metrics, ensure_ascii=False))
    unresolved = sorted(set(re.findall(r"{{[A-Z0-9_]+}}", rendered)))
    if unresolved:
        raise ValueError(f"unresolved template values: {', '.join(unresolved)}")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if output is not current")
    args = parser.parse_args()

    values, metrics = build_values(load_json(args.results), load_json(args.tasks))
    rendered = render(args.template.read_text(encoding="utf-8"), values, metrics)
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
