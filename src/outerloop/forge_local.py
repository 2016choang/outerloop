"""The local backend: refs, files, transport and records over a bare git repository.

A deployment's forge root holds one bare repository per target at
``<root>/<owner>/<repo>.git`` (`docs/design/forge.md`, "The local backend").
This module implements the refs-and-files, transport and record half of the
`Forge` protocol: pull requests, threads and the kernel's issues live as JSON
files on ``refs/outerloop/records``.

The backend writes with Git's low-level commands on the bare repository:
blobs through ``hash-object``, trees through ``mktree`` (nested trees are
built bottom-up), commits through ``commit-tree``, and every ref move is a
compare-and-swap ``update-ref``. A commit therefore either lands whole or not at all, and a
writer that lost a race retries against the new tip exactly as it does on
GitHub.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from outerloop.forge import body_with_addendum, candidate_row_body
from outerloop.github import (
    SAFE_GIT_FLAGS,
    GitError,
    GitHubError,
    git_identity,
)

log = logging.getLogger(__name__)

DEFAULT_BRANCH_CONFIG = "outerloop.defaultBranch"
DEFAULT_LOCAL_IDENTITY = "outerloop"

# Records live on their own ref in the target's bare repository: every change
# is a compare-and-swap commit, so a crash leaves the old commit or the new
# one and a mirror backs up code and records together.
RECORDS_REF = "refs/outerloop/records"
RECORDS_WRITE_ATTEMPTS = 5
COUNTERS_FILE = "counters.json"
# merge-tree --write-tree, the trial merge and merge commit path, is Git 2.38.
MIN_GIT_VERSION = (2, 38)
# A record's recent head shas: enough to recognise a merge of a branch that
# advanced (and was deleted) between the kernel's reads.
HEAD_HISTORY_LIMIT = 16

_TARGET_PART = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_GIT_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


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


def _direct_tree(repo: Path, tree: str) -> dict[str, tuple[str, str, str]]:
    """One tree's direct children by name."""
    result = _git(repo, ["ls-tree", "-z", tree])
    entries: dict[str, tuple[str, str, str]] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, kind, sha = meta.decode().split(" ")
        entries[os.fsdecode(name)] = (mode, kind, sha)
    return entries


def _build_tree(
    repo: Path, changes: dict[str, tuple[str, str, str]], base_tree: str | None = None
) -> str:
    """Build `changes` over `base_tree`, rebuilding only the directories on a
    changed path: every other entry keeps the subtree sha it already had, so
    one record write costs one `mktree` per changed level, not per directory."""
    root: dict[str, Any] = {}
    for path, entry in changes.items():
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

    def emit(node: dict[str, Any], tree: str | None, prefix: str = "") -> str:
        entries = _direct_tree(repo, tree) if tree else {}
        for name, value in node.items():
            full = prefix + name
            current = entries.get(name)
            if isinstance(value, dict):
                if current is not None and current[1] != "tree":
                    raise ValueError(f"path is both a file and a directory: {full!r}")
                child = current[2] if current is not None else None
                entries[name] = ("040000", "tree", emit(value, child, full + "/"))
            else:
                if current is not None and current[1] == "tree":
                    raise ValueError(f"path is both a file and a directory: {full!r}")
                entries[name] = value
        lines: list[bytes] = []
        for name, value in entries.items():
            mode, kind, sha = value
            lines.append(f"{mode} {kind} {sha}\t".encode() + os.fsencode(name))
        payload = b"\0".join(lines) + (b"\0" if lines else b"")
        return _git(repo, ["mktree", "-z"], input_bytes=payload).stdout.strip().decode()

    return emit(root, base_tree)


def _git_version(text: str) -> tuple[int, ...]:
    """A `git version` string's numeric release (`2.39.5 (Apple Git-154)`
    -> (2, 39, 5))."""
    match = _GIT_VERSION.search(text)
    if not match:
        raise GitError(f"could not read the Git version from {text.strip()!r}")
    return tuple(int(part or 0) for part in match.groups())


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
        found = _git_version(_git(None, ["version"]).stdout.decode(errors="replace"))
        if found < MIN_GIT_VERSION:
            minimum = ".".join(str(part) for part in MIN_GIT_VERSION)
            raise GitError(
                f"the local forge needs Git {minimum} or newer (merge-tree --write-tree); "
                f"found {'.'.join(str(part) for part in found)}"
            )
        self.root = Path(root)
        self.bot_login = bot_login.strip()
        self.dry_run = dry_run
        # mergeable_state by (repo, head, base): a re-read of the same pair
        # need not trial-merge again inside one tick.
        self._mergeable_cache: dict[tuple[str, str, str], str] = {}

    # -- paths and transport -------------------------------------------------

    @property
    def name(self) -> str:
        """The forge's name in user-visible text."""
        return "Local"

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
        """Direct entries of a directory at `ref`; [] when absent or a file.

        A ref or tree that cannot be read raises rather than reading as an
        empty directory (`_entry_at` makes the same distinction), so a
        damaged repository is not mistaken for one with nothing there."""
        bare = self.repo_path(repo)
        resolved = _git(bare, ["rev-parse", "--verify", "--quiet", f"{ref}^{{tree}}"], check=False)
        if resolved.returncode == 1:
            # --quiet folds "no such ref" and "the ref names a missing object"
            # into the same code; `cat-file -e` tells them apart (a name that
            # resolves with no object behind it is damage, not absence).
            probe = _git(bare, ["cat-file", "-e", ref], check=False)
            if probe.returncode == 1:
                raise GitError(f"ref {ref} names an object that is missing")
            if probe.returncode:
                return []  # the ref does not resolve at all
            raise GitError(f"ref {ref} does not peel to a tree")
        if resolved.returncode:
            detail = resolved.stderr.decode(errors="replace").strip()
            raise GitError(f"git rev-parse failed: {detail}", returncode=resolved.returncode)
        root = path in ("", ".", "/")
        spec = resolved.stdout.strip().decode()
        if not root:
            found_entry = _entry_at(bare, ref, path)
            if found_entry is None or found_entry[1] != "tree":
                return []  # absent, or not a directory
            spec = found_entry[2]
        result = _git(bare, ["ls-tree", "-z", "-l", spec], check=False)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise GitError(f"git ls-tree failed: {detail}", returncode=result.returncode)
        prefix = "" if root else path.rstrip("/") + "/"
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
        if not self._move(repo, f"refs/heads/{branch}", commit, head):
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
        return self._move(repo, f"refs/heads/{branch}", commit, head)

    def _commit_files(self, repo: str, files: dict[str, str], parent: str, message: str) -> str:
        """One commit carrying `files` over `parent`'s tree. An empty parent
        is the first commit on a ref: no tree to read past, no parent."""
        path = self.repo_path(repo)
        base_tree = self.commit_tree(repo, parent) if parent else None
        changes: dict[str, tuple[str, str, str]] = {}
        for file_path, content in sorted(files.items()):
            blob = (
                _git(path, ["hash-object", "-w", "--stdin"], input_bytes=content.encode())
                .stdout.strip()
                .decode()
            )
            changes[file_path] = ("100644", "blob", blob)
        tree = _build_tree(path, changes, base_tree)
        identity = list(git_identity(self.bot_login))
        args = [*identity, "commit-tree", tree]
        if parent:
            args += ["-p", parent]
        commit = _git(path, [*args, "-m", message]).stdout.strip().decode()
        if not commit:
            raise GitError("commit-tree produced no commit")
        return commit

    def _move(self, repo: str, ref: str, new: str, old: str) -> bool:
        """Compare-and-swap `ref` from `old` to `new`."""
        result = _git(
            self.repo_path(repo),
            ["update-ref", ref, new, old],
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "ref %s on %s moved under the writer: %s",
                ref,
                repo,
                result.stderr.decode(errors="replace").strip(),
            )
            return False
        return True

    # -- records -------------------------------------------------------------
    #
    # `pulls/<n>.json`, `issues/<n>.json` and `threads/<n>/<id>.json` on
    # `refs/outerloop/records`; `counters.json` holds the shared issue/PR
    # number and the last comment id. Every write is one compare-and-swap
    # commit, retried on the new tip when another writer wins.

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @staticmethod
    def _pull_path(number: int) -> str:
        return f"pulls/{number}.json"

    @staticmethod
    def _issue_path(number: int) -> str:
        return f"issues/{number}.json"

    @staticmethod
    def _comment_path(number: int, comment_id: int) -> str:
        return f"threads/{number}/{comment_id}.json"

    def _ref_head(self, repo: str, ref: str) -> str:
        """The ref's commit, or "" when the ref does not exist."""
        path = self.repo_path(repo)
        found = _git(path, ["show-ref", "--verify", "--quiet", ref], check=False)
        if found.returncode == 1:
            return ""
        if found.returncode:
            detail = found.stderr.decode(errors="replace").strip()
            raise GitError(f"git show-ref failed: {detail}", returncode=found.returncode)
        resolved = _git(path, ["rev-parse", "--verify", ref], check=False)
        sha = resolved.stdout.strip().decode()
        if resolved.returncode != 0 or not sha:
            detail = resolved.stderr.decode(errors="replace").strip()
            raise GitError(f"git rev-parse failed: {detail}", returncode=resolved.returncode)
        return sha

    def _tree_at(self, repo: str, ref: str) -> str:
        """The tree `ref` names; a broken ref raises rather than reading as
        an empty tree."""
        path = self.repo_path(repo)
        resolved = _git(path, ["rev-parse", "--verify", f"{ref}^{{tree}}"], check=False)
        if resolved.returncode != 0:
            detail = resolved.stderr.decode(errors="replace").strip()
            raise GitError(f"git rev-parse failed: {detail}", returncode=resolved.returncode)
        return resolved.stdout.strip().decode()

    def _blobs(self, repo: str, shas: dict[str, str]) -> dict[str, str]:
        """The text of several blobs in ONE `cat-file --batch`; a missing
        object or non-UTF-8 content raises."""
        if not shas:
            return {}
        path = self.repo_path(repo)
        items = list(shas.items())
        payload = b"".join(sha.encode() + b"\n" for _name, sha in items)
        result = _git(path, ["cat-file", "--batch"], input_bytes=payload, check=False)
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip()
            raise GitError(f"git cat-file failed: {detail}", returncode=result.returncode)
        out = result.stdout
        pos = 0
        files: dict[str, str] = {}
        for name, _sha in items:
            end = out.find(b"\n", pos)
            if end < 0:
                raise GitError(f"git cat-file returned no header for record {name}")
            header = out[pos:end].decode(errors="replace").split()
            pos = end + 1
            if len(header) < 3 or header[1] != "blob":
                raise GitError(f"git cat-file could not read record {name}")
            size = int(header[2])
            content = out[pos : pos + size]
            pos += size + 1  # the newline cat-file appends after each object
            try:
                files[name] = content.decode()
            except UnicodeDecodeError as exc:
                raise GitError(f"record {name} is not UTF-8 text") from exc
        return files

    def _files_for(self, repo: str, ref: str, paths: Sequence[str]) -> dict[str, str]:
        """Only the named files in `ref`'s tree as text; {} for a missing ref
        or an absent path. A broken ref or unreadable blob raises rather than
        reading as an empty tree. A record write names the records its
        `mutate` reads, so it never loads every record in the tree."""
        if not ref or not paths:
            return {}
        wanted = set(paths)
        path = self.repo_path(repo)
        tree = self._tree_at(repo, ref)
        found = _git(path, ["ls-tree", "-z", tree, "--", *sorted(wanted)])
        shas: dict[str, str] = {}
        for record in found.stdout.split(b"\0"):
            if not record:
                continue
            meta, _, raw_name = record.partition(b"\t")
            name = os.fsdecode(raw_name)
            if name not in wanted:
                continue
            _mode, kind, sha = meta.decode().split(" ")
            if kind == "blob":
                shas[name] = sha
        return self._blobs(repo, shas)

    def _blob_at(self, repo: str, ref: str, path: str) -> str | None:
        """One record's text at exactly `path`, or None when absent."""
        entry = _entry_at(self.repo_path(repo), ref, path)
        if entry is None or entry[1] != "blob":
            return None
        return self._blobs(repo, {path: entry[2]})[path]

    def _json_records(self, repo: str, ref: str, directory: str) -> dict[str, dict[str, Any]]:
        """The direct `*.json` records under `directory` at `ref`, read with
        one `cat-file --batch`; {} when the ref or the directory is absent."""
        if not ref:
            return {}
        path = self.repo_path(repo)
        tree = self._tree_at(repo, ref)
        args = ["ls-tree", "-z", "-r", tree]
        if directory:
            args += ["--", directory.rstrip("/")]
        result = _git(path, args)
        prefix = directory.rstrip("/") + "/" if directory else ""
        shas: dict[str, str] = {}
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            meta, _, raw_name = record.partition(b"\t")
            name = os.fsdecode(raw_name)
            relative = name[len(prefix) :] if prefix else name
            if prefix and not name.startswith(prefix):
                continue
            if "/" in relative or not relative.endswith(".json"):
                continue
            _mode, kind, sha = meta.decode().split(" ")
            if kind == "blob":
                shas[name] = sha
        return {
            name: self._decode_json(name, text) for name, text in self._blobs(repo, shas).items()
        }

    def _read_json(self, files: dict[str, str], path: str) -> dict[str, Any] | None:
        text = files.get(path)
        return None if text is None else self._decode_json(path, text)

    @staticmethod
    def _decode_json(path: str, text: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise GitHubError(500, path, "record is not valid JSON") from exc
        if not isinstance(data, dict):
            raise GitHubError(500, path, "record is not a JSON object")
        return data

    def _update_records(
        self,
        repo: str,
        message: str,
        mutate: Callable[[dict[str, str]], dict[str, str] | None],
        paths: Sequence[str],
    ) -> None:
        """Apply `mutate` to the records as ONE compare-and-swap commit.

        `paths` names every record `mutate` reads (and `counters.json` when it
        allocates a number or a comment id); only those blobs are read, never
        the whole tree. `mutate` receives the current files and returns the
        files to write (or None for no change). A lost race re-reads the new
        tip and runs `mutate` again, a few times, before failing: no writer's
        change is silently dropped."""
        if self.dry_run:
            return
        for _ in range(RECORDS_WRITE_ATTEMPTS):
            head = self._ref_head(repo, RECORDS_REF)
            changes = mutate(self._files_for(repo, head, paths))
            if changes is None:
                return
            commit = self._commit_files(repo, changes, head, message)
            if self._move(repo, RECORDS_REF, commit, head or _zero(commit)):
                return
            log.info("records on %s moved under the writer; retrying", repo)
        raise GitHubError(
            409, RECORDS_REF, f"records moved under the writer {RECORDS_WRITE_ATTEMPTS} times"
        )

    def _read_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        head = self._ref_head(repo, RECORDS_REF)
        if not head:
            return None
        path = self._pull_path(number)
        text = self._blob_at(repo, head, path)
        return None if text is None else self._decode_json(path, text)

    def _counters(self, files: dict[str, str]) -> dict[str, int]:
        data = self._read_json(files, COUNTERS_FILE) or {}
        return {
            key: value
            for key in ("number", "comment")
            if isinstance((value := data.get(key)), int) and not isinstance(value, bool)
        }

    def _branch_ref(self, repo: str, branch: str) -> str:
        """Validate `branch` and return its full ref name. Records steer the
        merge transaction, so a branch name is checked before it can reach
        `update-ref --stdin`."""
        result = _git(
            self.repo_path(repo), ["check-ref-format", f"refs/heads/{branch}"], check=False
        )
        if not branch or result.returncode != 0:
            raise GitHubError(422, branch, "invalid branch name")
        return f"refs/heads/{branch}"

    @staticmethod
    def _remember_head(record: dict[str, Any], sha: str) -> None:
        """Stamp `sha` as the head and keep it in a short history, so a merge
        of a branch that later advanced (or was deleted) stays recognisable
        from a sha the record has seen."""
        if not sha:
            return
        record.setdefault("head", {})["sha"] = sha
        history = record.get("head_history")
        seen = (
            [item for item in history if isinstance(item, str)] if isinstance(history, list) else []
        )
        if sha in seen:
            seen.remove(sha)
        seen.append(sha)
        record["head_history"] = seen[-HEAD_HISTORY_LIMIT:]

    def _stamp_head(self, repo: str, record: dict[str, Any]) -> None:
        """Keep the record's last-seen head sha current when the record is
        being written anyway; an open record's returned shape still reads the
        live ref. A merged record keeps the head the merge landed."""
        if record.get("merged"):
            return
        head_ref = str((record.get("head") or {}).get("ref") or "")
        live = self.branch_head(repo, head_ref) if head_ref else ""
        if live:
            self._remember_head(record, live)

    def _mergeable_state(self, repo: str, head: str, base: str) -> str:
        """`clean` when the head contains the base tip; `behind` when it does
        not but a trial merge succeeds; `dirty` only on conflict."""
        if not head or not base:
            return "unknown"
        key = (repo, head, base)
        if key in self._mergeable_cache:
            return self._mergeable_cache[key]
        if self.head_contains(repo, base, head):
            state = "clean"
        else:
            trial = _git(
                self.repo_path(repo), ["merge-tree", "--write-tree", base, head], check=False
            )
            if trial.returncode == 0:
                state = "behind"
            elif trial.returncode == 1:
                state = "dirty"
            else:
                detail = trial.stderr.decode(errors="replace").strip()
                raise GitError(f"git merge-tree failed: {detail}", returncode=trial.returncode)
        self._mergeable_cache[key] = state
        return state

    def _pull_shape(self, repo: str, record: dict[str, Any]) -> dict[str, Any]:
        """The record in the shape callers read. An open record shows the live
        head sha (a follow-up push is seen at once); a merged record keeps the
        head the merge landed, not wherever the branch went afterwards."""
        number = int(record.get("number") or 0)
        head_ref = str((record.get("head") or {}).get("ref") or "")
        base_ref = str((record.get("base") or {}).get("ref") or "")
        merged = bool(record.get("merged"))
        stored_head = str((record.get("head") or {}).get("sha") or "")
        if merged:
            head_sha = stored_head
        else:
            live_head = self.branch_head(repo, head_ref) if head_ref else ""
            head_sha = live_head or stored_head
        open_ = str(record.get("state") or "open") == "open" and not merged
        base_tip = self.branch_head(repo, base_ref) if base_ref and open_ else ""
        _, url = self.ref_for(repo, "pull", number)
        return {
            "number": number,
            "state": str(record.get("state") or "open"),
            "merged": merged,
            "merged_at": record.get("merged_at"),
            "merge_commit_sha": record.get("merge_commit_sha"),
            "draft": bool(record.get("draft")),
            "user": dict(record.get("user") or {}),
            "title": str(record.get("title") or ""),
            "body": str(record.get("body") or ""),
            "html_url": url,
            "labels": list(record.get("labels") or []),
            "head": {"sha": head_sha, "ref": head_ref},
            "base": {"ref": base_ref},
            "auto_merge": None,
            "mergeable_state": (
                self._mergeable_state(repo, head_sha, str(base_tip or "")) if open_ else "unknown"
            ),
        }

    def _with_merge(self, record: dict[str, Any], sha: str) -> dict[str, Any]:
        return {
            **record,
            "merged": True,
            "merged_at": self._now(),
            "merge_commit_sha": sha,
            "state": "closed",
        }

    def _seen_heads(self, repo: str, record: dict[str, Any]) -> list[str]:
        """Head shas the record has seen, newest first: the live branch, the
        stored head, then the history of shas stamped on earlier writes."""
        head_ref = str((record.get("head") or {}).get("ref") or "")
        candidates = [self.branch_head(repo, head_ref) if head_ref else ""]
        candidates.append(str((record.get("head") or {}).get("sha") or ""))
        history = record.get("head_history")
        if isinstance(history, list):
            candidates.extend(reversed([item for item in history if isinstance(item, str)]))
        seen: list[str] = []
        for sha in candidates:
            if sha and sha not in seen:
                seen.append(sha)
        return seen

    def _descends_from(self, repo: str, ancestor: str, commit: str) -> bool:
        try:
            return self.head_contains(repo, ancestor, commit)
        except GitHubError as exc:
            if exc.status == 404:  # a gone object is simply not it
                return False
            raise

    def _detect_merge(self, repo: str, record: dict[str, Any]) -> tuple[str, str] | None:
        """(merge commit, merged head) when the base carries evidence that
        this head landed, None while the pull request is open.

        A person may merge with plain git, or a process may die between the
        ref move and the record write. On the base's FIRST-PARENT chain:
          - a merge commit whose second parent is, or descends from, a seen
            head landed that head — the merged head is the second parent;
          - a seen head that is itself on the chain was a fast-forward — the
            merged head is that commit.
        The EARLIEST landing wins: a later merge may bring in a branch built
        on top of an already-landed head, and that later merge is not when
        this pull request landed.
        A head the base merely contains some other way (squash, rebase, an
        unrelated history) is not evidence: the record stays open."""
        head_ref = str((record.get("head") or {}).get("ref") or "")
        base_ref = str((record.get("base") or {}).get("ref") or "")
        if not head_ref or not base_ref or head_ref == base_ref:
            return None
        seen = self._seen_heads(repo, record)
        if not seen:
            return None
        base_tip = self.branch_head(repo, base_ref)
        if not base_tip:
            return None
        # No first-parent evidence can exist unless the base contains a seen
        # head; this keeps an ordinary open pull request from walking the
        # whole history on every read.
        contained = [head for head in seen if self._descends_from(repo, head, base_tip)]
        if not contained:
            return None

        def first_parent_chain() -> list[tuple[str, list[str]]]:
            result = _git(
                self.repo_path(repo),
                ["rev-list", "--first-parent", "--reverse", "--parents", base_tip],
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.decode(errors="replace").strip()
                raise GitError(f"git rev-list failed: {detail}", returncode=result.returncode)
            return [
                (fields[0], fields[1:])
                for line in result.stdout.decode().splitlines()
                if (fields := line.split())
            ]

        chain = first_parent_chain()
        position = {commit: index for index, (commit, _parents) in enumerate(chain)}
        best: tuple[int, str, str] | None = None
        for head in contained:
            landing: tuple[int, str, str] | None = None
            if head in position:
                # A fast-forward: the head itself is on the chain.
                landing = (position[head], head, head)
            else:
                # Oldest first, only the commits after `head`: the first merge
                # whose second parent is or descends from `head` landed it.
                result = _git(
                    self.repo_path(repo),
                    [
                        "rev-list",
                        "--first-parent",
                        "--reverse",
                        "--parents",
                        base_tip,
                        "--not",
                        head,
                    ],
                    check=False,
                )
                if result.returncode != 0:
                    detail = result.stderr.decode(errors="replace").strip()
                    raise GitError(f"git rev-list failed: {detail}", returncode=result.returncode)
                for line in result.stdout.decode().splitlines():
                    fields = line.split()
                    if len(fields) < 3:
                        continue
                    commit, second = fields[0], fields[2]
                    if second == head or self._descends_from(repo, head, second):
                        landing = (position.get(commit, len(chain)), commit, second)
                        break
            if landing is not None and (best is None or landing[0] < best[0]):
                best = landing
        return None if best is None else (best[1], best[2])

    def _record_merge(self, repo: str, number: int, sha: str, head_sha: str = "") -> None:
        def mutate(files: dict[str, str]) -> dict[str, str] | None:
            record = self._read_json(files, self._pull_path(number))
            # A concurrent close must not hide a merge the kernel landed;
            # only an already-merged record is left alone.
            if record is None or record.get("merged"):
                return None
            if head_sha:
                self._remember_head(record, head_sha)
            return {self._pull_path(number): self._dump(self._with_merge(record, sha))}

        self._update_records(repo, f"records: merge #{number}", mutate, [self._pull_path(number)])

    # -- pull requests -------------------------------------------------------

    def create_pull(
        self, repo: str, title: str, head: str, base: str, body: str, draft: bool = False
    ) -> str:
        """Open a pull request; returns its local reference. The head branch
        must exist, as on GitHub (publish pushes it first)."""
        self._branch_ref(repo, head)
        self._branch_ref(repo, base)
        head_sha = self.branch_head(repo, head)
        if head_sha is None:
            raise GitHubError(500, head, "branch head unavailable")
        if not head_sha:
            raise GitHubError(404, head, "branch not found")
        base_sha = self.branch_head(repo, base)
        if base_sha is None:
            raise GitHubError(500, base, "branch head unavailable")
        if not base_sha:
            raise GitHubError(422, base, "base branch not found")
        if self.head_contains(repo, head_sha, base_sha):
            # GitHub refuses this with 422 "No commits between ..."; a pull
            # request with nothing to merge can never be recorded as merged.
            raise GitHubError(422, head, f"No commits between {base} and {head}")
        if self.dry_run:
            log.info("[dry-run] PR on %s: %s (%s -> %s)", repo, title, head, base)
            return self.ref_for(repo, "pull", "dry-run")[1]
        created: dict[str, int] = {}

        def mutate(files: dict[str, str]) -> dict[str, str]:
            counters = self._counters(files)
            number = counters.get("number", 0) + 1
            counters["number"] = number
            created["number"] = number
            record = {
                "number": number,
                "state": "open",
                "merged": False,
                "merged_at": None,
                "merge_commit_sha": None,
                "draft": bool(draft),
                "user": {"login": self.bot_login},
                "author_association": "NONE",
                "title": title,
                "body": body,
                "labels": [],
                "head": {"ref": head, "sha": head_sha},
                "head_history": [head_sha],
                "base": {"ref": base},
                "created_at": self._now(),
            }
            return {
                COUNTERS_FILE: self._dump(counters),
                self._pull_path(number): self._dump(record),
            }

        self._update_records(repo, "records: open pull request", mutate, [COUNTERS_FILE])
        return self.ref_for(repo, "pull", created["number"])[1]

    def get_pull_request(self, repo: str, number: int) -> dict[str, Any]:
        """The record with live head/base refs; a merge made outside the
        kernel is recorded here once, on first read."""
        record = self._read_pull(repo, number)
        if record is None:
            raise GitHubError(404, self._pull_path(number), "pull request not found")
        if str(record.get("state") or "open") == "open" and not record.get("merged"):
            detected = self._detect_merge(repo, record)
            if detected:
                merge_sha, merged_head = detected
                if self.dry_run:
                    # Same shape as the write path: the head the merge landed,
                    # never a branch pushed after it.
                    self._remember_head(record, merged_head)
                    record = self._with_merge(record, merge_sha)
                else:
                    # Stamp the head the merge landed, never the live branch:
                    # a push after the merge is not part of what landed.
                    self._record_merge(repo, number, merge_sha, merged_head)
                    record = self._read_pull(repo, number) or self._with_merge(record, merge_sha)
        return self._pull_shape(repo, record)

    def find_open_pull_for_head(
        self, repo: str, head_branch: str, base: str
    ) -> dict[str, Any] | None:
        head = self._ref_head(repo, RECORDS_REF)
        if not head:
            return None
        matches = [
            int(record.get("number") or 0)
            for record in self._json_records(repo, head, "pulls").values()
            if str(record.get("state") or "open") == "open"
            and not record.get("merged")
            and str((record.get("head") or {}).get("ref") or "") == head_branch
            and str((record.get("base") or {}).get("ref") or "") == base
        ]
        # A read can just have recorded an outside merge; skip that pull
        # request and keep looking rather than return a closed one.
        for number in sorted(matches):
            pull = self.get_pull_request(repo, number)
            if pull.get("state") == "open" and not pull.get("merged"):
                return pull
        return None

    def list_pulls(self, repo: str) -> list[dict[str, Any]]:
        """Every pull-request record, open, merged or closed, in number order.

        Read through `get_pull_request` so a merge made outside the kernel is
        recorded on first sight, exactly as a run's own read does."""
        head = self._ref_head(repo, RECORDS_REF)
        if not head:
            return []
        numbers = sorted(
            int(record.get("number") or 0)
            for record in self._json_records(repo, head, "pulls").values()
        )
        return [self.get_pull_request(repo, number) for number in numbers if number]

    def update_candidate_row(
        self, repo: str, number: int, candidate: float, digits: int | None = None
    ) -> bool:
        """Rewrite the results table's candidate row in place (shared text
        transform; this method is only the read and the CAS write)."""
        if self.dry_run:
            log.info("[dry-run] update candidate row %s#%d -> %s", repo, number, candidate)
            return True
        outcome: dict[str, bool] = {}

        def mutate(files: dict[str, str]) -> dict[str, str] | None:
            record = self._read_json(files, self._pull_path(number))
            if record is None:
                raise GitHubError(404, self._pull_path(number), "pull request not found")
            updated = candidate_row_body(str(record.get("body") or ""), candidate, digits)
            if updated is None:
                outcome["rewritten"] = False
                return None
            record["body"] = updated
            self._stamp_head(repo, record)
            outcome["rewritten"] = True
            return {self._pull_path(number): self._dump(record)}

        self._update_records(
            repo, f"records: candidate row on #{number}", mutate, [self._pull_path(number)]
        )
        return outcome.get("rewritten", True)

    def append_pull_body(self, repo: str, number: int, addendum: str) -> None:
        """Upsert the EDIT addendum onto the pull-request body (shared text
        transform; this method is only the read and the CAS write)."""
        if self.dry_run:
            log.info(
                "[dry-run] upsert pull body edit %s#%d (%d chars)", repo, number, len(addendum)
            )
            return

        def mutate(files: dict[str, str]) -> dict[str, str]:
            record = self._read_json(files, self._pull_path(number))
            if record is None:
                raise GitHubError(404, self._pull_path(number), "pull request not found")
            record["body"] = body_with_addendum(str(record.get("body") or ""), addendum)
            self._stamp_head(repo, record)
            return {self._pull_path(number): self._dump(record)}

        self._update_records(repo, f"records: edit #{number}", mutate, [self._pull_path(number)])

    def mark_ready_for_review(self, repo: str, number: int, expected_head: str = "") -> None:
        """Mark a draft ready, honoring `expected_head` like the client: a
        head that moved since the panel read is left a draft."""
        if self.dry_run:
            log.info("[dry-run] mark PR ready for review %s#%d", repo, number)
            return
        pr = self.get_pull_request(repo, number)
        head = str((pr.get("head") or {}).get("sha") or "")
        if expected_head and head != expected_head:
            log.info("not marking %s#%d ready: head moved since the panel read", repo, number)
            return

        def mutate(files: dict[str, str]) -> dict[str, str] | None:
            record = self._read_json(files, self._pull_path(number))
            if record is None:
                raise GitHubError(404, self._pull_path(number), "pull request not found")
            # The panel read happened before the compare-and-swap: a merge or
            # close that landed since must not be reopened or have its frozen
            # head overwritten.
            if record.get("merged") or str(record.get("state") or "open") != "open":
                return None
            head_ref = str((record.get("head") or {}).get("ref") or "")
            live = self.branch_head(repo, head_ref) if head_ref else ""
            if expected_head and live and live != expected_head:
                return None
            if not record.get("draft"):
                return None
            record["draft"] = False
            if live:
                self._remember_head(record, live)
            return {self._pull_path(number): self._dump(record)}

        self._update_records(
            repo, f"records: ready for review #{number}", mutate, [self._pull_path(number)]
        )

    def arm_auto_merge_when_review_required(self, repo: str, number: int) -> bool:
        """Nothing is armed locally; the auto dial merges through merge_pull."""
        return False

    def disable_auto_merge(self, repo: str, number: int) -> bool:
        """Nothing is ever armed, so auto-merge is always confirmed disarmed."""
        return True

    def allowed_merge_methods(self, repo: str) -> list[str]:
        return ["MERGE"]

    def _merge_commit(
        self, repo: str, tree: str, base_tip: str, head_sha: str, message: str
    ) -> str:
        result = _git(
            self.repo_path(repo),
            [
                *git_identity(self.bot_login),
                "commit-tree",
                tree,
                "-p",
                base_tip,
                "-p",
                head_sha,
                "-m",
                message,
            ],
            check=False,
        )
        commit = result.stdout.strip().decode()
        if result.returncode != 0 or not commit:
            detail = result.stderr.decode(errors="replace").strip()
            raise GitError(f"git commit-tree failed: {detail}", returncode=result.returncode)
        return commit

    def _land_merge(
        self,
        repo: str,
        head_ref: str,
        head_sha: str,
        base_ref: str,
        base_tip: str,
        merge_sha: str,
    ) -> bool:
        """Move the base in ONE transaction: the head must still point at the
        expected sha and the base at the tip this commit was built on, or
        nothing changes at all."""
        head = self._branch_ref(repo, head_ref)
        base = self._branch_ref(repo, base_ref)
        payload = (
            b"start\n"
            + f"verify {head} {head_sha}\n".encode()
            + f"update {base} {merge_sha} {base_tip}\n".encode()
            + b"prepare\ncommit\n"
        )
        result = _git(
            self.repo_path(repo), ["update-ref", "--stdin"], input_bytes=payload, check=False
        )
        if result.returncode != 0:
            log.warning(
                "merge of %s into %s refused: %s",
                head_ref,
                base_ref,
                result.stderr.decode(errors="replace").strip(),
            )
            return False
        return True

    def merge_pull(
        self, repo: str, number: int, method: str = "merge", expected_head: str = ""
    ) -> bool:
        """Directly merge a clean pull request as a merge commit (the only
        method the local forge offers). `expected_head` guarantees the head
        did not move; a lost race moves nothing."""
        if self.dry_run:
            log.info("[dry-run] merge %s#%s", repo, number)
            return True
        if str(method).strip().lower() != "merge":
            log.warning("local forge merges with merge commits only; refusing %r", method)
            return False
        try:
            record = self._read_pull(repo, number)
            if record is None:
                log.warning("merge of %s#%s failed: pull request not found", repo, number)
                return False
            if record.get("merged") or str(record.get("state") or "open") != "open":
                log.warning("merge of %s#%s failed: pull request is not open", repo, number)
                return False
            head_ref = str((record.get("head") or {}).get("ref") or "")
            base_ref = str((record.get("base") or {}).get("ref") or "")
            if not head_ref or not base_ref:
                return False
            head_sha = self.branch_head(repo, head_ref)
            base_tip = self.branch_head(repo, base_ref)
            if not head_sha or not base_tip:
                log.warning("merge of %s#%s failed: branch head unavailable", repo, number)
                return False
            wanted = expected_head or head_sha
            if wanted != head_sha:
                log.warning(
                    "merge of %s#%s refused: head moved %s -> %s",
                    repo,
                    number,
                    wanted[:9],
                    head_sha[:9],
                )
                return False
            if self._descends_from(repo, wanted, base_tip):
                # merged outside the kernel after the read: land nothing (no
                # second merge commit); the next read records the outside merge
                log.warning("merge of %s#%s skipped: the base already contains it", repo, number)
                return False
            if self.head_contains(repo, base_tip, wanted):
                tree = self.commit_tree(repo, wanted)
            else:
                # the base moved ahead: merge it in, or refuse a conflict
                trial = _git(
                    self.repo_path(repo),
                    ["merge-tree", "--write-tree", base_tip, wanted],
                    check=False,
                )
                if trial.returncode == 1:
                    log.warning("merge of %s#%s refused: the trial merge conflicts", repo, number)
                    return False
                tree = trial.stdout.split(b"\n", 1)[0].strip().decode()
                if trial.returncode != 0 or not tree:
                    detail = trial.stderr.decode(errors="replace").strip()
                    raise GitError(f"git merge-tree failed: {detail}", returncode=trial.returncode)
            message = f"Merge pull request #{number} from {head_ref}\n\n{record.get('title') or ''}"
            merge_sha = self._merge_commit(repo, tree, base_tip, wanted, message)
            if not self._land_merge(repo, head_ref, wanted, base_ref, base_tip, merge_sha):
                return False
        except (GitHubError, GitError) as exc:
            log.warning("merge of %s#%s failed: %s", repo, number, exc)
            return False
        try:
            self._record_merge(repo, number, merge_sha, wanted)
        except (GitHubError, GitError) as exc:
            # the ref moved; the next read recovers the merge from the base
            log.warning("merged %s#%s but could not record it: %s", repo, number, exc)
        return True

    def close_issue(self, repo: str, number: int) -> None:
        """Close a pull request or an issue; the state is always written
        explicitly (`close_if_done` reads it to end a rejected run)."""
        if self.dry_run:
            log.info("[dry-run] close issue %s#%s", repo, number)
            return

        def mutate(files: dict[str, str]) -> dict[str, str] | None:
            for path in (self._pull_path(number), self._issue_path(number)):
                record = self._read_json(files, path)
                if record is None:
                    continue
                if record.get("state") == "closed":
                    return None
                record["state"] = "closed"
                return {path: self._dump(record)}
            raise GitHubError(404, str(number), "issue or pull request not found")

        self._update_records(
            repo,
            f"records: close #{number}",
            mutate,
            [self._pull_path(number), self._issue_path(number)],
        )

    # -- threads -------------------------------------------------------------

    def list_comments(
        self, repo: str, issue_number: int, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        head = self._ref_head(repo, RECORDS_REF)
        if not head:
            return []
        comments = self._json_records(repo, head, f"threads/{issue_number}")
        ordered = sorted(comments.values(), key=lambda c: int(c.get("id") or 0))
        return ordered[: max_pages * 100]  # GitHub's cap: max_pages pages of 100, oldest first

    def comment(self, repo: str, issue_number: int, body: str) -> None:
        """Append one kernel comment to an issue's or pull request's thread."""
        self._append_comment(repo, issue_number, body, self.bot_login, "NONE")

    def comment_as(
        self,
        repo: str,
        issue_number: int,
        body: str,
        *,
        author: str,
        association: str = "OWNER",
    ) -> None:
        """Append a comment written by a person (`outerloop pr comment`).

        Records carry the author's standing, so the inbox delivers it to the
        author as a person's comment: locally only people with write access to
        the forge directory can reach this path."""
        author = author.strip()
        if not author:
            raise ValueError("a person's comment needs an author")
        self._append_comment(repo, issue_number, body, author, association)

    def _append_comment(
        self, repo: str, issue_number: int, body: str, author: str, association: str
    ) -> None:
        if self.dry_run:
            log.info("[dry-run] comment on %s#%s (%d chars)", repo, issue_number, len(body))
            return

        def mutate(files: dict[str, str]) -> dict[str, str]:
            if not any(
                self._read_json(files, path)
                for path in (self._pull_path(issue_number), self._issue_path(issue_number))
            ):
                raise GitHubError(404, str(issue_number), "issue or pull request not found")
            counters = self._counters(files)
            comment_id = counters.get("comment", 0) + 1
            counters["comment"] = comment_id
            record = {
                "id": comment_id,
                "user": {"login": author},
                "body": body,
                "author_association": association,
                "created_at": self._now(),
            }
            return {
                COUNTERS_FILE: self._dump(counters),
                self._comment_path(issue_number, comment_id): self._dump(record),
            }

        self._update_records(
            repo,
            f"records: comment on #{issue_number}",
            mutate,
            [COUNTERS_FILE, self._pull_path(issue_number), self._issue_path(issue_number)],
        )

    # -- issues --------------------------------------------------------------

    def list_open_issues(
        self, repo: str, max_pages: int = 3, creator: str = ""
    ) -> list[dict[str, Any]]:
        head = self._ref_head(repo, RECORDS_REF)
        if not head:
            return []
        wanted = creator.strip().casefold()
        issues = []
        for record in self._json_records(repo, head, "issues").values():
            if record.get("state") != "open":
                continue
            login = str((record.get("user") or {}).get("login") or "")
            if wanted and login.casefold() != wanted:
                continue
            issues.append(record)
        # GitHub's page cap, in its order: open issues oldest first.
        return sorted(issues, key=lambda i: int(i.get("number") or 0))[: max_pages * 100]

    def create_issue(self, repo: str, title: str, body: str, labels: Sequence[str] = ()) -> int:
        """Open an issue on the shared number sequence; returns its number."""
        if self.dry_run:
            log.info("[dry-run] issue on %s: %s", repo, title)
            return 0
        created: dict[str, int] = {}

        def mutate(files: dict[str, str]) -> dict[str, str]:
            counters = self._counters(files)
            number = counters.get("number", 0) + 1
            counters["number"] = number
            created["number"] = number
            record = {
                "number": number,
                "state": "open",
                "title": title,
                "body": body,
                "user": {"login": self.bot_login},
                "author_association": "NONE",
                "labels": [{"name": str(label)} for label in labels],
                "created_at": self._now(),
            }
            return {
                COUNTERS_FILE: self._dump(counters),
                self._issue_path(number): self._dump(record),
            }

        self._update_records(repo, "records: open issue", mutate, [COUNTERS_FILE])
        return created["number"]

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
