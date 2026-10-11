"""All contract readers use `.outerloop.yaml`."""

from __future__ import annotations

from pathlib import Path

import pytest

from outerloop.contract import (
    ALWAYS_FORBIDDEN,
    CONTRACT_NAME,
    contract_in_tree,
    contract_text_in_tree,
    find_contract,
)
from outerloop.github import GitError, contract_at, guidance_at


def test_contract_and_github_are_forbidden() -> None:
    assert CONTRACT_NAME == ".outerloop.yaml"
    assert ALWAYS_FORBIDDEN == (".github", ".outerloop.yaml")


def test_find_contract() -> None:
    files = {".outerloop.yaml": "new", ".autoresearch.yaml": "old"}
    assert find_contract(lambda n: files.get(n)) == (".outerloop.yaml", "new")
    assert find_contract(lambda n: {".autoresearch.yaml": "old"}.get(n)) is None
    assert find_contract(lambda n: None) is None


def test_contract_in_tree_and_text(tmp_path: Path) -> None:
    assert contract_in_tree(tmp_path) is None
    (tmp_path / ".outerloop.yaml").write_text("new: 1\n")
    assert contract_in_tree(tmp_path) == (".outerloop.yaml", "new: 1\n")
    assert contract_text_in_tree(tmp_path) == "new: 1\n"


def test_contract_text_in_tree_raises_naming_contract(tmp_path: Path) -> None:
    (tmp_path / "unknown.yaml").write_text("benchmarks: []\n")
    with pytest.raises(FileNotFoundError, match=r"\.outerloop\.yaml"):
        contract_text_in_tree(tmp_path)


class _Ws:
    """A Workspace stand-in over a fixed tree: `ls-tree` reports the MODE,
    `git show <sha>:<path>` fails for a missing path (a symlink's show text
    is its target, so the reader never asks for one)."""

    def __init__(self, files: dict[str, str], symlinks: frozenset[str] = frozenset()) -> None:
        self.files = files
        self.symlinks = symlinks

    def git(self, *args: str) -> str:
        if args[0] == "ls-tree":
            name = args[-1]
            if name not in self.files and name not in self.symlinks:
                return ""
            mode = "120000" if name in self.symlinks else "100644"
            return f"{mode} blob 0000000000000000000000000000000000000000\t{name}"
        assert args[0] == "show"
        sha, _, name = args[1].partition(":")
        if name not in self.files:
            raise GitError(f"fatal: path '{name}' does not exist in '{sha}'")
        return self.files[name]


def test_contract_at_over_git_show() -> None:
    assert contract_at(_Ws({".outerloop.yaml": "new"}), "abc") == "new"
    with pytest.raises(GitError, match="no contract at abc"):
        contract_at(_Ws({}), "abc")
    # the one guidance reader: git objects only, and a symlink blob (whose
    # `git show` text is its target, not file content) is refused as empty
    # guidance, like a missing path
    ws = _Ws({"guidance/author.md": "sweep first"}, symlinks=frozenset({"guidance/panel.md"}))
    assert guidance_at(ws, "abc", "guidance") == ("sweep first", "")
    assert guidance_at(_Ws({}), "abc", "guidance") == ("", "")
    # the Actions verifier's API reader refuses the same links, although the
    # Contents API itself would follow them to the target file's text
    from outerloop.verify_agent import _regular_file

    tree = {
        "": {"g": "dir", "link": "symlink"},
        "g": {"author.md": "file", "panel.md": "symlink"},
    }

    class API:
        def list_directory(self, repo, path, ref):
            return [{"name": n, "type": t} for n, t in tree.get(path, {}).items()]

        def get_file_content(self, repo, path, ref):
            return "followed"

    assert _regular_file(API(), "o/r", "g/author.md", "main") == "followed"
    assert _regular_file(API(), "o/r", "g/panel.md", "main") is None
    assert _regular_file(API(), "o/r", "link/author.md", "main") is None


def test_tick_contract_text_via_the_api() -> None:
    from outerloop.tick import _contract_text

    class GH:
        def __init__(self, files: dict[str, str]) -> None:
            self.files = files

        def get_file_content(self, repo: str, path: str, ref: str) -> str | None:
            return self.files.get(path)  # None = missing, like the real client

    assert _contract_text(GH({".outerloop.yaml": "n"}), "o/r", "x") == "n"
    assert _contract_text(GH({}), "o/r", "main") is None
