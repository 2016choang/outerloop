"""Kernel-owned, best-effort per-run event chain."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from outerloop.harness import SessionResult

log = logging.getLogger(__name__)
LEDGER = "trajectory.jsonl"
MAX_LEDGER_BYTES = 4_000_000
_cap_warned: set[Path] = set()


def _event(line: bytes) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(row, dict) or type(row.get("seq")) is not int:
        return None
    return row


def record(
    run_dir: Path,
    type: str,
    data: dict[str, Any],
    *,
    actor: str = "kernel",
    refs: Iterable[dict[str, str]] = (),
    at: float | None = None,
) -> None:
    """Preserve torn bytes, then continue from the last parseable complete line.

    The directory name is the run id. The lock covers reading and appending;
    malformed lines remain visible to verify, including after recovery.
    """
    try:
        path = run_dir / LEDGER
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "trajectory.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with path.open("a+b") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                if size >= MAX_LEDGER_BYTES:
                    _warn_cap(path)
                    return
                stream.seek(0)
                raw = stream.read()
                seq, prev = 0, hashlib.sha256(run_dir.name.encode()).hexdigest()
                for line in reversed(raw.splitlines(keepends=True)):
                    if (row := _event(line.removesuffix(b"\n"))) is not None:
                        seq = row["seq"] + 1
                        prev = hashlib.sha256(line.removesuffix(b"\n")).hexdigest()
                        break
                event = dict(
                    seq=seq,
                    ts=time.time() if at is None else float(at),
                    type=type,
                    actor=actor,
                    data=data,
                    refs=list(refs),
                    prev=prev,
                )
                prefix = b"\n" if raw and not raw.endswith(b"\n") else b""
                encoded = prefix + json.dumps(event, sort_keys=True).encode() + b"\n"
                if size + len(encoded) > MAX_LEDGER_BYTES:
                    _warn_cap(path)
                    return
                stream.write(encoded)
    except Exception as exc:
        log.warning("could not write trajectory in %s: %s", run_dir, exc)


def _warn_cap(path: Path) -> None:
    if path not in _cap_warned:
        _cap_warned.add(path)
        log.warning("trajectory size cap reached: %s", path)


def file_refs(run_dir: Path, *paths: Path) -> list[dict[str, str]]:
    """Hash existing host-owned artifacts once, outside the append lock."""
    refs = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(run_dir.resolve())
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            refs.append({"path": str(relative), "sha256": digest})
        except (OSError, ValueError):
            continue
    return refs


def read(run_dir: Path) -> list[dict]:
    """Missing legacy histories are empty; damaged lines are skipped. Any other
    read failure raises, so an unreadable chain is never shown as absent."""
    try:
        with (run_dir / LEDGER).open("rb") as stream:
            raw = stream.read(MAX_LEDGER_BYTES)
    except FileNotFoundError:
        return []
    return [
        row
        for line in raw.splitlines(keepends=True)
        if line.endswith(b"\n") and (row := _event(line[:-1])) is not None
    ]


def summarize(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Count recorded syscall decisions and collect session authors in first-seen order."""
    decisions = 0
    refusals = 0
    by_verb: dict[str, int] = {}
    authors: list[str] = []
    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            data = {}
        if event.get("type") == "syscall.decision":
            decisions += 1
            if data.get("outcome") == "refused":
                refusals += 1
                verb = data.get("verb") or "unknown"
                by_verb[verb] = by_verb.get(verb, 0) + 1
        elif event.get("type") == "session.start":
            author = f"{data.get('backend') or 'unknown'}/{data.get('model') or 'unknown'}"
            if author not in authors:
                authors.append(author)
    return {
        "decisions": decisions,
        "refusals": refusals,
        "refusals_by_verb": by_verb,
        "authors": authors,
    }


def verify(run_dir: Path, run_id: str) -> list[str]:
    """Check exact line bytes and sequence continuity without changing files."""
    try:
        with (run_dir / LEDGER).open("rb") as stream:
            raw = stream.read(MAX_LEDGER_BYTES + 1)
    except FileNotFoundError:
        return ["no trajectory"]
    except OSError as exc:
        return [f"cannot read trajectory: {exc}"]
    problems = []
    if len(raw) > MAX_LEDGER_BYTES:
        problems.append("trajectory exceeds size cap")
    seq, prev = 0, hashlib.sha256(run_id.encode()).hexdigest()
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        if not line.endswith(b"\n") or (row := _event(line[:-1])) is None:
            problems.append(f"line {number}: torn or malformed line")
            continue
        if row["seq"] != seq:
            problems.append(f"line {number}: sequence gap (expected {seq}, got {row['seq']})")
        if row.get("prev") != prev:
            problems.append(
                f"line {number}: previous hash mismatch (line {number - 1} changed or missing)"
            )
        if not all(key in row for key in ("ts", "type", "actor", "data", "refs")):
            problems.append(f"line {number}: missing event fields")
        seq, prev = row["seq"] + 1, hashlib.sha256(line[:-1]).hexdigest()
    return problems


def session_event(
    workspace: Path,
    type: str,
    backend: str,
    model: str,
    session_id: str,
    *,
    result: SessionResult | None = None,
    outcome: str = "",
) -> None:
    """Harness invocations outside a kernel run have no run trajectory."""
    try:
        directory = next((p for p in workspace.parents if p.parent.name == "runs"), None)
        if directory is None:
            return
        state = json.loads((directory / "state.json").read_text())
        data = {
            "backend": backend,
            "model": model,
            "session_id": session_id,
            "stage": state.get("stage", {}).get("phase", "author"),
        }
        refs = []
        if result is not None:
            data.update(outcome=result.stop_reason, is_error=result.is_error)
            if result.session_record_path:
                with contextlib.suppress(OSError, ValueError, TypeError, AttributeError):
                    evidence = json.loads(Path(result.session_record_path).read_text())
                    native = evidence.get("artifacts", {}).get("native", {})
                    if native.get("path"):
                        refs = file_refs(directory, Path(native["path"]))
        elif outcome:
            data.update(outcome=outcome, is_error=True)
        record(directory, type, data, refs=refs)
    except Exception as exc:
        log.warning("could not record session trajectory: %s", exc)
