"""The operator's CLI note lands in a run's inbox and renders at its wake."""

from dataclasses import replace

import pytest

from outerloop.cli import main
from outerloop.inbox import pending, wake_pending
from outerloop.runstate import ENDED, PARKED, RunRecord, run_dir, save_record


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr("outerloop.cli.env_file_values", lambda **kwargs: {})
    record = RunRecord(run_id="r1", target="owner/repo", task_title="Try a change", state=PARKED)
    save_record(tmp_path, record, 1)
    return record


def test_command_message_renders_as_the_wake_delivers_it(tmp_path, run, capsys):
    from outerloop.brief import code_fence
    from outerloop.orchestrator import _render_author_inbox

    text = "this deployment stops this run's slot at 48 GPU-hours; about 4 are left"
    assert main(["message", "r1", "--root", str(tmp_path), text]) == 0
    assert "message 1 queued" in capsys.readouterr().out
    directory = run_dir(tmp_path, "r1")
    messages = pending(directory, 0)
    assert [(m.kind, m.source, m.origin) for m in messages] == [("note", "kernel", "operator")]
    # the message itself is the sweep's existing wake trigger, like a PR comment
    assert wake_pending(directory, run)
    rendered = _render_author_inbox(
        messages, budgets="Budgets: 2 launches", redact_secrets=(), inbox_dir=directory
    )
    assert "## #1 note | kernel -> you" in rendered
    assert f"Message from the operator:\n\n{text}" in rendered
    # operator text is trusted like the kernel's own notes: no data fence
    assert code_fence(text) not in rendered


@pytest.mark.parametrize(
    ("run_id", "ended", "error"),
    [
        ("missing", False, "unknown run id"),
        ("../r1", False, "unknown run id"),
        (".", False, "unknown run id"),
        ("..", False, "unknown run id"),
        ("r1", True, "already ended"),
    ],
)
def test_command_refuses_unknown_or_ended_runs(tmp_path, run, run_id, ended, error, capsys):
    if ended:
        save_record(tmp_path, replace(run, state=ENDED, ending="operator"), 2)
    assert main(["message", run_id, "--root", str(tmp_path), "hello"]) == 2
    assert error in capsys.readouterr().err
    assert not (run_dir(tmp_path, "r1") / "inbox").exists()
