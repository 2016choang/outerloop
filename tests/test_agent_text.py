"""A small protocol glossary over the text solvers actually receive."""

import re
from pathlib import Path

import pytest

from outerloop.agent_text import Entry, inventory, refusal_inventory, verb_parsers

# These phrases name public actions or research data, not kernel state.
LEGITIMATE_TERMS = {
    "stages a public message": "Preparing a message is part of the public syscall protocol.",
    "stage a launch": "Preparing a request is part of the public syscall protocol.",
    "stage a job": "Preparing a request is part of the public syscall protocol.",
    "stage a submit": "Preparing a request is part of the public syscall protocol.",
    "stage `submit": "Preparing a request is part of the public syscall protocol.",
    "stage `stop": "Preparing a request is part of the public syscall protocol.",
    "stage them again": "Preparing a request is part of the public syscall protocol.",
    "stage the files": "Git staging is an agent action, not kernel state.",
    "stage them": "Git staging during a merge is an agent action.",
    "a sweep": "An experiment array is a parameter sweep, not a kernel cycle.",
    "whole sweep": "This is the experiment array's concurrency limit.",
    "record one finding": "Record is a verb here, not an internal run record.",
    "the records live on the research-log branch": "These are public benchmark results.",
}

# These refusals need policy decisions, not invented promises about recovery.
REFUSAL_EXCEPTIONS = {
    "attempt:publish:refuse:2": ("A changed measurement contract needs a new comparison policy."),
    "attempt:publish:refuse:3": ("A changed measurement contract needs a new comparison policy."),
    "attempt:publish:refuse:4": (
        "The cause may require operator intervention rather than a solver retry."
    ),
    "attempt:publish:refuse:5": ("Reopening a closed PR requires a separate policy decision."),
    "attempt:publish:refuse:6": (
        "Recovery depends on which of the pinned and current bases moved."
    ),
    "attempt:publish:refuse:7": ("The solver cannot manage the forge's auto-merge setting."),
    "attempt:publish:refuse:8": (
        "Recovery depends on whether the external head change should be retained."
    ),
    "attempt:publish:refuse:9": (
        "Missing history and forge outages require different recovery actions."
    ),
    "attempt:publish:refuse:10": (
        "An external head change needs ancestry inspection before another publish."
    ),
}

# Inline code that is deliberately not a syscall command.
CODE_DATA = {
    ".outerloop/": "Channel directory.",
    "research/agent-01": "Example branch.",
    "experiment": "Example launch handle.",
    "kernel -> you": "Inbox sender heading.",
}


def violations(entry: Entry) -> list[str]:
    verbs = verb_parsers()
    text = " ".join(entry.text.split())
    # The historical spelling must be named when upgrading an old session.
    if entry.source.startswith("note:tool-update:"):
        text = text.replace("The `end` verb is now `stop`.", "")
    errors = []
    if re.search(r"`end\b|\bend\s+--|\bend\s+(?:verb|command)\b", text, re.I):
        errors.append("obsolete end command")
    glossary = text.lower()
    for phrase in LEGITIMATE_TERMS:
        glossary = glossary.replace(phrase, "")
    if re.search(r"\b(?:legs?|leases?|ticks?|sweeps?|stages?|records?)\b", glossary):
        errors.append("kernel vocabulary")
    if entry.source.startswith("refusal:") and entry.source[8:] not in REFUSAL_EXCEPTIONS:
        cues = "|".join(
            (
                *verbs,
                "retry",
                "retrying",
                "use",
                "run",
                "wait",
                "fix",
                "write",
                "omit",
                "provide",
                "change",
            )
        )
        if not re.search(rf"\b(?:{cues})\b", text, re.I):
            errors.append("refusal has no next step")
    # Commands are inline code or explicit syscall invocations; prose and
    # fenced example payloads are not a command language.
    text = re.sub(r"```.*?```", "", text)
    for code in re.findall(r"(?<!`)`([^`]+)`(?!`)", text):
        words = code.split()
        if code in CODE_DATA or code.startswith("--") or words[0] == "git":
            continue
        verb = words[0]
        if verb == "python" and len(words) > 2 and words[1].endswith("/syscall"):
            verb = words[2]
            if verb == "<verb>":  # Help notation, not a literal command.
                continue
        if verb not in verbs:
            errors.append(f"unknown syscall: {verb}")
    for verb in re.findall(r"(?:\bpython\s+\S*/syscall\s+|\bsyscall\s+)([a-z][a-z-]*)", text):
        if verb not in verbs and verb not in {"tool", "request", "file", "keys", "only"}:
            errors.append(f"unknown syscall: {verb}")
    return errors


def test_inventory_protocol() -> None:
    entries = inventory()
    assert len({entry.source for entry in entries}) == len(entries)
    assert all(entry.text for entry in entries)
    assert {f"verb:{verb} --help" for verb in verb_parsers()} <= {e.source for e in entries}
    assert {"refusal:" + key for key in REFUSAL_EXCEPTIONS} <= {e.source for e in entries}
    failures = [f"{entry.source}: {error}" for entry in entries for error in violations(entry)]
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "source,text",
    [
        ("note:sample", "Use `end --withdraw reason`."),
        ("note:sample", "Wait for the next tick."),
        ("refusal:sample", "Invalid input."),
        ("note:sample", "Use `teleport` or python .outerloop/syscall vanish."),
    ],
)
def test_lint_detects_drift(source: str, text: str) -> None:
    assert violations(Entry(source, text))


@pytest.mark.parametrize("text", ["--minutes must be a positive integer", "at most 8 messages"])
def test_validation_category_exempts_only_next_step(text: str) -> None:
    assert not violations(Entry("validation:sample", text))
    assert violations(Entry("refusal:sample", text))
    assert violations(Entry("validation:sample", "Use `end`."))
    assert violations(Entry("validation:sample", "Wait for a tick."))
    assert violations(Entry("validation:sample", "Use `teleport`."))


def test_collector_reads_new_calls_without_evaluating_expressions(monkeypatch) -> None:
    original_read = Path.read_text

    def read_text(path, *args, **kwargs):
        if path.name == "syscall_cli.py":
            return """
def cmd_launch():
    "An unrelated docstring."
    raise ToolError(f"Wait for a tick {never_execute()!r:>10}.")
    raise ToolError("new " "constraint")
    raise ValueError("An unrelated exception.")

def _load_staged():
    raise ToolError("An input-like sentence is still a state refusal here.")
"""
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    entries = [entry for entry in refusal_inventory() if ":syscall_cli:" in entry.source]
    assert entries == [
        Entry("validation:syscall_cli:cmd_launch:ToolError:1", "Wait for a tick {}."),
        Entry("validation:syscall_cli:cmd_launch:ToolError:2", "new constraint"),
        Entry(
            "refusal:syscall_cli:_load_staged:ToolError:1",
            "An input-like sentence is still a state refusal here.",
        ),
    ]
