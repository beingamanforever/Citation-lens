#!/usr/bin/env python3
"""Extract the facts the explainer shows from a recorded evaluation run into data.js.

    python video/make_data.py --run ../output/evals-v3/heldout-claude-rerun \\
        --grades ../output/evals-v3/heldout-claude-rerun/grades.json \\
        --task state_space_frontier --rep 2
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def events(path):
    for line in path.read_text().splitlines():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def tool_blocks(path):
    for event in events(path):
        message = event.get("message") or {}
        yield from (message.get("content") or []) if isinstance(message, dict) else []


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def short(title, limit=30):
    """A name that fits a chip: the part before the colon, trimmed to whole words."""
    name = title.split(":")[0]
    if len(name) <= limit:
        return name
    words = []
    for word in name.split():
        if len(" ".join(words + [word])) > limit - 1:
            break
        words.append(word)
    return " ".join(words) + "…"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--task", default="state_space_frontier")
    parser.add_argument("--rep", type=int, default=2)
    args = parser.parse_args()
    grades = json.loads(args.grades.read_text())
    name = f"{args.task}-lens-r{args.rep}"
    uses, results = {}, {}
    for block in tool_blocks(args.run / "events" / f"{name}.jsonl"):
        if block.get("type") == "tool_use":
            uses[block["id"]] = block
        elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
            results[block["tool_use_id"]] = text_of(block.get("content"))
    search = expand = None
    for uid, use in uses.items():
        tool = use["name"].rsplit("__", 1)[-1]
        if tool == "research_search" and search is None:
            search = (use["input"], json.loads(results[uid]))
        if tool == "research_expand" and expand is None:
            expand = (use["input"], json.loads(results[uid]))
    answer = {e["key"]: e for e in grades["papers"][name]}
    seeds = [
        {
            "title": short(answer[s]["title"]),
            "full": answer[s]["title"],
            "year": answer[s]["paper"]["year"],
        }
        for s in expand[0]["seed_ids"]
    ]

    def lane(label, count=4):
        cards = [c for c in expand[1]["papers"] if c.get("lane") == label][:count]
        return [
            {"title": c["title"], "year": c["year"], "cites": c["cites"], "why": c["why"]}
            for c in cards
        ]

    sides = {}
    for mode in ("web", "lens"):
        key = f"{args.task}-{mode}-r{args.rep}"
        score = next(
            s
            for s in grades["scores"]
            if (s["task"], s["mode"], s["rep"]) == (args.task, mode, args.rep)
        )
        papers = [e for e in grades["papers"][key] if e.get("status") == "ok"]
        index = {e["key"]: i for i, e in enumerate(papers)}
        sides[mode] = {
            "verified": score["citations_verified"],
            "claimed": len(grades["edges"][key]),
            "recent_useful": score["recent_useful"],
            "useful": score["useful"],
            "seconds": round(score["seconds"]),
            "calls": score["tool_calls"],
            "nodes": [
                {"year": e["paper"]["year"], "recent": bool(e.get("recent"))} for e in papers
            ],
            "edges": [
                [index[e["citing"]], index[e["cited"]]]
                for e in grades["edges"][key]
                if e.get("verified") and e["citing"] in index and e["cited"] in index
            ],
        }
    seed_keys = set(expand[0]["seed_ids"])
    proof = next(
        e
        for e in grades["edges"][name]
        if e.get("verified")
        and (e.get("evidence") or {}).get("reference")
        and e["cited"] in seed_keys
        and e["citing"] in answer
        and e["cited"] in answer
    )
    reference = proof["evidence"]["reference"]
    data = {
        "question": "What's new in linear-time sequence modeling?",
        "queries": search[0]["query"],
        "search": {
            "shown": len(search[1]["papers"]),
            "cards": [c["title"] for c in search[1]["papers"][:6]],
        },
        "seeds": seeds,
        "expand": {
            "total": expand[1]["total"],
            "shown": len(expand[1]["papers"]),
            "edges": len(expand[1]["edges"]),
            "foundation": lane("foundation"),
            "follow-up": lane("follow-up"),
            "recent": lane("recent"),
        },
        "verify": {
            "citing": short(answer[proof["citing"]]["title"]),
            "cited": short(answer[proof["cited"]]["title"]),
            "reference": " ".join(reference.split()),
        },
        "web": sides["web"],
        "lens": sides["lens"],
    }
    out = HERE / "data.js"
    out.write_text("window.VIDEO_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print(f"wrote {out}")
    print(json.dumps({k: data[k] for k in ("search", "seeds", "verify")}, ensure_ascii=False)[:900])
    print(
        {
            m: {
                k: sides[m][k] for k in ("verified", "claimed", "recent_useful", "seconds", "calls")
            }
            for m in sides
        }
    )
    print("foundation:", [c["title"][:50] for c in data["expand"]["foundation"]])


if __name__ == "__main__":
    main()
