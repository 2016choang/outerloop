import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from outerloop import trajectory


def test_summarize() -> None:
    start = {"type": "session.start", "data": {"backend": "claude", "model": "model-x"}}
    events = [start, {"type": "wake", "data": {}}, start]
    events.extend(
        {"type": "syscall.decision", "data": {"verb": verb, "outcome": outcome}}
        for verb, outcome in (
            ("submit", "accepted"),
            ("submit", "refused"),
            ("message", "refused"),
            ("submit", "refused"),
        )
    )
    events.append({"type": "session.start", "data": {"backend": "claude", "model": "model-y"}})
    judge = {"type": "session.start", "data": {"backend": "codex", "role": "judge"}}
    events.extend([start, judge, {"type": "wake", "data": ["malformed"]}])
    assert trajectory.summarize(iter(events)) == {
        "decisions": 4,
        "refusals": 3,
        "refusals_by_verb": {"submit": 2, "message": 1},
        "authors": ["claude/model-x", "claude/model-y"],
    }
    assert trajectory.summarize([]) == {
        "decisions": 0,
        "refusals": 0,
        "refusals_by_verb": {},
        "authors": [],
    }


def test_cli_summary(tmp_path: Path, capsys) -> None:
    from outerloop.cli import main

    directory = tmp_path / "runs" / "sample"
    trajectory.record(directory, "session.start", {"backend": "claude", "model": "model-x"})
    for verb, outcome in (
        ("submit", "accepted"),
        ("submit", "refused"),
        ("message", "refused"),
        ("submit", "refused"),
    ):
        trajectory.record(directory, "syscall.decision", {"verb": verb, "outcome": outcome})
    args = ["trajectory", "sample", "--root", str(tmp_path)]
    for flags in ([], ["--verify"]):
        assert main(args + flags) == 0
        assert capsys.readouterr().out.splitlines()[-1] == (
            "refusals: 3 of 4 decisions (submit 2, message 1); author: claude/model-x"
        )
        assert main(args + flags + ["--json"]) == 0
        expected = trajectory.read(directory)
        assert json.loads(capsys.readouterr().out) == (
            {"events": expected, "problems": []} if flags else expected
        )


def test_chain_detects_edits_and_gaps(tmp_path: Path) -> None:
    for n in range(3):
        trajectory.record(tmp_path, "measure", {"value": n}, at=n)
    path = tmp_path / trajectory.LEDGER
    original = path.read_bytes()
    assert trajectory.verify(tmp_path, tmp_path.name) == []
    assert [e["seq"] for e in trajectory.read(tmp_path)] == [0, 1, 2]
    lines = original.splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"value": 1', b'"value": 9')
    path.write_bytes(b"".join(lines))
    assert "line 2 changed" in " ".join(trajectory.verify(tmp_path, tmp_path.name))
    path.write_bytes(b"".join(original.splitlines(keepends=True)[::2]))
    assert "sequence gap (expected 1, got 2)" in " ".join(
        trajectory.verify(tmp_path, tmp_path.name)
    )


@pytest.mark.parametrize("tail", [b'{"seq":', b"\xff"])
def test_torn_line_remains_visible_after_recovery(tmp_path: Path, tail: bytes) -> None:
    trajectory.record(tmp_path, "wake", {})
    path = tmp_path / trajectory.LEDGER
    with path.open("ab") as stream:
        stream.write(tail)
    trajectory.record(tmp_path, "ending", {})
    trajectory.record(tmp_path, "wake", {})
    assert [e["seq"] for e in trajectory.read(tmp_path)] == [0, 1, 2]
    assert trajectory.verify(tmp_path, tmp_path.name) == ["line 2: torn or malformed line"]
    assert tail in path.read_bytes()


def test_complete_json_missing_newline(tmp_path: Path) -> None:
    trajectory.record(tmp_path, "wake", {})
    path = tmp_path / trajectory.LEDGER
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    assert trajectory.verify(tmp_path, tmp_path.name) == ["line 1: torn or malformed line"]
    trajectory.record(tmp_path, "ending", {})
    assert trajectory.verify(tmp_path, tmp_path.name) == []


def test_legacy_run_without_trajectory(tmp_path: Path, capsys) -> None:
    from outerloop.cli import main

    directory = tmp_path / "runs" / "legacy"
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps({"run_id": "legacy", "state": "ended"}))
    assert trajectory.read(directory) == []
    assert trajectory.verify(directory, "legacy") == ["no trajectory"]
    assert main(["trajectory", "legacy", "--root", str(tmp_path), "--verify"]) == 0
    assert "no trajectory" in capsys.readouterr().out
    assert not (directory / trajectory.LEDGER).exists()
    # an unreadable chain is an error, never "no trajectory"
    (directory / trajectory.LEDGER).mkdir()
    assert main(["trajectory", "legacy", "--root", str(tmp_path)]) == 1
    assert "cannot read trajectory" in capsys.readouterr().err


def test_concurrent_appends_and_best_effort_limits(tmp_path: Path, monkeypatch, caplog) -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: trajectory.record(tmp_path, "wake", {"n": n}), range(12)))
    assert len(trajectory.read(tmp_path)) == 12
    assert trajectory.verify(tmp_path, tmp_path.name) == []
    monkeypatch.setattr(trajectory, "MAX_LEDGER_BYTES", 1)
    for _ in range(2):
        trajectory.record(tmp_path, "wake", {})
    assert caplog.text.count("size cap reached") == 1
    blocked = tmp_path / "file"
    blocked.write_text("")
    trajectory.record(blocked, "wake", {})
    assert "could not write trajectory" in caplog.text


def test_non_serializable_data_is_best_effort(tmp_path: Path, caplog) -> None:
    circular: dict = {}
    circular["self"] = circular
    for value in (tmp_path, object(), circular):
        trajectory.record(tmp_path, "measure", {"value": value})
    assert caplog.text.count("could not write trajectory") == 3
    assert trajectory.read(tmp_path) == []
    trajectory.record(tmp_path, "wake", {})
    assert trajectory.verify(tmp_path, tmp_path.name) == []
