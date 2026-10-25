import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from outerloop import trajectory

# conftest stubs the key so unrelated run-ending tests touch no real config;
# these tests exercise the file itself.
REAL_ATTESTATION_KEY = trajectory._attestation_key


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
        "tool_calls": 0,
        "tool_calls_by_tool": {},
        "authors": ["claude/model-x", "claude/model-y"],
    }
    assert trajectory.summarize([]) == {
        "decisions": 0,
        "refusals": 0,
        "refusals_by_verb": {},
        "tool_calls": 0,
        "tool_calls_by_tool": {},
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
            "refusals: 3 of 4 decisions (submit 2, message 1); author: claude/model-x; "
            "tool calls: 0"
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


def test_attest_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(trajectory, "_attestation_key", REAL_ATTESTATION_KEY)
    config = tmp_path / "config"
    monkeypatch.setenv("OUTERLOOP_ENV_FILE", str(config / ".env"))
    directory = tmp_path / "runs" / "sample"
    trajectory.record(directory, "measure", {"value": 1}, at=1)

    trajectory.attest(directory, "sample")

    events = trajectory.read(directory)
    assert [event["type"] for event in events] == ["measure", "attest"]
    data = events[-1]["data"]
    assert data["alg"] == "hmac-sha256"
    assert data["head"] == events[-1]["prev"]
    assert len(data["mac"]) == 64
    assert trajectory.check_attest(directory, "sample") is True
    assert trajectory.verify(directory, "sample") == []
    key = config / trajectory.ATTEST_KEY
    assert key.stat().st_mode & 0o777 == 0o600
    assert key.read_bytes() != b""
    assert not (directory / trajectory.ATTEST_KEY).exists()


def test_verify_reports_attested_then_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    from outerloop.cli import main

    monkeypatch.setattr(trajectory, "_attestation_key", REAL_ATTESTATION_KEY)
    monkeypatch.setenv("OUTERLOOP_ENV_FILE", str(tmp_path / "config" / ".env"))
    directory = tmp_path / "runs" / "sample"
    trajectory.record(directory, "measure", {"value": 1}, at=1)
    trajectory.record(directory, "ending", {}, at=2)
    trajectory.attest(directory, "sample")
    args = ["trajectory", "sample", "--root", str(tmp_path), "--verify"]

    assert main(args) == 0
    out = capsys.readouterr().out
    assert "chain intact" in out
    assert "attested\n" in out

    # an event appended after the attestation is reported as not covered
    trajectory.record(directory, "launch.result", {}, at=3)
    assert main(args) == 0
    assert "attested; 1 later event(s) not covered" in capsys.readouterr().out

    path = directory / trajectory.LEDGER
    lines = path.read_bytes().splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"data": {}', b'"data": {"x": 1}')
    path.write_bytes(b"".join(lines))

    assert main(args) == 1
    out = capsys.readouterr().out
    assert "attestation mismatch" in out


def test_attest_without_a_key_skips_the_event(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(trajectory, "_attestation_key", REAL_ATTESTATION_KEY)
    config = tmp_path / "config"
    config.write_text("")  # a file where the config dir should be
    monkeypatch.setenv("OUTERLOOP_ENV_FILE", str(config / ".env"))
    directory = tmp_path / "runs" / "sample"
    trajectory.record(directory, "measure", {"value": 1}, at=1)

    with caplog.at_level("WARNING"):
        trajectory.attest(directory, "sample")

    assert [event["type"] for event in trajectory.read(directory)] == ["measure"]
    assert trajectory.check_attest(directory, "sample") is None
    assert "attestation key unavailable" in caplog.text
