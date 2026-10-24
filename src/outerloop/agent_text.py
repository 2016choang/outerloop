"""Rendered protocol text for review: ``python -m outerloop.agent_text``."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

from outerloop import syscall_cli
from outerloop.brief import BriefInputs, BudgetState, Task, build_brief, render
from outerloop.inbox import Message, base_moved_text, budgets_line, render_inbox
from outerloop.orchestrator import _render_author_inbox
from outerloop.syscall import tool_update_note


@dataclass(frozen=True)
class Entry:
    source: str
    text: str


def verb_parsers() -> dict[str, argparse.ArgumentParser]:
    """Discover verbs from the same parser the installed tool uses."""
    return {
        name: parser
        for action in syscall_cli.build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
        for name, parser in action.choices.items()
    }


# Each row names a source module, scope, construct, and default category.
# Scope is empty for module-wide calls. Operator CLI errors are deliberately
# excluded: only the installed syscall CLI and messages sent to agents belong here.
REFUSAL_CONSTRUCTS = (
    ("syscall_cli", "", "ToolError", "validation"),
    ("syscall", "", "SyscallError", "validation"),
    ("syscall", "read_request", "assign:problem", "validation"),
    ("syscall", "budget_error", "return", "refusal"),
    ("syscall", "_deliver_artifacts", "return", "refusal"),
    ("attempt", "deliver_messages", "refuse", "reason"),
    ("attempt", "deliver_messages", "message", "refusal"),
    ("attempt", "_admit_working_tree", "ValueError", "refusal"),
    ("attempt", "ScopeHistoryError", "super().__init__", "refusal"),
    ("attempt", "publish", "refuse", "refusal"),
    ("orchestrator", "attempt_once", "assign:problem", "refusal"),
    ("orchestrator", "attempt_once", "assign:no_backend", "refusal"),
    ("orchestrator", "attempt_once", "keyword:problem", "refusal"),
    ("orchestrator", "attempt_once", "message", "refusal"),
)

# ToolError mixes input constraints with state/policy refusals. Within these
# functions, these call ordinals (in source order) are the state/policy checks.
# All other ToolError calls state an input constraint or an invalid input file.
CLI_REFUSALS = {
    "_load_staged": {1},
    "_check_stop": {1},
    "cmd_stop": {1, 3, 4, 5, 6},
    "cmd_message": {3},
    "cmd_reports": {1, 2},
}


def _literal_text(node: ast.AST) -> str:
    """Render literal portions only; never evaluate source expressions."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            "{}" if isinstance(value, ast.FormattedValue) else _literal_text(value)
            for value in node.values
        )
    return ""


def _strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.IfExp):
        return _strings(node.body) + _strings(node.orelse)
    if isinstance(node, ast.Tuple):
        return [text for value in node.elts for text in _strings(value)]
    text = _literal_text(node)
    return [text] if text else []


def refusal_inventory() -> list[Entry]:
    """Read inline refusals from explicit AST constructs, without running them."""
    entries = []
    trees = {
        module: ast.parse(Path(__file__).with_name(f"{module}.py").read_text(encoding="utf-8"))
        for module in dict.fromkeys(row[0] for row in REFUSAL_CONSTRUCTS)
    }
    for module, scope, construct, category in REFUSAL_CONSTRUCTS:
        tree = trees[module]
        root = next(n for n in tree.body if getattr(n, "name", None) == scope) if scope else tree
        # Associate module-wide error calls with their containing function.
        functions = {
            id(node): function.name
            for function in ast.walk(root)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(function)
        }
        counts: dict[str, int] = {}
        for node in sorted(
            ast.walk(root), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))
        ):
            value: ast.AST | None = None
            if isinstance(node, ast.Call) and ast.unparse(node.func) == construct:
                index = 1 if scope == "deliver_messages" else 0
                if len(node.args) > index:
                    value = node.args[index]
            elif (
                (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and construct == f"assign:{target.id}"
                        for target in node.targets
                    )
                )
                or (isinstance(node, ast.keyword) and construct == f"keyword:{node.arg}")
                or (isinstance(node, ast.Return) and construct == "return")
            ):
                value = node.value
            elif (
                construct == "message"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Message"
                and len(node.args) > 6
                and _literal_text(node.args[5]).split(":")[0]
                in {"message-refused", "refusal", "capacity-refusal"}
                and isinstance(node.args[6], ast.Dict)
            ):
                value = next(
                    (
                        v
                        for k, v in zip(node.args[6].keys, node.args[6].values, strict=True)
                        if k is not None and _literal_text(k) == "text"
                    ),
                    None,
                )
            if value is None:
                continue
            function = functions.get(id(node), scope)
            for text in _strings(value):
                counts[function] = counts.get(function, 0) + 1
                ordinal = counts[function]
                kind = category
                if module == "syscall_cli" and ordinal in CLI_REFUSALS.get(function, set()):
                    kind = "refusal"
                entries.append(Entry(f"{kind}:{module}:{function}:{construct}:{ordinal}", text))
    return entries


def inventory() -> list[Entry]:
    """Collect real renderers with deterministic, public example data and source literals."""
    inputs = BriefInputs(
        task=Task(
            "Compare two search methods", "search", "score, higher is better", "Report results"
        ),
        contract_text="benchmarks:\n  - name: search",
        ruler="Score over a frozen evaluation set.",
        budget=BudgetState(6.0, 4),
        lessons="The previous method plateaued.",
        recent_reports=("A previous experiment found no improvement.",),
        report_archive=True,
        syscalls=True,
        launch_budget=4,
        sleep_budget=6,
        gpu_hour_budget=6.0,
        eval_minutes_default=10,
        line_ref="research/agent-01",
        memory="Compare the two methods.",
        line_divergence="1 file changed",
        guidance="Prefer small experiments.",
    )
    entries = []
    for name, sample in (
        ("gate", inputs),
        ("self-report", replace(inputs, verification="self_report")),
        (
            "channels-off",
            replace(
                inputs,
                channels=dict.fromkeys(
                    ("messages", "siblings", "branches", "shared_reports"), False
                ),
            ),
        ),
        (
            "minimal",
            BriefInputs(task=inputs.task, contract_text=inputs.contract_text, ruler=inputs.ruler),
        ),
    ):
        entries.append(Entry(f"brief:{name}", render(build_brief(sample, "2026-01-01T00:00:00Z"))))
    entries.append(Entry("verb:syscall --help", syscall_cli.build_parser().format_help()))
    entries.extend(
        Entry(f"verb:{name} --help", parser.format_help())
        for name, parser in verb_parsers().items()
    )
    for messages, siblings, verification in product(
        (True, False), (True, False), ("gate", "self_report")
    ):
        entries.append(
            Entry(
                f"note:tool-update:messages={messages}:siblings={siblings}:{verification}",
                tool_update_note(
                    ".outerloop", messages=messages, siblings=siblings, verification=verification
                ),
            )
        )
    entries.extend(refusal_inventory())
    for messages, verification in product((True, False), ("gate", "self_report")):
        entries.append(
            Entry(
                f"wake:author:messages={messages}:{verification}",
                _render_author_inbox(
                    [],
                    budgets=budgets_line(launches=4, sleeps=6, gpu_hours=6),
                    redact_secrets=(),
                    messages_enabled=messages,
                    verification=verification,
                ),
            )
        )
    samples: list[tuple[str, str, dict]] = [
        (
            "launch-result",
            "job",
            {
                "name": "experiment",
                "exit_code": 0,
                "elapsed": 2,
                "why": "Compare methods",
                "delivered": ["results/score.json"],
                "skipped": ["missing artifact"],
                "stdout_tail": "score: 1",
                "stderr_tail": "",
            },
        ),
        ("launch-result", "job", {"name": "experiment", "slurm_state": "FAILED"}),
        ("launch-result", "job", {"name": "experiment"}),
        ("comment", "human", {"association": "MEMBER", "body": "Please compare methods."}),
        ("comment", "human", {"context_only": True, "body": "Review context."}),
        ("check-result", "ci", {"text": "Check completed", "url": "", "log_tail": "OK"}),
        (
            "panel-verdict",
            "panel",
            {
                "head": "abcdef0",
                "findings": [
                    {
                        "blocking": True,
                        "file": "solver.py",
                        "line": 1,
                        "summary": "Boundary error",
                        "detail": "Check empty input",
                    },
                    {"blocking": False, "file": "solver.py", "summary": "Add an example"},
                ],
                "transcript": "Review complete",
                "text": "Read the findings.",
            },
        ),
        (
            "gate-verdict",
            "kernel",
            {"sealed_sha": "abcdef0", "base_sha": "1234567", "text": "Measurement complete"},
        ),
        ("base-moved", "git", {"text": base_moved_text("a" * 40, "main")}),
        ("base-moved", "git", {"text": base_moved_text("a" * 40, "unusual branch")}),
        ("base-moved", "git", {"text": "Base changed. What landed:\nA change"}),
        ("head-moved", "git", {"text": "The PR head changed."}),
        ("note", "kernel", {"text": "Read the result.", "quoted_text": "Sample output"}),
        ("agent-message", "agent", {"text": "Compare results."}),
    ]
    for index, (kind, source, payload) in enumerate(samples, 1):
        message = Message(
            index,
            kind,
            source,
            "owner/repo#1",
            0,
            f"sample:{index}",
            payload,
            origin="agent-02",
            context_id="agent-01",
            to="agent-01",
            in_reply_to="missing" if index == 14 else "",
        )
        entries.append(
            Entry(
                f"inbox:{kind}:{index}",
                render_inbox(
                    [message],
                    budgets=budgets_line(launches=4, sleeps=6),
                    clock="Session time remaining: 10 minutes",
                ),
            )
        )
    return entries


def main() -> None:
    for entry in inventory():
        print(f"=== {entry.source} ===\n{entry.text}\n")


if __name__ == "__main__":
    main()
