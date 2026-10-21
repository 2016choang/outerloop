"""The forge seam: one interface for the code host the kernel works against.

Everything the kernel does on its code host — cloning and pushing code,
reading refs and files, opening and merging pull requests, posting comments,
opening and closing issues, reading checks — goes through these verbs, so a
backend is one implementation (`docs/design/forge.md`). `GitHubClient`
satisfies the Protocol as it stands; a planned local backend will implement the
same verbs over a bare repository.

`forge_from_env` is the one factory every entry point (the tick, the attempt
and steward jobs, the ledger migration, the CLI) builds its forge through.
Selection is `OUTERLOOP_FORGE`, read like `OUTERLOOP_COMPUTE` in `compute.py`:
`github` (the default) is the only backend implemented so far; `local` is
refused with a message naming the setting.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from outerloop.markers import marker

if TYPE_CHECKING:
    from outerloop.github import TokenProvider

GITHUB = "github"
LOCAL = "local"
FORGE_ENV = "OUTERLOOP_FORGE"

# Both backends edit a pull-request body the same way: the candidate row is
# rewritten in place, and an EDIT addendum replaces the previous one. Kept
# here so the local backend's text is byte-identical to the GitHub client's.
BODY_EDIT_MARKER = marker("body-edit")
# the orchestrator-owned candidate row in pr_body's results table
# \r-tolerant: a human web-UI edit can normalize the body to CRLF
_CANDIDATE_ROW = re.compile(r"^\| candidate \| .* \|(\r?)$", re.MULTILINE)


def candidate_row_body(body: str, candidate: float, digits: int | None = None) -> str | None:
    """`body` with its first candidate row rewritten, or None when the row
    cannot be rewritten safely.

    Only the FIRST match, and only in the orchestrator's preamble (before
    the report section) — agent report text could contain a lookalike row,
    and it must stay untouched. No report heading means the preamble
    boundary is gone (human body edit?), so this fails closed rather than
    rewrite report text.
    """
    head, sep, tail = body.partition("## Research report")
    if not sep or not _CANDIDATE_ROW.search(head):
        return None
    from outerloop.progress import fmt_metric

    head = _CANDIDATE_ROW.sub(rf"| candidate | {fmt_metric(candidate, digits)} |\1", head, count=1)
    return f"{head}{sep}{tail}"


def body_with_addendum(body: str, addendum: str) -> str:
    """`body` with the EDIT addendum replaced (marker-delimited), not
    stacked: the body stays bounded and always points at the latest state.

    Strip ONLY a previous addendum of ours: marker followed by our exact
    format. Agent-authored report text could contain the marker string (it
    is public), and splitting on it blindly would truncate the frozen
    report — the history the addendum exists to preserve.
    """
    base = body
    idx = body.rfind(BODY_EDIT_MARKER)
    if idx != -1:
        tail = body[idx + len(BODY_EDIT_MARKER) :]
        if tail.lstrip().startswith("---"):
            base = body[:idx].rstrip()
    return f"{base}\n\n{BODY_EDIT_MARKER}\n{addendum}"


class Forge(Protocol):
    """The kernel's verbs on its code host, grouped by role."""

    # Whether mutating verbs only log their intent.
    dry_run: bool

    # The forge's name in user-visible text ("GitHub", "Local").
    @property
    def name(self) -> str: ...

    # Transport: where a Workspace clones from and the credential it pushes with.
    def clone_url(self, target: str) -> str: ...
    def credential(self) -> TokenProvider | None: ...
    def secrets(self) -> tuple[str, ...]: ...

    # Refs and files.
    def default_branch(self, repo: str) -> str: ...
    def branch_sha(self, repo: str, branch: str) -> str: ...
    def branch_head(self, repo: str, branch: str) -> str | None: ...
    def head_contains(self, repo: str, base: str, head: str) -> bool: ...
    def commit_tree(self, repo: str, sha: str) -> str: ...
    def get_tree(self, repo: str, sha: str) -> dict: ...
    def get_file(self, repo: str, path: str, ref: str) -> str: ...
    def get_file_content(self, repo: str, path: str, ref: str) -> str | None: ...
    def list_directory(self, repo: str, path: str, ref: str) -> list[dict[str, Any]]: ...
    def create_ref(self, repo: str, ref: str, sha: str) -> None: ...
    def ensure_branch(self, repo: str, branch: str) -> bool: ...
    def put_file(self, repo: str, path: str, content: str, branch: str, message: str) -> str: ...
    def put_files(
        self,
        repo: str,
        files: dict[str, str],
        branch: str,
        message: str,
        expected_head: str | None = None,
    ) -> bool: ...

    # Pull requests.
    def create_pull(
        self, repo: str, title: str, head: str, base: str, body: str, draft: bool = False
    ) -> str: ...
    def get_pull_request(self, repo: str, number: int) -> dict[str, Any]: ...
    def find_open_pull_for_head(
        self, repo: str, head_branch: str, base: str
    ) -> dict[str, Any] | None: ...
    def update_candidate_row(
        self, repo: str, number: int, candidate: float, digits: int | None = None
    ) -> bool: ...
    def append_pull_body(self, repo: str, number: int, addendum: str) -> None: ...
    def mark_ready_for_review(self, repo: str, number: int, expected_head: str = "") -> None: ...
    def arm_auto_merge_when_review_required(self, repo: str, number: int) -> bool: ...
    def disable_auto_merge(self, repo: str, number: int) -> bool: ...
    def allowed_merge_methods(self, repo: str) -> list[str]: ...
    def merge_pull(
        self, repo: str, number: int, method: str = "merge", expected_head: str = ""
    ) -> bool: ...
    def close_issue(self, repo: str, number: int) -> None: ...

    # Threads.
    def list_comments(
        self, repo: str, issue_number: int, max_pages: int = 20
    ) -> list[dict[str, Any]]: ...
    def comment(self, repo: str, issue_number: int, body: str) -> None: ...
    def list_pr_reviews(
        self, repo: str, number: int, max_pages: int = 10
    ) -> list[dict[str, Any]]: ...
    def list_pr_review_comments(
        self, repo: str, number: int, max_pages: int = 10
    ) -> list[dict[str, Any]]: ...

    # Issues.
    def list_open_issues(
        self, repo: str, max_pages: int = 3, creator: str = ""
    ) -> list[dict[str, Any]]: ...
    def create_issue(self, repo: str, title: str, body: str, labels: Sequence[str] = ()) -> int: ...

    # Checks.
    def list_check_runs(self, repo: str, ref: str) -> list[dict]: ...

    # References: a stable reference and its link on this forge.
    def ref_for(self, repo: str, kind: str, ident: int | str) -> tuple[str, str]: ...


def forge_name() -> str:
    """The selected backend, per `OUTERLOOP_FORGE` (default `github`). This
    helper is the only reader of the setting, so mode checks cannot drift."""
    return os.environ.get(FORGE_ENV, "").strip().lower() or GITHUB


def forge_from_env(
    pat_file: str = "",
    app_file: str = "",
    *,
    dry_run: bool = False,
    backend: str = "",
) -> Forge:
    """The deployment's forge, built from the credential paths and
    `OUTERLOOP_FORGE`. `backend` overrides the environment for callers that
    read settings from the operator's .env rather than an exported variable."""
    name = (backend or forge_name()).strip().lower()
    if name == GITHUB:
        from outerloop.appauth import resolve_bot_auth
        from outerloop.github import GitHubClient

        return GitHubClient(auth=resolve_bot_auth(pat_file, app_file), dry_run=dry_run)
    if name == LOCAL:
        raise NotImplementedError(
            f"{FORGE_ENV}=local is not implemented yet; only {FORGE_ENV}=github is supported"
        )
    raise ValueError(f"{FORGE_ENV}={name!r} is not a known forge; use 'github'")
