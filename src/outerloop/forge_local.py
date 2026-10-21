"""The local backend: refs, files and transport over a bare git repository.

A deployment's forge root holds one bare repository per target at
``<root>/<owner>/<repo>.git`` (`docs/design/forge.md`, "The local backend").
This module implements the refs-and-files and transport half of the `Forge`
protocol; pull requests, threads and issues arrive in step 3 and raise
`NotImplementedError` until then.

The backend writes with Git's low-level commands on the bare repository:
blobs through ``hash-object``, trees through ``mktree`` (nested trees are
built bottom-up), commits through ``commit-tree``, and every ref move is a
compare-and-swap ``update-ref``. A commit therefore either lands whole or not at all, and a
writer that lost a race retries against the new tip exactly as it does on
GitHub.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from outerloop.github import (
    SAFE_GIT_FLAGS,
    GitError,
    GitHubError,
    git_identity,
)

log = logging.getLogger(__name__)

DEFAULT_BRANCH_CONFIG = "outerloop.defaultBranch"
DEFAULT_LOCAL_IDENTITY = "outerloop"

_TARGET_PART = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _split_target(target: str) -> tuple[str, str]:
    """`owner/repo` (an optional `.git` suffix is dropped), case-folded so a
    target has one repository path however it was spelled."""
    raw = target.strip()
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    parts = raw.split("/")
    if len(parts) != 2:
        raise ValueError(f"forge target must be owner/repo: {target!r}")
    owner, name = (part.casefold() for part in parts)
    if not _TARGET_PART.fullmatch(owner) or not _TARGET_PART.fullmatch(name):
        raise ValueError(f"forge target is not a safe repository name: {target!r}")
    return owner, name


def _zero(sha: str) -> str:
    """The "must not exist" old value for `update-ref`, as long as `sha`
    (40 hex digits in a SHA-1 repository, 64 in a SHA-256 one)."""
    return "0" * len(sha)


def local_repo_path(root: Path, target: str) -> Path:
    """The bare repository that backs `target` under a forge root."""
    owner, name = _split_target(target)
    return Path(root) / owner / f"{name}.git"


def _git(
    cwd: Path | None,
    args: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run git with host configuration and hooks neutralized. The repositories
    here are the kernel's own, but the same isolation the GitHub client uses
    keeps a stray global filter or hook from observing a write."""
    # Drop inherited GIT_* settings: GIT_DIR and friends would redirect the
    # repository past `-C`, and GIT_AUTHOR_*/GIT_COMMITTER_* would replace the
    # kernel's identity on its commits.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    command = ["git"]
    if cwd is not None:
        command += ["-C", str(cwd)]
    command += [*SAFE_GIT_FLAGS, *args]
    result = subprocess.run(command, input=input_bytes, capture_output=True, env=env, check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip()
        raise GitError(f"git {args[0]} failed: {detail}", returncode=result.returncode)
    return result


def _clean_path(path: str) -> str:
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"not a repository-relative file path: {path!r}")
    return path


def _entry_at(repo: Path, ref: str, path: str) -> tuple[str, str, str] | None:
    """(mode, type, sha) for exactly `path` at `ref`, or None. A directory
    path yields None: git lists its children, none of them named `path`. A
    missing ref is None too; any other git failure raises, so a broken or
    unreadable repository is not mistaken for an absent file."""
    found = _git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"], check=False)
    if found.returncode == 1:  # --quiet: the ref does not resolve
        return None
    if found.returncode:
        detail = found.stderr.decode(errors="replace").strip()
        raise GitError(f"git rev-parse failed: {detail}", returncode=found.returncode)
    result = _git(repo, ["ls-tree", "-z", ref, "--", path])
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        if os.fsdecode(name) != path:
            continue
        mode, kind, sha = meta.decode().split(" ")
        return mode, kind, sha
    return None


def _flat_tree(repo: Path, tree: str) -> dict[str, tuple[str, str, str]]:
    """Every entry of a tree by path (recursive), for rebuilding it."""
    result = _git(repo, ["ls-tree", "-r", "-z", "--full-tree", tree])
    entries: dict[str, tuple[str, str, str]] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, sha = meta.decode().split(" ")
        entries[os.fsdecode(name)] = (mode, kind, sha)
    return entries


def _build_tree(repo: Path, entries: dict[str, tuple[str, str, str]]) -> str:
    """Build nested trees bottom-up: `mktree` only accepts one level."""
    root: dict[str, Any] = {}
    for path, entry in entries.items():
        node = root
        for part in path.split("/")[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"path is both a file and a directory: {path!r}")
            node = child
        leaf = path.rsplit("/", 1)[-1]
        if isinstance(node.get(leaf), dict):
            raise ValueError(f"path is both a file and a directory: {path!r}")
        node[leaf] = entry

    def emit(node: dict[str, Any]) -> str:
        lines: list[bytes] = []
        for name, value in node.items():
            if isinstance(value, dict):
                lines.append(b"040000 tree " + emit(value).encode() + b"\t" + os.fsencode(name))
            else:
                mode, kind, sha = value
                lines.append(f"{mode} {kind} {sha}\t".encode() + os.fsencode(name))
        payload = b"\0".join(lines) + (b"\0" if lines else b"")
        return _git(repo, ["mktree", "-z"], input_bytes=payload).stdout.strip().decode()

    return emit(root)


class LocalForge:
    """The `Forge` verbs over bare repositories under one forge root."""

    dry_run: bool

    def __init__(
        self,
        root: Path | str,
        *,
        bot_login: str = DEFAULT_LOCAL_IDENTITY,
        dry_run: bool = False,
    ) -> None:
        if not bot_login.strip():
            raise ValueError("a local forge needs a kernel identity; git refuses an empty author")
        self.root = Path(root)
        self.bot_login = bot_login.strip()
        self.dry_run = dry_run

    # -- paths and transport -------------------------------------------------

    def repo_path(self, repo: str) -> Path:
        return local_repo_path(self.root, repo)

    def clone_url(self, target: str) -> str:
        """A session clones from the bare repository's path; no credential is
        needed and filesystem permissions are the access control."""
        return str(self.repo_path(target))

    def credential(self) -> None:
        return None

    def secrets(self) -> tuple[str, ...]:
        return ()

    def ref_for(self, repo: str, kind: str, ident: int | str) -> tuple[str, str]:
        """A stable local reference; there is no web view, so records other
        than a pull request carry no link. A pull-request reference ends in
        its number, which the kernel parses from the reference it stores."""
        path = self.repo_path(repo)
        if kind in ("pull", "issue", "issues"):
            segment = "pull" if kind == "pull" else "issue"
            return f"{repo}#{ident}", f"{path}#{segment}/{ident}"
        return f"{path}#{kind}/{ident}", ""

    # -- refs and files ------------------------------------------------------

    def default_branch(self, repo: str) -> str:
        path = self.repo_path(repo)
        configured = _git(
            path, ["config", "--get", DEFAULT_BRANCH_CONFIG], check=False
        ).stdout.strip()
        if configured:
            return configured.decode()
        symbolic = _git(path, ["symbolic-ref", "--short", "HEAD"], check=False)
        branch = symbolic.stdout.strip().decode()
        if symbolic.returncode != 0 or not branch:
            raise GitHubError(404, str(path), "default branch is not recorded")
        return branch

    def branch_sha(self, repo: str, branch: str) -> str:
        head = self.branch_head(repo, branch)
        if head is None:
            raise GitHubError(500, branch, "branch head unavailable")
        if not head:
            raise GitHubError(404, branch, "branch not found")
        return head

    def branch_head(self, repo: str, branch: str) -> str | None:
        """The branch's commit, "" when it does not exist, None on failure."""
        path = self.repo_path(repo)
        if not branch:
            return ""
        ref = f"refs/heads/{branch}"
        found = _git(path, ["show-ref", "--verify", "--quiet", ref], check=False)
        if found.returncode == 1:
            return ""
        if found.returncode != 0:
            return None
        resolved = _git(path, ["rev-parse", ref], check=False)
        sha = resolved.stdout.strip().decode()
        return sha if resolved.returncode == 0 and sha else None

    def head_contains(self, repo: str, base: str, head: str) -> bool:
        """Whether `head` contains the base tip (the reverse of the merge
        check: `git merge-base --is-ancestor base head`)."""
        result = _git(
            self.repo_path(repo), ["merge-base", "--is-ancestor", base, head], check=False
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        detail = result.stderr.decode(errors="replace").strip()
        raise GitHubError(404, f"{base}..{head}", detail or "commit not found")

    def commit_tree(self, repo: str, sha: str) -> str:
        result = _git(
            self.repo_path(repo), ["rev-parse", "--verify", f"{sha}^{{tree}}"], check=False
        )
        if result.returncode != 0:
            raise GitHubError(404, sha, "commit not found")
        return result.stdout.strip().decode()

    def get_tree(self, repo: str, sha: str) -> dict:
        path = self.repo_path(repo)
        tree_sha = self.commit_tree(repo, sha)
        result = _git(
            path, ["ls-tree", "-r", "-t", "-z", "-l", "--full-tree", tree_sha], check=False
        )
        if result.returncode != 0:
            raise GitHubError(404, sha, "tree not found")
        items: list[dict[str, Any]] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            mode, kind, object_sha, size = meta.decode().split()
            item: dict[str, Any] = {
                "path": os.fsdecode(name),
                "mode": mode,
                "type": kind,
                "sha": object_sha,
            }
            if size != "-":
                item["size"] = int(size)
            items.append(item)
        return {"sha": tree_sha, "truncated": False, "tree": items}

    def get_file(self, repo: str, path: str, ref: str) -> str:
        """A regular file's text at `ref`; raises the client's 404 when gone
        (and refuses directories, symlinks and submodules as not files)."""
        content = self._file_bytes(repo, path, ref)
        if content is None:
            raise GitHubError(404, path, "file not found")
        try:
            return content.decode()
        except UnicodeDecodeError as exc:
            raise GitHubError(200, path, "file is not UTF-8 text") from exc

    def get_file_content(self, repo: str, path: str, ref: str) -> str | None:
        """Best-effort text, None for missing, non-regular, binary or
        non-UTF-8 content (the same refusals as the GitHub client)."""
        try:
            content = self._file_bytes(repo, path, ref)
        except (GitHubError, GitError):
            return None
        if content is None or b"\x00" in content:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _file_bytes(self, repo: str, path: str, ref: str) -> bytes | None:
        entry = _entry_at(self.repo_path(repo), ref, path)
        if entry is None:
            return None
        mode, kind, sha = entry
        if kind != "blob" or mode == "120000":
            raise GitHubError(200, path, f"not a regular file (mode={mode})")
        result = _git(self.repo_path(repo), ["cat-file", "blob", sha], check=False)
        if result.returncode != 0:
            raise GitHubError(404, path, "file not found")
        return result.stdout

    def list_directory(self, repo: str, path: str, ref: str) -> list[dict[str, Any]]:
        """Direct entries of a directory at `ref`; [] when absent or a file."""
        spec = ref if path in ("", ".", "/") else f"{ref}:{path}"
        result = _git(self.repo_path(repo), ["ls-tree", "-z", "-l", spec], check=False)
        if result.returncode != 0:
            return []
        prefix = "" if path in ("", ".", "/") else path.rstrip("/") + "/"
        entries: list[dict[str, Any]] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            meta, _, raw_name = record.partition(b"\t")
            mode, kind, sha, size = meta.decode().split()
            name = os.fsdecode(raw_name)
            if kind == "tree":
                entry_type = "dir"
            elif kind == "commit":
                entry_type = "submodule"
            elif mode == "120000":
                entry_type = "symlink"
            else:
                entry_type = "file"
            entry: dict[str, Any] = {
                "name": name,
                "path": prefix + name,
                "sha": sha,
                "type": entry_type,
            }
            if size != "-":
                entry["size"] = int(size)
            entries.append(entry)
        return entries

    def create_ref(self, repo: str, ref: str, sha: str) -> None:
        """Create a ref without replacing an existing one (old value zero)."""
        if self.dry_run:
            log.info("[dry-run] create Git ref %s at %s", ref, sha[:9])
            return
        if not ref.startswith("refs/"):
            raise GitHubError(422, ref, "ref must start with refs/")
        result = _git(self.repo_path(repo), ["update-ref", ref, sha, _zero(sha)], check=False)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise GitHubError(422, ref, detail or "ref already exists")

    def ensure_branch(self, repo: str, branch: str) -> bool:
        head = self.branch_head(repo, branch)
        if head:
            return True
        if head is None:
            return False
        if self.dry_run:
            log.info("[dry-run] create branch %s on %s", branch, repo)
            return True
        try:
            base = self.default_branch(repo)
            self.create_ref(repo, f"refs/heads/{branch}", self.branch_sha(repo, base))
            return True
        except (GitHubError, GitError) as exc:
            log.warning("could not create branch %s on %s: %s", branch, repo, exc)
            return False

    def put_file(self, repo: str, path: str, content: str, branch: str, message: str) -> str:
        """Create or update one file as one commit; ``created``/``updated``,
        "" on failure (the client's shape)."""
        if self.dry_run:
            log.info("[dry-run] put %s on %s@%s", path, repo, branch)
            return "created"
        head = self.branch_head(repo, branch)
        if not head:
            return ""
        try:
            _clean_path(path)
            existed = _entry_at(self.repo_path(repo), head, path) is not None
            commit = self._commit_files(repo, {path: content}, head, message)
        except (GitHubError, GitError, ValueError) as exc:
            log.warning("could not put %s on %s@%s: %s", path, repo, branch, exc)
            return ""
        if not self._move(repo, branch, commit, head):
            return ""
        return "updated" if existed else "created"

    def put_files(
        self,
        repo: str,
        files: dict[str, str],
        branch: str,
        message: str,
        expected_head: str | None = None,
    ) -> bool:
        """Write several files as ONE compare-and-swap commit. True only when
        the ref moved; a stale `expected_head` or a lost race is False and
        changes nothing, so the caller retries the whole batch next pass."""
        if not files:
            return True
        if self.dry_run:
            log.info("[dry-run] batch put %d file(s) on %s@%s", len(files), repo, branch)
            return True
        head = self.branch_head(repo, branch)
        if not head:
            return False
        if expected_head and expected_head != head:
            log.warning(
                "batch on %s@%s refused: head moved %s -> %s (retry next pass)",
                repo,
                branch,
                expected_head[:9],
                head[:9],
            )
            return False
        try:
            for path in files:
                _clean_path(path)
            commit = self._commit_files(repo, files, head, message)
        except (GitHubError, GitError, ValueError) as exc:
            log.warning(
                "batched put of %d file(s) on %s@%s failed: %s",
                len(files),
                repo,
                branch,
                exc,
            )
            return False
        return self._move(repo, branch, commit, head)

    def _commit_files(self, repo: str, files: dict[str, str], parent: str, message: str) -> str:
        path = self.repo_path(repo)
        entries = _flat_tree(path, self.commit_tree(repo, parent))
        for file_path, content in sorted(files.items()):
            blob = (
                _git(path, ["hash-object", "-w", "--stdin"], input_bytes=content.encode())
                .stdout.strip()
                .decode()
            )
            entries[file_path] = ("100644", "blob", blob)
        tree = _build_tree(path, entries)
        identity = list(git_identity(self.bot_login))
        commit = (
            _git(path, [*identity, "commit-tree", tree, "-p", parent, "-m", message])
            .stdout.strip()
            .decode()
        )
        if not commit:
            raise GitError("commit-tree produced no commit")
        return commit

    def _move(self, repo: str, branch: str, new: str, old: str) -> bool:
        result = _git(
            self.repo_path(repo),
            ["update-ref", f"refs/heads/{branch}", new, old],
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "ref %s on %s moved under the writer: %s",
                branch,
                repo,
                result.stderr.decode(errors="replace").strip(),
            )
            return False
        return True

    # -- step 3: records, pull requests, threads and issues -----------------

    @staticmethod
    def _step_three(verb: str) -> NotImplementedError:
        return NotImplementedError(
            f"LocalForge.{verb} arrives in forge step 3 (records, pull requests, threads, issues)"
        )

    def create_pull(
        self, repo: str, title: str, head: str, base: str, body: str, draft: bool = False
    ) -> str:
        raise self._step_three("create_pull")

    def get_pull_request(self, repo: str, number: int) -> dict[str, Any]:
        raise self._step_three("get_pull_request")

    def find_open_pull_for_head(
        self, repo: str, head_branch: str, base: str
    ) -> dict[str, Any] | None:
        raise self._step_three("find_open_pull_for_head")

    def update_candidate_row(
        self, repo: str, number: int, candidate: float, digits: int | None = None
    ) -> bool:
        raise self._step_three("update_candidate_row")

    def append_pull_body(self, repo: str, number: int, addendum: str) -> None:
        raise self._step_three("append_pull_body")

    def mark_ready_for_review(self, repo: str, number: int, expected_head: str = "") -> None:
        raise self._step_three("mark_ready_for_review")

    def arm_auto_merge_when_review_required(self, repo: str, number: int) -> bool:
        raise self._step_three("arm_auto_merge_when_review_required")

    def disable_auto_merge(self, repo: str, number: int) -> bool:
        raise self._step_three("disable_auto_merge")

    def allowed_merge_methods(self, repo: str) -> list[str]:
        raise self._step_three("allowed_merge_methods")

    def merge_pull(
        self, repo: str, number: int, method: str = "merge", expected_head: str = ""
    ) -> bool:
        raise self._step_three("merge_pull")

    def close_issue(self, repo: str, number: int) -> None:
        raise self._step_three("close_issue")

    def list_comments(
        self, repo: str, issue_number: int, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        raise self._step_three("list_comments")

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        raise self._step_three("comment")

    def list_open_issues(
        self, repo: str, max_pages: int = 3, creator: str = ""
    ) -> list[dict[str, Any]]:
        raise self._step_three("list_open_issues")

    def create_issue(self, repo: str, title: str, body: str, labels: Sequence[str] = ()) -> int:
        raise self._step_three("create_issue")

    def list_check_runs(self, repo: str, ref: str) -> list[dict]:
        """Locally there are no CI checks (docs/design/forge.md)."""
        return []

    def list_pr_reviews(self, repo: str, number: int, max_pages: int = 10) -> list[dict[str, Any]]:
        """Locally there are no review rounds."""
        return []

    def list_pr_review_comments(
        self, repo: str, number: int, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Locally there are no inline review comments."""
        return []


def bootstrap_local_repo(
    root: Path | str,
    target: str,
    source: str | Path | None = None,
    *,
    bot_login: str = DEFAULT_LOCAL_IDENTITY,
) -> Path:
    """Create the bare repository for `target` under `root` and record its
    default branch.

    With a `source` (a repository path or URL) the repository is a bare clone.
    Without one it is an empty bare repository whose default branch `main`
    holds a single empty commit, so every branch-creating write has a base.
    """
    bot_login = bot_login.strip()
    if not bot_login:
        raise ValueError("a local forge needs a kernel identity; git refuses an empty author")
    dest = local_repo_path(Path(root), target)
    if dest.exists():
        raise FileExistsError(f"a forge repository already exists at {dest}")
    if source is not None and not _git(None, ["ls-remote", "--heads", str(source)]).stdout.strip():
        # a bare clone of it would have a default branch with no ref behind it
        raise ValueError(f"source repository has no commits: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    identity = list(git_identity(bot_login))
    if source is not None:
        _git(
            None,
            ["clone", "--bare", "--no-local", str(source), str(dest)],
        )
        head = _git(dest, ["symbolic-ref", "--short", "HEAD"], check=False)
        branch = head.stdout.strip().decode() or "main"
    else:
        _git(None, ["init", "--bare", "--initial-branch=main", str(dest)])
        tree = _git(dest, ["mktree", "-z"], input_bytes=b"").stdout.strip().decode()
        commit = (
            _git(dest, [*identity, "commit-tree", tree, "-m", "Initial commit"])
            .stdout.strip()
            .decode()
        )
        _git(dest, ["update-ref", "refs/heads/main", commit, _zero(commit)])
        branch = "main"
    _git(dest, ["symbolic-ref", "HEAD", f"refs/heads/{branch}"])
    _git(dest, ["config", DEFAULT_BRANCH_CONFIG, branch])
    return dest
