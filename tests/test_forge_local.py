"""The local forge against real bare repositories: refs, files and transport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from outerloop.contract import load_contract
from outerloop.forge_local import LocalForge, bootstrap_local_repo
from outerloop.github import GitHubError, Workspace

CONTRACT = """\
benchmarks:
  - name: tsp
    command: uv run python -m pilot.eval --benchmark tsp --json
    metric: mean_tour_length
    direction: min
budgets: {gpu_hours_per_run: 1, runs_per_week: 10}
scope: {allowed: [src/pilot/solvers/]}
roadmap: docs/roadmap.md
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    (seed / "src" / "pilot" / "solvers").mkdir(parents=True)
    (seed / "docs").mkdir()
    (seed / ".outerloop.yaml").write_text(CONTRACT)
    (seed / "docs" / "roadmap.md").write_text("# roadmap\n")
    (seed / "src" / "pilot" / "solvers" / "tsp.py").write_text("def solve(): ...\n")
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    return seed


@pytest.fixture
def forge(tmp_path: Path) -> tuple[LocalForge, str, Path]:
    root = tmp_path / "forge"
    repo = "org/pilot"
    bare = bootstrap_local_repo(root, repo, _seed(tmp_path))
    fashion = LocalForge(root)
    return fashion, repo, bare


def test_refs_and_files_round_trip_with_404_and_cas(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    assert fashion.default_branch(repo) == "main"
    main = fashion.branch_sha(repo, "main")
    assert fashion.branch_head(repo, "main") == main
    assert fashion.branch_head(repo, "missing") == ""

    tree = fashion.get_tree(repo, main)
    assert tree["truncated"] is False
    assert {item["path"] for item in tree["tree"]} >= {
        "docs/roadmap.md",
        "src/pilot/solvers/tsp.py",
    }
    assert fashion.commit_tree(repo, main) == tree["sha"]
    assert fashion.get_file(repo, "docs/roadmap.md", main) == "# roadmap\n"
    assert fashion.get_file_content(repo, "docs/roadmap.md", main) == "# roadmap\n"
    assert fashion.get_file_content(repo, "docs", main) is None  # a directory
    assert fashion.get_file_content(repo, "gone.md", main) is None
    with pytest.raises(GitHubError) as missing:
        fashion.get_file(repo, "gone.md", main)
    assert missing.value.status == 404
    with pytest.raises(GitHubError) as missing_branch:
        fashion.branch_sha(repo, "missing")
    assert missing_branch.value.status == 404

    listing = fashion.list_directory(repo, "docs", main)
    assert [(e["name"], e["type"]) for e in listing] == [("roadmap.md", "file")]
    assert fashion.list_directory(repo, "gone", main) == []

    # create_ref never overwrites
    fashion.create_ref(repo, "refs/heads/topic", main)
    with pytest.raises(GitHubError) as exists:
        fashion.create_ref(repo, "refs/heads/topic", main)
    assert exists.value.status == 422
    assert fashion.ensure_branch(repo, "research-log") is True
    assert fashion.ensure_branch(repo, "research-log") is True

    # one batched commit, then a single-file create and update
    assert (
        fashion.put_files(
            repo,
            {"results/leader.json": "{}\n", "BENCHMARKS.md": "# progress\n"},
            "research-log",
            "ledger",
            expected_head=main,
        )
        is True
    )
    first = fashion.branch_sha(repo, "research-log")
    assert fashion.head_contains(repo, main, first) is True
    assert fashion.head_contains(repo, first, main) is False
    assert fashion.put_file(repo, "notes.txt", "one\n", "research-log", "note") == "created"
    assert fashion.put_file(repo, "notes.txt", "two\n", "research-log", "note") == "updated"
    assert fashion.get_file(repo, "notes.txt", "research-log") == "two\n"
    updated = fashion.branch_sha(repo, "research-log")
    assert _git(bare, "rev-list", "--count", f"{first}..{updated}") == "2"

    # compare-and-swap: a stale expected head writes nothing
    assert (
        fashion.put_files(
            repo,
            {"stale.txt": "no\n"},
            "research-log",
            "stale",
            expected_head=first,
        )
        is False
    )
    assert fashion.get_file_content(repo, "stale.txt", "research-log") is None

    # dry run honors every mutating verb
    dry = LocalForge(fashion.root, dry_run=True)
    dry.create_ref(repo, "refs/heads/dry", main)
    assert dry.branch_head(repo, "dry") == ""
    assert dry.put_file(repo, "dry.txt", "x\n", "research-log", "dry") == "created"
    assert dry.put_files(repo, {"dry.txt": "x\n"}, "research-log", "dry") is True
    assert dry.ensure_branch(repo, "dry-branch") is True
    assert dry.branch_head(repo, "dry-branch") == ""
    assert fashion.get_file_content(repo, "dry.txt", "research-log") is None


def test_ledger_written_and_read_through_the_local_forge(tmp_path) -> None:
    from outerloop.ledger_branch import ensure_ledger_branch, read_ledger, write_ledger
    from outerloop.progress import LEADER_FILE, LeaderEntry, ledger_record

    root = tmp_path / "forge"
    repo = "org/pilot"
    bare = bootstrap_local_repo(root, repo)  # init --bare: an empty first commit
    fashion = LocalForge(root)
    assert fashion.default_branch(repo) == "main"
    assert fashion.get_tree(repo, fashion.branch_sha(repo, "main"))["tree"] == []

    ensure_ledger_branch(fashion, repo, fashion.branch_sha(repo, "main"))
    head, leader, pendings = read_ledger(fashion, repo)
    assert leader == {} and pendings == {}

    entry = LeaderEntry("tsp", "mean_tour_length", "min", 1.0, 2.0, "run-1", "2026-10-20")
    write_ledger(
        fashion,
        repo,
        head,
        lambda current, submissions: {LEADER_FILE: json.dumps({"tsp": ledger_record(entry)})},
    )

    written, leader, pendings = read_ledger(fashion, repo)
    assert written != head and leader["tsp"] == entry and pendings == {}
    progress = fashion.get_file(repo, "BENCHMARKS.md", written)
    assert "| tsp |" in progress
    assert "https://github.com/org/pilot" not in progress  # a local forge has no web link
    # the kernel's identity authors the write; git refuses an empty author
    assert _git(bare, "log", "-1", "--format=%an", "research-log") == "outerloop"


def test_a_session_clone_from_a_path_shares_no_objects(tmp_path, forge) -> None:
    _, _, bare = forge
    dest = tmp_path / "session"
    Workspace.clone(str(bare), dest)

    assert not (dest / ".git" / "objects" / "info" / "alternates").exists()

    def inodes(objects: Path) -> set[int]:
        return {path.stat().st_ino for path in objects.rglob("*") if path.is_file()}

    shared = inodes(bare / "objects") & inodes(dest / ".git" / "objects")
    assert shared == set()


def test_a_local_workspace_pushes_its_line_snapshot(tmp_path, forge, monkeypatch) -> None:
    from outerloop.attempt import _checkout_line, _push_line_snapshot

    _, _, bare = forge
    # The bare repo is cloned through a path; no credential is involved.
    ws = Workspace.clone(str(bare), tmp_path / "line-ws")
    _checkout_line(ws, ws.root, "agent-07", "main")
    (ws.root / "src" / "pilot" / "solvers" / "tsp.py").write_text("def solve(): return 1\n")
    monkeypatch.setenv("OUTERLOOP_BOT_LOGIN", "outerloop")

    _push_line_snapshot(
        ws,
        "agents/agent-07",
        "run-1",
        "improved",
        contract=load_contract(CONTRACT, "org/pilot"),
    )

    assert _git(bare, "show", "agents/agent-07:src/pilot/solvers/tsp.py") == (
        "def solve(): return 1"
    )
    assert _git(bare, "log", "-1", "--format=%s", "agents/agent-07").startswith(
        "line snapshot: run-1"
    )


def test_bootstrap_takes_a_sha256_source_and_refuses_an_empty_one(tmp_path) -> None:
    source = tmp_path / "sha256"
    subprocess.run(
        ["git", "init", "-q", "--object-format=sha256", "-b", "main", str(source)], check=True
    )
    (source / "a.txt").write_text("x")
    _git(source, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(source, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed")
    bootstrap_local_repo(tmp_path / "forge", "org/wide", source)
    fashion = LocalForge(tmp_path / "forge")
    assert fashion.ensure_branch("org/wide", "research-log")
    assert len(fashion.branch_sha("org/wide", "research-log")) == 64

    empty = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(ValueError, match="no commits"):
        bootstrap_local_repo(tmp_path / "forge", "org/empty", empty)


def test_bootstrap_leaves_no_seed_remote_or_credential_in_the_shared_config(tmp_path) -> None:
    """The bare clone would keep its seed URL as remote.origin.url, and a
    `--forge-source` URL with userinfo would expose that credential to every
    state-root user. The forge never fetches from its seed again."""
    seed = _seed(tmp_path)
    bare = bootstrap_local_repo(tmp_path / "forge", "org/private", f"file://user:secret@{seed}")
    assert _git(bare, "remote") == ""
    config = (bare / "config").read_text()
    assert "remote.origin" not in config
    assert "secret" not in config


def test_inherited_git_settings_neither_redirect_nor_reauthor_a_write(
    tmp_path, forge, monkeypatch
) -> None:
    fashion, repo, bare = forge
    head = fashion.branch_sha(repo, "main")
    with monkeypatch.context() as env:
        env.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
        env.setenv("GIT_AUTHOR_NAME", "intruder")
        env.setenv("GIT_COMMITTER_NAME", "intruder")
        assert fashion.put_files(repo, {"notes.md": "x"}, "main", "note", expected_head=head)
    assert _git(bare, "log", "-1", "--format=%an %cn", "main") == "outerloop outerloop"
