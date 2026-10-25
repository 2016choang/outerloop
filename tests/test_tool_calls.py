"""Synthetic native logs only: no real session content or tool output."""

from __future__ import annotations

import json
from pathlib import Path

from outerloop import tool_calls, trajectory
from outerloop.harness import SessionResult
from outerloop.orchestrator import AttemptResult, RunConfig, pr_body
from outerloop.session_evidence import capture_session


def test_claude_calls() -> None:
    log = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": "/work",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "Bash",
                                "input": {"command": "uv run pytest -q"},
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "cwd": "/work",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t1",
                                "content": "1 failed",
                                "is_error": True,
                            }
                        ]
                    },
                    "toolUseResult": {"stdout": "1 failed", "exitCode": 1},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": "/work",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "t2",
                                "name": "Edit",
                                "input": {"file_path": "/work/src/bench.py"},
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
                                "tool_use_id": "t2",
                                "content": "ok",
                                "is_error": False,
                            }
                        ]
                    },
                }
            ),
            '{"type": "assistant", malformed',
        ]
    )
    assert tool_calls.extract("claude", log) == [
        {
            "tool": "Bash",
            "files": [],
            "command": "uv run pytest -q",
            "id": "t1",
            "exit": 1,
            "ok": False,
        },
        {"tool": "Edit", "files": ["src/bench.py"], "id": "t2", "ok": True},
    ]
    # a path outside the session's working directory is dropped, not recorded
    assert tool_calls._relative("/etc/hosts", "/work") == ""
    assert tool_calls._relative("../elsewhere/x.py", "/work") == ""


def test_codex_calls() -> None:
    log = "\n".join(
        [
            json.dumps({"type": "session_meta", "payload": {"id": "s1", "cwd": "/work"}}),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps({"command": ["bash", "-lc", "uv run pytest"]}),
                        "call_id": "c1",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": "Process exited with code 2\nOutput:\n",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": "*** Begin Patch\n*** Update File: src/bench.py\n+x\n"
                        "*** End Patch\n",
                        "call_id": "c2",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": "c2",
                        "output": "Success. Updated the following files:\nM src/bench.py\n",
                    },
                }
            ),
            # newer Codex: a script tool whose input is the script itself
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "input": 'text(await shell("uv run pytest -q"))',
                        "call_id": "c3",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call_output",
                        "call_id": "c3",
                        "output": [{"type": "input_text", "text": "Script completed\nok"}],
                    },
                }
            ),
            "not json",
        ]
    )
    assert tool_calls.extract("codex", log) == [
        {
            "tool": "shell",
            "files": [],
            "command": "bash -lc uv run pytest",
            "id": "c1",
            "exit": 2,
            "ok": False,
        },
        {"tool": "apply_patch", "files": ["src/bench.py"], "id": "c2"},
        {
            "tool": "exec",
            "files": [],
            "command": 'text(await shell("uv run pytest -q"))',
            "id": "c3",
            "ok": True,
        },
    ]
    assert tool_calls.extract("hermes", log) == []
    assert tool_calls.extract("unknown", log) == []


def test_session_end_appends_tool_call_events(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "synthetic"
    ws = run / "ws"
    ws.mkdir(parents=True)
    (run / "state.json").write_text(
        json.dumps({"agent_id": "agent-07", "stage": {"phase": "author"}})
    )
    native = run / "ws-home/.claude/projects/project/session-1.jsonl"
    native.parent.mkdir(parents=True)
    native.write_text(
        json.dumps(
            {
                "type": "assistant",
                "cwd": str(ws),
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "ok",
                            "is_error": False,
                        }
                    ]
                },
            }
        )
        + "\n"
    )

    class Adapter:
        api_key = "synthetic-credential"
        model = "test-model"
        container_image = ""

        @capture_session("claude")
        def run(self, brief: str, workspace: Path, resume_session_id: str | None = None):
            capture = workspace.parent / "capture.json"
            capture.write_text("{}")
            return SessionResult("completed", False, 0.0, 1, "session-1", "done", str(capture))

    Adapter().run("brief", ws)
    events = trajectory.read(run)
    assert [event["type"] for event in events] == ["session.start", "tool.call", "session.end"]
    assert events[1]["actor"] == "author:agent-07"
    call = {"tool": "Bash", "files": [], "command": "ls", "id": "t1", "ok": True}
    assert events[1]["data"] == call
    assert events[1]["refs"] == []
    # a resumed leg re-reads the whole session log; its earlier calls are not recorded again
    Adapter().run("brief", ws)
    assert [e["type"] for e in trajectory.read(run)].count("tool.call") == 1


def test_pr_process_section_and_summary_line(tmp_path: Path, capsys) -> None:
    from outerloop.cli import main

    result = AttemptResult(outcome="improved", baseline=1.0, candidate=0.9)
    config = RunConfig(target="owner/repo", benchmark="bench")
    body = pr_body(result, config, (), tool_calls={"Bash": 12, "Edit": 5})
    assert "## Process" in body
    assert "17 tool calls from the run's trajectory: Bash 12, Edit 5." in body
    assert "## Process" not in pr_body(result, config, ())

    run = tmp_path / "runs" / "sample"
    trajectory.record(run, "session.start", {"backend": "claude", "model": "model-x"})
    for tool in ("Bash", "Bash", "Edit"):
        trajectory.record(run, "tool.call", {"tool": tool, "files": []})
    assert main(["trajectory", "sample", "--root", str(tmp_path)]) == 0
    assert (
        capsys.readouterr()
        .out.splitlines()[-1]
        .endswith("author: claude/model-x; tool calls: 3 (Bash 2, Edit 1)")
    )
