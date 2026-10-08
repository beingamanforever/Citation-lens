import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))

import run as runner  # noqa: E402
from run import (  # noqa: E402
    TOOL_BUDGET,
    answer_errors,
    attempt_status,
    build_prompt,
    summarize_claude_events,
    summarize_events,
)

TASK = {
    "name": "t",
    "prompt": "Find papers on exact attention.",
    "split": "development",
    "kind": "systems",
    "anchors": [{"id": "X"}],
    "required_approaches": ["secret approach"],
}

ALTERNATE_TASK = TASK | {
    "name": "fresh",
    "prompt": "Find fresh papers on exact attention.",
    "split": "held_out",
}


def valid_answer():
    return {
        "papers": [
            {
                "title": "Paper",
                "url": "https://example.test/paper",
                "role": "foundation",
                "reason": "reason",
                "quote": "quote",
            }
        ],
        "citations": [],
        "summary": "summary",
        "gaps": [],
    }


def test_prompts_hide_grading_fields_and_differ_only_in_tools():
    web, lens = (build_prompt(TASK, mode, date(2026, 10, 8)) for mode in ("web", "lens"))
    for prompt in (web, lens):
        assert "Find papers on exact attention." in prompt and "secret approach" not in prompt
    assert "research_expand" in lens and "research_expand" not in web
    assert web.split("Tools:")[0] == lens.split("Tools:")[0]


def test_codex_events_count_tools_utf8_bytes_and_failures():
    result = {"content": "café"}
    lines = [
        json.dumps(event)
        for event in [
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "tool": "research_search",
                    "arguments": {},
                    "status": "completed",
                    "result": result,
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "web_search", "action": {"type": "search", "queries": ["q"]}},
            },
            {"type": "turn.failed", "error": {"message": "native failure"}},
        ]
    ]
    usage, calls, errors = summarize_events(lines)
    assert usage is None and errors and "native failure" in errors[0]
    assert [call["tool"] for call in calls] == ["research_search", "web_search"]
    assert calls[0]["result_bytes"] == len(json.dumps(result, ensure_ascii=False).encode())
    assert attempt_status(False, 0, valid_answer(), errors, []) != "ok"


def test_claude_events_skip_schema_tool_but_keep_unknown_tools_and_errors():
    lines = [
        json.dumps(event)
        for event in [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "a",
                            "name": "mcp__citation-lens__research_expand",
                            "input": {},
                        },
                        {"type": "tool_use", "id": "b", "name": "WebFetch", "input": {}},
                        {"type": "tool_use", "id": "c", "name": "StructuredOutput", "input": {}},
                        {"type": "tool_use", "id": "d", "name": "MysteryTool", "input": {}},
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "a", "content": "cards"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "b",
                            "content": "failed",
                            "is_error": True,
                        },
                    ]
                },
            },
            {
                "type": "result",
                "usage": {"input_tokens": 3},
                "is_error": True,
                "structured_output": valid_answer(),
                "result": "Claude failed",
            },
        ]
    ]
    usage, calls, errors, answer = summarize_claude_events(lines)
    assert answer == valid_answer() and usage == {"input_tokens": 3}
    assert [call["tool"] for call in calls] == ["research_expand", "web_fetch", "MysteryTool"]
    assert calls[0]["result_bytes"] > 0 and errors == ["Claude failed"]
    assert calls[1]["status"] == "failed" and calls[1]["error"] == "failed"
    assert answer_errors(answer, calls) == ["Unexpected tools: MysteryTool"]
    assert attempt_status(False, 0, answer, errors, []) != "ok"


def test_answer_schema_call_status_and_budgets_are_enforced():
    malformed = valid_answer() | {"summary": 3}
    assert answer_errors(malformed, []) == ["Invalid summary or gaps"]

    too_many_papers = valid_answer()
    too_many_papers["papers"] *= 26
    calls = [{"tool": "web_search"} for _ in range(TOOL_BUDGET + 1)]
    errors = answer_errors(too_many_papers, calls)
    assert any("Paper budget exceeded" in error for error in errors)
    assert any("Research call budget exceeded" in error for error in errors)

    failed_call = [{"tool": "research_search", "status": "failed"}]
    assert answer_errors(valid_answer(), failed_call) == []
    assert attempt_status(False, 0, valid_answer(), [], []) == "ok"
    assert attempt_status(False, 2, valid_answer(), [], []) == "process_error"


def test_recoverable_tool_error_does_not_mask_a_valid_final_answer():
    answer = valid_answer()
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__citation-lens__research_search",
                            "input": {},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "provider unavailable",
                            "is_error": True,
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "is_error": False,
                "structured_output": answer,
            }
        ),
    ]
    _, calls, errors, returned = summarize_claude_events(lines)
    assert calls[0]["status"] == "failed" and calls[0]["error"] == "provider unavailable"
    assert errors == [] and answer_errors(returned, calls) == []
    assert attempt_status(False, 0, returned, errors, []) == "ok"


def make_project(path):
    files = {
        "run.py": "# package runner\n",
        "src/citation_lens/server.py": "SERVER = 1\n",
        "skills/research/SKILL.md": "---\nname: research\n---\nFrozen playbook.\n",
        "evals/run.py": "# experiment runner\n",
        "evals/grade.py": "# experiment grader\n",
        "evals/tasks.json": json.dumps({"tasks": [TASK]}),
        "docs/EVALUATION.md": "# Evaluation protocol\n",
    }
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return path


@pytest.mark.parametrize(
    ("returncode", "is_error", "expected"),
    [(7, False, "process_error"), (0, True, "agent_error")],
)
def test_run_attempt_never_accepts_process_or_native_failure(
    tmp_path, monkeypatch, returncode, is_error, expected
):
    project = make_project(tmp_path / "project")
    out = tmp_path / "results"
    out.mkdir()
    runner.freeze_sources(project, out)
    for sub in ("attempts", "events"):
        (out / sub).mkdir()

    class FakeProcess:
        pid = 1

        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = returncode

        def communicate(self, prompt, timeout):
            event = {
                "type": "result",
                "is_error": is_error,
                "result": "native failure" if is_error else "",
                "structured_output": valid_answer(),
            }
            self.stdout.write(json.dumps(event) + "\n")
            self.stdout.flush()

    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda command, **kwargs: FakeProcess(kwargs["stdout"]),
    )
    args = SimpleNamespace(
        agent="claude",
        out=out,
        shared_cache=False,
        model="claude-opus-5-5",
        effort="high",
        package_version="abc123",
    )
    record = runner.run_attempt(TASK, "web", 1, args, date(2026, 10, 8))
    assert record["answer"] == valid_answer() and record["status"] == expected


def invoke_main(
    monkeypatch, project, out, attempts, reps=1, jobs=1, task_name="t", tasks_path=None
):
    def fake_attempt(task, mode, rep, args, today):
        name = f"{task['name']}-{mode}-r{rep}"
        attempts.append(name)
        record = {
            "status": "ok",
            "task": task["name"],
            "mode": mode,
            "rep": rep,
            "package": args.package_version,
        }
        (args.out / "attempts" / f"{name}.json").write_text(json.dumps(record))
        (args.out / "events" / f"{name}.jsonl").write_text("original events")
        return {"status": "ok"}

    monkeypatch.setattr(runner, "ROOT", project)
    monkeypatch.setattr(runner, "run_attempt", fake_attempt)
    argv = [
        "run.py",
        "--out",
        str(out),
        "--task",
        task_name,
        "--modes",
        "web",
        "--reps",
        str(reps),
        "--jobs",
        str(jobs),
    ]
    if tasks_path is not None:
        argv.extend(("--tasks", str(tasks_path)))
    monkeypatch.setattr(sys, "argv", argv)
    runner.main()


def write_tasks(path, tasks):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tasks": tasks}))


def test_alternate_task_dataset_is_selected_and_frozen(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    external_tasks = tmp_path / "private" / "heldout-v4.json"
    write_tasks(external_tasks, [ALTERNATE_TASK])
    out = tmp_path / "results"
    attempts = []

    invoke_main(
        monkeypatch,
        project,
        out,
        attempts,
        task_name="fresh",
        tasks_path=external_tasks,
    )

    manifest = json.loads((out / "manifest.json").read_text())
    assert attempts == ["fresh-web-r1"]
    assert (out / "tasks.json").read_bytes() == external_tasks.read_bytes()
    assert (out / "tasks.json").read_bytes() != (project / "evals/tasks.json").read_bytes()
    assert manifest["selection"]["tasks"] == ["fresh"]
    assert str(external_tasks) not in json.dumps(manifest)


@pytest.mark.parametrize(
    ("names", "message"),
    [
        (["same", "same"], "Duplicate task name"),
        (["/tmp/escape"], "Invalid task name"),
        (["../escape"], "Invalid task name"),
        ([""], "Invalid task name"),
        (["C:escape"], "Invalid task name"),
        (["research:alternate"], "Invalid task name"),
        (["task", "Task"], "Invalid task name"),
        (["con"], "Invalid task name"),
        (["lpt9"], "Invalid task name"),
    ],
)
def test_invalid_external_task_names_are_rejected_before_run_mutation(
    tmp_path, monkeypatch, names, message
):
    project = make_project(tmp_path / "project")
    external_tasks = tmp_path / "private" / "unsafe.json"
    write_tasks(external_tasks, [ALTERNATE_TASK | {"name": name} for name in names])
    out = tmp_path / "results"
    attempts = []

    with pytest.raises(SystemExit, match=message):
        invoke_main(
            monkeypatch,
            project,
            out,
            attempts,
            task_name=names[0],
            tasks_path=external_tasks,
        )

    assert attempts == []
    assert not out.exists()


def test_changed_external_task_dataset_rejects_resume_without_overwriting(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    external_tasks = tmp_path / "private" / "heldout-v4.json"
    write_tasks(external_tasks, [ALTERNATE_TASK])
    out = tmp_path / "results"
    attempts = []
    invoke_main(
        monkeypatch,
        project,
        out,
        attempts,
        task_name="fresh",
        tasks_path=external_tasks,
    )
    frozen = (out / "tasks.json").read_bytes()
    manifest = (out / "manifest.json").read_bytes()
    result = (out / "attempts/fresh-web-r1.json").read_bytes()

    write_tasks(external_tasks, [ALTERNATE_TASK | {"prompt": "Changed prompt."}])
    with pytest.raises(SystemExit, match="tasks snapshot.*new --out"):
        invoke_main(
            monkeypatch,
            project,
            out,
            attempts,
            reps=2,
            task_name="fresh",
            tasks_path=external_tasks,
        )

    assert (out / "tasks.json").read_bytes() == frozen
    assert (out / "manifest.json").read_bytes() == manifest
    assert (out / "attempts/fresh-web-r1.json").read_bytes() == result
    assert not (out / "attempts/fresh-web-r2.json").exists()


def test_tampered_frozen_alternate_dataset_rejects_resume(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    external_tasks = tmp_path / "private" / "heldout-v4.json"
    write_tasks(external_tasks, [ALTERNATE_TASK])
    out = tmp_path / "results"
    attempts = []
    invoke_main(
        monkeypatch,
        project,
        out,
        attempts,
        task_name="fresh",
        tasks_path=external_tasks,
    )
    manifest = (out / "manifest.json").read_bytes()
    frozen_tasks = out / "tasks.json"
    frozen_tasks.write_text(frozen_tasks.read_text() + "\n")
    tampered = frozen_tasks.read_bytes()

    with pytest.raises(SystemExit, match="frozen run snapshot changed.*new --out"):
        invoke_main(
            monkeypatch,
            project,
            out,
            attempts,
            reps=2,
            task_name="fresh",
            tasks_path=external_tasks,
        )

    assert frozen_tasks.read_bytes() == tampered
    assert (out / "manifest.json").read_bytes() == manifest
    assert not (out / "attempts/fresh-web-r2.json").exists()


def test_run_snapshot_is_immutable_and_resume_only_adds_repetitions(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    out = tmp_path / "results"
    attempts = []

    invoke_main(monkeypatch, project, out, attempts)
    manifest = json.loads((out / "manifest.json").read_text())
    snapshot_names = ("runner.py", "grader.py", "tasks.json", "EVALUATION.md")
    frozen = {name: (out / name).read_bytes() for name in snapshot_names}
    frozen_package = (out / "package/run.py").read_bytes()
    first_result = (out / "attempts/t-web-r1.json").read_bytes()
    assert attempts == ["t-web-r1"]
    assert manifest["jobs"] == 1 and manifest["cache"] == "cold"
    assert manifest["deadline_seconds"] == 420 and manifest["tool_budget"] == 8
    assert manifest["selection"] == {
        "split": "development",
        "requested_tasks": ["t"],
        "tasks": ["t"],
    }
    assert manifest["model"] == "gpt-6.1-sol" and manifest["effort"] == "high"

    invoke_main(monkeypatch, project, out, attempts, reps=2)
    assert attempts == ["t-web-r1", "t-web-r2"]
    assert (out / "attempts/t-web-r1.json").read_bytes() == first_result
    assert (out / "events/t-web-r1.jsonl").read_text() == "original events"
    assert {name: (out / name).read_bytes() for name in frozen} == frozen
    assert (out / "package/run.py").read_bytes() == frozen_package
    assert json.loads((out / "manifest.json").read_text())["reps"] == 2

    for relative in ("run.py", "evals/run.py", "evals/tasks.json", "docs/EVALUATION.md"):
        source = project / relative
        original = source.read_text()
        source.write_text(original + "changed\n")
        with pytest.raises(SystemExit, match="new --out"):
            invoke_main(monkeypatch, project, out, attempts, reps=3)
        source.write_text(original)
    with pytest.raises(SystemExit, match="configuration.*new --out"):
        invoke_main(monkeypatch, project, out, attempts, reps=3, jobs=2)
    assert not (out / "attempts/t-web-r3.json").exists()

    (out / "events/t-web-r1.jsonl").unlink()
    with pytest.raises(SystemExit, match="does not match.*new --out"):
        invoke_main(monkeypatch, project, out, attempts, reps=3)
    assert (out / "attempts/t-web-r1.json").read_bytes() == first_result


def test_legacy_run_directory_is_preserved_and_rejected(tmp_path, monkeypatch):
    project = make_project(tmp_path / "project")
    out = tmp_path / "legacy"
    out.mkdir()
    marker = out / "old-result.json"
    marker.write_text("keep me")
    (out / "manifest.json").write_text(json.dumps({"date": "2026-10-08"}))

    with pytest.raises(SystemExit, match="legacy.*new --out"):
        invoke_main(monkeypatch, project, out, [])
    assert marker.read_text() == "keep me"
