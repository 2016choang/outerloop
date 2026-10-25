"""Tool calls extracted host-side from the retained, redacted native session log.

Pure parsers skip malformed lines and bound every field; the record hook is
best-effort, like the rest of the trajectory.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from outerloop.trajectory import read, record

MAX_CALLS = 500
MAX_FILES = 20
MAX_PATH = 200
MAX_COMMAND = 200
MAX_TOOL = 100
MAX_LOG_BYTES = 32 * 1024 * 1024  # session_evidence retains up to this much
MAX_ID = 100

_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_EXIT_CODE = re.compile(r"(?:Process exited with code|Exit code)\s*:?\s*(-?\d+)", re.IGNORECASE)
_PATCH_PREFIXES = (
    "*** Add File: ",
    "*** Update File: ",
    "*** Delete File: ",
    "*** Move to: ",
)


def _rows(text: str) -> Iterator[dict[str, Any]]:
    """JSON objects from a JSONL log; malformed and non-object lines are skipped."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [
            part["text"]
            for part in value
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
    return []


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _command(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(part for part in value if isinstance(part, str))
    if isinstance(value, str) and value.strip():
        return value[:MAX_COMMAND]
    return ""


def _relative(value: str, cwd: str) -> str:
    """Strip the session's working directory so files read as repo-relative;
    a path outside it reads as "" and is dropped."""
    text = value.strip()
    root = cwd.rstrip("/")
    if root and text.startswith(root + "/"):
        text = text[len(root) + 1 :]
    if text.startswith(("/", "~")) or ".." in text.split("/"):
        return ""
    return text[:MAX_PATH]


def _files(args: dict[str, Any], cwd: str) -> list[str]:
    found: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
    edits = args.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("file_path"), str):
                found.append(edit["file_path"])
    files: list[str] = []
    for value in found:
        relative = _relative(value, cwd)
        if relative and relative not in files:
            files.append(relative)
        if len(files) >= MAX_FILES:
            break
    return files


def _exit_code(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in ("exitCode", "exit_code", "returnCode", "return_code"):
        code = value.get(key)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
    return None


def _apply_result(call: dict[str, Any], is_error: Any, text: str, extra: Any) -> None:
    """Attach the exit code and outcome a completed call's log line carries."""
    code = _exit_code(extra)
    if code is None and (match := _EXIT_CODE.search(text)):
        code = int(match.group(1))
    if code is not None:
        call["exit"] = code
        call["ok"] = code == 0
    elif isinstance(is_error, bool):
        call["ok"] = not is_error


def claude_calls(text: str) -> Iterator[dict[str, Any]]:
    """Claude Code project transcripts: assistant `tool_use` parts matched to
    user `tool_result` parts by id, with `toolUseResult` as the exit detail."""
    cwd = ""
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in _rows(text):
        if isinstance(row.get("cwd"), str):
            cwd = row["cwd"]
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                name = part.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                args = part.get("input")
                args = args if isinstance(args, dict) else {}
                call: dict[str, Any] = {"tool": name[:MAX_TOOL], "files": _files(args, cwd)}
                if command := _command(args.get("command")):
                    call["command"] = command
                call_id = part.get("id")
                if isinstance(call_id, str):
                    call["id"] = call_id[:MAX_ID]
                    by_id[call_id] = call
                calls.append(call)
            elif part.get("type") == "tool_result":
                result_id = part.get("tool_use_id")
                pending = by_id.get(result_id) if isinstance(result_id, str) else None
                if pending is not None:
                    _apply_result(
                        pending,
                        part.get("is_error"),
                        "\n".join(_text(part.get("content"))),
                        row.get("toolUseResult"),
                    )
    yield from calls[:MAX_CALLS]


def _patch_files(patch: str, cwd: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        for prefix in _PATCH_PREFIXES:
            if line.startswith(prefix):
                relative = _relative(line[len(prefix) :], cwd)
                if relative and relative not in files:
                    files.append(relative)
                if len(files) >= MAX_FILES:
                    return files
    return files


def codex_calls(text: str) -> Iterator[dict[str, Any]]:
    """Codex rollout JSONL: `function_call` (shell) and `custom_tool_call`
    (apply_patch) items matched to their outputs by call id."""
    cwd = ""
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in _rows(text):
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            if isinstance(payload.get("cwd"), str):
                cwd = payload["cwd"]
            continue
        kind = payload.get("type")
        if kind in ("function_call", "custom_tool_call"):
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            args = _arguments(payload.get("arguments"))
            patch = payload.get("input") if kind == "custom_tool_call" else None
            call: dict[str, Any] = {"tool": name[:MAX_TOOL], "files": _files(args, cwd)}
            if isinstance(patch, str):
                call["files"] = _patch_files(patch, cwd)
            elif isinstance(args.get("input"), str):
                call["files"] = _patch_files(args["input"], cwd)
            if command := _command(args.get("command") or args.get("cmd")):
                call["command"] = command
            elif name == "exec" and isinstance(patch, str) and (command := _command(patch)):
                # newer Codex runs commands through a script tool whose input
                # is the script itself, not JSON arguments
                call["command"] = command
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                call["id"] = call_id[:MAX_ID]
                by_id[call_id] = call
            calls.append(call)
        elif kind in ("function_call_output", "custom_tool_call_output"):
            result_id = payload.get("call_id")
            pending = by_id.get(result_id) if isinstance(result_id, str) else None
            if pending is not None:
                output = "\n".join(_text(payload.get("output")))
                _apply_result(pending, None, output, None)
                # the script tool reports success in its first words, not an exit code
                if "ok" not in pending and output.startswith("Script completed"):
                    pending["ok"] = True
    yield from calls[:MAX_CALLS]


def extract(backend: str, text: str) -> list[dict[str, Any]]:
    """Tool calls from one retained native log. Unknown backends yield nothing."""
    parser = {"claude": claude_calls, "codex": codex_calls}.get(backend)
    return list(parser(text)) if parser is not None else []


def _read_retained(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(MAX_LOG_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _actor(directory: Path, workspace: Path) -> str:
    try:
        state = json.loads((directory / "state.json").read_text())
    except (OSError, ValueError):
        return "kernel"
    if not isinstance(state, dict) or workspace.relative_to(directory).parts[:1] == ("panel",):
        return "kernel"
    agent = state.get("agent_id")
    return f"author:{agent}" if isinstance(agent, str) and _AGENT_ID.fullmatch(agent) else "kernel"


def record_tool_calls(workspace: Path, backend: str, session_record_path: str) -> None:
    """Append one `tool.call` event per call in the retained redacted log.

    The record names that copy; the raw home file is never read here. Callers
    wrap this, and every step stays best-effort.
    """
    if not session_record_path:
        return
    artifacts = json.loads(Path(session_record_path).read_text()).get("artifacts")
    native = artifacts.get("native") if isinstance(artifacts, dict) else None
    source = native.get("path") if isinstance(native, dict) else None
    if not isinstance(source, str) or not source:
        return
    calls = extract(backend, _read_retained(Path(source)))
    if not calls:
        return
    directory = next((p for p in workspace.parents if p.parent.name == "runs"), None)
    if directory is None:
        return
    actor = _actor(directory, workspace)
    # a resumed session's log repeats its earlier calls: record each id once
    seen = {
        event["data"].get("id")
        for event in read(directory)
        if event.get("type") == "tool.call" and isinstance(event.get("data"), dict)
    }
    for call in calls:
        if call.get("id") and call["id"] in seen:
            continue
        record(directory, "tool.call", call, actor=actor)
