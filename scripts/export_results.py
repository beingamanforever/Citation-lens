"""Export graded research answers and evidence without private host event logs."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    grades = json.loads(args.grades.read_text())
    attempts = [
        json.loads(path.read_text()) for path in sorted((args.run / "attempts").glob("*.json"))
    ]
    tasks = json.loads((args.run / "tasks.json").read_text())["tasks"]
    names = {attempt["task"] for attempt in attempts}
    result = {
        "protocol": {
            key: manifest.get(key)
            for key in (
                "agent",
                "model",
                "effort",
                "date",
                "deadline_seconds",
                "tool_budget",
                "max_papers",
                "jobs",
                "cache",
            )
        },
        "packages": sorted({attempt["package"] for attempt in attempts}),
        "tasks": [task for task in tasks if task["name"] in names],
        "attempts": [
            {
                key: attempt.get(key)
                for key in (
                    "agent",
                    "task",
                    "mode",
                    "rep",
                    "package",
                    "status",
                    "seconds",
                    "usage",
                    "tool_calls",
                    "answer",
                )
            }
            for attempt in attempts
        ],
        "grading": {
            key: grades.get(key)
            for key in (
                "grading_revision",
                "offline",
                "pairwise_rerun",
                "judge",
                "summary",
                "scores",
                "pairs",
                "papers",
                "edges",
                "anchors",
            )
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(f"Exported {len(attempts)} attempts to {args.out}")


if __name__ == "__main__":
    main()
