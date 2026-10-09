#!/usr/bin/env python3
"""Build demo/index.html: an explainer plus a side-by-side replay of recorded research runs.

Each scenario pairs one task's two recorded attempts, the host agent alone and the same agent
with Citation Lens, taken from an `evals/run.py` output folder, with verified numbers from the
`evals/grade.py` grades of that run. Nothing is simulated: queries, tool calls, returned cards
and final answers are read from the event logs and attempt records.

    python demo/build.py --run ../output/evals-v3/heldout-claude \\
        --grades ../output/evals-v3/heldout-claude-audit/grades.json \\
        --scenario state_space_frontier:2 --scenario dpo_lineage:2
"""

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARD_LIMIT = 8  # cards shown per Lens result; the replay is a preview, not a dump
LINK_LIMIT = 6  # web links shown per search result


def tool_text(content):
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def web_links(text):
    """Titles of a WebSearch result, which lists links as a JSON array after 'Links:'."""
    start = text.find("Links:")
    if start < 0:
        return []
    try:
        links, _ = json.JSONDecoder().raw_decode(text[text.index("[", start) :])
    except (ValueError, json.JSONDecodeError):
        return []
    return [{"title": link.get("title", "")[:140], "url": link.get("url", "")} for link in links]


def lens_result(tool, text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"error": text[:240]}
    cards = [
        {k: card.get(k) for k in ("title", "year", "cites", "lane", "why", "snippet")}
        for card in (data.get("papers") or [])[:CARD_LIMIT]
    ]
    for card in cards:
        card["snippet"] = (card.get("snippet") or "")[:170]
    searches = data.get("searches") or []
    return {
        "total": data.get("total"),
        "shown": len(data.get("papers") or []),
        "edges": len(data.get("edges") or []),
        "bytes": len(text.encode()),
        "cards": cards,
        "providers_failed": sum("error" in s for s in searches),
        "providers_total": len(searches),
        "errors": [e.get("error", "")[:120] for e in (data.get("errors") or [])[:2]],
        "documents": len(data.get("papers") or []) if tool == "research_read" else None,
    }


def short(tool, arguments):
    if tool in ("WebSearch",):
        return arguments.get("query", "")
    if tool == "WebFetch":
        return arguments.get("url", "")
    if tool == "research_search":
        return " | ".join(arguments.get("query", []))
    if tool == "research_expand":
        return f"{len(arguments.get('seed_ids', []))} seeds: {arguments.get('query', '')}"
    if tool == "research_graph":
        return f"page at offset {arguments.get('offset', 0)}"
    if tool == "research_read":
        ids = arguments.get("paper_id")
        count = len(ids) if isinstance(ids, list) else 1
        return f"{count} papers, {arguments.get('part', 'abstract')}"
    return json.dumps(arguments)[:120]


def timeline(events_path):
    steps, by_id = [], {}
    for line in events_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message") or {}
        for block in message.get("content") or [] if isinstance(message, dict) else []:
            if block.get("type") == "tool_use" and block["name"] != "StructuredOutput":
                name = block["name"].removeprefix("mcp__citation-lens__")
                step = {"tool": name, "text": short(name, block.get("input") or {})}
                by_id[block["id"]] = step
                steps.append(step)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                step, text = by_id[block["tool_use_id"]], tool_text(block.get("content"))
                if block.get("is_error"):
                    step["failed"] = re.sub(r"\s+", " ", text)[:200]
                elif step["tool"] == "WebSearch":
                    links = web_links(text)
                    step["n_links"], step["links"] = len(links), links[:LINK_LIMIT]
                elif step["tool"] == "WebFetch":
                    step["note"] = re.sub(r"\s+", " ", text.replace("**", ""))[:200]
                else:
                    step["lens"] = lens_result(step["tool"], text)
    return steps


def side(run, grades, task, mode, rep):
    name = f"{task}-{mode}-r{rep}"
    record = json.loads((run / "attempts" / f"{name}.json").read_text())
    score = next(
        s for s in grades["scores"] if (s["task"], s["mode"], s["rep"]) == (task, mode, rep)
    )
    papers = []
    for entry in grades["papers"][name]:
        paper = entry.get("paper") or {}
        papers.append(
            {
                "key": entry.get("key"),
                "title": entry["title"],
                "year": paper.get("year"),
                "recent": bool(entry.get("recent")),
                "found": entry.get("status") == "ok",
            }
        )
    keys = {p["key"] for p in papers}
    verified = [
        [e["citing"], e["cited"]]
        for e in grades["edges"][name]
        if e.get("verified") and e["citing"] in keys and e["cited"] in keys
    ]
    return {
        "seconds": record["seconds"],
        "steps": timeline(run / "events" / f"{name}.jsonl"),
        "papers": papers,
        "links_claimed": len(grades["edges"][name]),
        "links_verified": score["citations_verified"],
        "verified_edges": verified,
        "useful": score["useful"],
        "recent_useful": score["recent_useful"],
        "model": record.get("model"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", type=Path, required=True, help="evals/run.py output folder")
    parser.add_argument("--grades", type=Path, required=True, help="grades.json of that run")
    parser.add_argument("--scenario", action="append", required=True, help="task:repetition")
    parser.add_argument("--out", type=Path, default=HERE / "index.html")
    args = parser.parse_args()
    grades = json.loads(args.grades.read_text())
    tasks = {t["name"]: t for t in json.loads((args.run / "tasks.json").read_text())["tasks"]}
    manifest = json.loads((args.run / "manifest.json").read_text())
    scenarios = []
    for spec in args.scenario:
        task, rep = spec.split(":")
        scenarios.append(
            {
                "id": f"{task}-r{rep}",
                "task": task,
                "label": task.replace("_", " "),
                "prompt": tasks[task]["prompt"],
                "web": side(args.run, grades, task, "web", int(rep)),
                "lens": side(args.run, grades, task, "lens", int(rep)),
            }
        )
    data = {
        "agent": manifest.get("agent", "claude"),
        "date": manifest.get("date"),
        "scenarios": scenarios,
    }
    template = (HERE / "template.html").read_text()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    args.out.write_text(template.replace("__DATA__", payload))
    print(f"wrote {args.out} ({args.out.stat().st_size // 1024} KB, {len(scenarios)} scenarios)")


if __name__ == "__main__":
    main()
