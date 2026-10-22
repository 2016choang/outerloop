"""The local forge's records: pull requests, threads, issues and merges."""

from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from outerloop import forge_local
from outerloop.forge_local import RECORDS_REF, LocalForge, bootstrap_local_repo
from outerloop.github import GitError, GitHubError

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
    return LocalForge(root), repo, bare


def _write(dest: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _commit(
    bare: Path, dest: Path, branch: str, base: str, files: dict[str, str], message: str = "change"
) -> str:
    """Commit `files` onto `branch` (based on `base`) in a scratch clone and
    push it to the bare repository; returns the new commit."""
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(bare), str(dest)], check=True, capture_output=True
    )
    _git(dest, "checkout", "-q", "-B", branch, f"origin/{base}")
    _write(dest, files)
    _git(dest, "add", "-A")
    _git(dest, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)
    _git(dest, "push", "-q", "origin", f"{branch}:{branch}")
    return _git(dest, "rev-parse", "HEAD")


def _external_merge(
    bare: Path, dest: Path, branch: str, base: str, files: dict[str, str], message: str = "merge"
) -> str:
    """A person's `git merge --no-ff` pushed straight to the base."""
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(bare), str(dest)], check=True, capture_output=True
    )
    _git(dest, "checkout", "-q", base)
    _write(dest, files)
    _git(dest, "add", "-A")
    _git(dest, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base moved")
    _git(
        dest,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "merge",
        "--no-ff",
        f"origin/{branch}",
        "-m",
        message,
    )
    _git(dest, "push", "-q", "origin", f"{base}:{base}")
    return _git(dest, "rev-parse", "HEAD")


def test_pull_record_lifecycle(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    first = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    body = "intro\n| candidate | 1 |\n\n## Research report\n\nprose"
    url = fashion.create_pull(repo, "Raise the score", "topic", "main", body, draft=True)
    assert url.endswith("/1")
    assert fashion.find_open_pull_for_head(repo, "topic", "main")["number"] == 1

    pr = fashion.get_pull_request(repo, 1)
    assert pr["number"] == 1 and pr["state"] == "open" and pr["merged"] is False
    assert pr["draft"] is True and pr["auto_merge"] is None
    assert pr["user"]["login"] == "outerloop" and pr["labels"] == []
    assert pr["head"] == {"sha": first, "ref": "topic"} and pr["base"]["ref"] == "main"
    assert pr["html_url"].endswith("/1")
    assert pr["mergeable_state"] == "clean"
    assert pr["merged_at"] is None and pr["merge_commit_sha"] is None

    fashion.comment(repo, 1, "question")
    fashion.comment(repo, 1, "answer")
    comments = fashion.list_comments(repo, 1)
    assert [c["id"] for c in comments] == [1, 2]
    assert comments[0]["body"] == "question"
    assert comments[0]["user"]["login"] == "outerloop"
    assert comments[0]["author_association"] == "NONE"
    assert comments[0]["created_at"].endswith("Z")

    # a follow-up push is seen at once
    second = _commit(bare, tmp_path / "topic-2", "topic", "topic", {"notes.txt": "two\n"})
    assert fashion.get_pull_request(repo, 1)["head"]["sha"] == second

    # both body transformations
    assert fashion.update_candidate_row(repo, 1, 10.2) is True
    assert "| candidate | 10.2 |" in fashion.get_pull_request(repo, 1)["body"]
    fashion.append_pull_body(repo, 1, "---\n**Edit:** first")
    fashion.append_pull_body(repo, 1, "---\n**Edit:** second")
    edited = fashion.get_pull_request(repo, 1)["body"]
    assert edited.count("<!-- outerloop:body-edit -->") == 1
    assert "second" in edited and "first" not in edited

    # expected_head: a stale read leaves the draft alone, the live head clears it
    fashion.mark_ready_for_review(repo, 1, expected_head=first)
    assert fashion.get_pull_request(repo, 1)["draft"] is True
    fashion.mark_ready_for_review(repo, 1, expected_head=second)
    assert fashion.get_pull_request(repo, 1)["draft"] is False

    fashion.close_issue(repo, 1)
    closed = fashion.get_pull_request(repo, 1)
    assert closed["state"] == "closed" and closed["merged"] is False
    assert fashion.find_open_pull_for_head(repo, "topic", "main") is None
    with pytest.raises(GitHubError) as missing:
        fashion.get_pull_request(repo, 99)
    assert missing.value.status == 404


def test_merge_pull_refuses_a_draft(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    main = fashion.branch_sha(repo, "main")
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Not yet", "topic", "main", "body", draft=True)
    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is False
    assert fashion.branch_sha(repo, "main") == main
    fashion.mark_ready_for_review(repo, 1, expected_head=head)
    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is True


def test_merge_pull_is_one_transaction_and_refuses_a_moved_head(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    main = fashion.branch_sha(repo, "main")
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merge me", "topic", "main", "body")

    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is True
    merged = fashion.branch_sha(repo, "main")
    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True and pr["state"] == "closed"
    assert pr["merge_commit_sha"] == merged and pr["merged_at"]
    assert _git(bare, "rev-list", "--parents", "-n", "1", merged).split()[1:] == [main, head]
    assert _git(bare, "log", "-1", "--format=%s", merged).startswith("Merge pull request #1")
    assert _git(bare, "log", "-1", "--format=%an", merged) == "outerloop"

    # a second pull request: a stale expected head merges nothing
    head2 = _commit(bare, tmp_path / "topic-2", "topic2", "main", {"other.txt": "two\n"})
    fashion.create_pull(repo, "Second", "topic2", "main", "body")
    moved = _commit(bare, tmp_path / "topic-3", "topic2", "topic2", {"other.txt": "three\n"})
    assert fashion.merge_pull(repo, 2, "merge", expected_head=head2) is False
    assert fashion.branch_sha(repo, "main") == merged
    assert fashion.get_pull_request(repo, 2)["state"] == "open"
    assert fashion.merge_pull(repo, 2, "squash", expected_head=moved) is False
    assert fashion.merge_pull(repo, 2, "merge", expected_head=moved) is True


def test_merge_transaction_refuses_when_the_head_moves_under_it(
    tmp_path, forge, monkeypatch
) -> None:
    fashion, repo, bare = forge
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merge me", "topic", "main", "body")
    base_before = fashion.branch_sha(repo, "main")

    original = fashion._merge_commit

    def raced(*args, **kwargs):
        commit = original(*args, **kwargs)
        # the head moves after the pre-check but before update-ref runs
        _commit(bare, tmp_path / "race", "topic", "topic", {"notes.txt": "raced\n"})
        return commit

    monkeypatch.setattr(fashion, "_merge_commit", raced)
    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is False
    assert fashion.branch_sha(repo, "main") == base_before
    assert fashion.get_pull_request(repo, 1)["state"] == "open"


def test_a_merge_made_outside_the_kernel_is_recorded(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merged by hand", "topic", "main", "body")
    merge = _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})
    # the branch moves after the merge; the merge is still found from the
    # head the record last saw
    _commit(bare, tmp_path / "topic-moved", "topic", "topic", {"notes.txt": "moved\n"})

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True and pr["state"] == "closed"
    assert pr["merge_commit_sha"] == merge
    # the fields the ledger reads: the merge commit's tree is readable
    tree = _git(bare, "rev-parse", f"{merge}^{{tree}}")
    assert fashion.commit_tree(repo, pr["merge_commit_sha"]) == tree

    # the record is kept even after the branch is deleted
    _git(bare, "update-ref", "-d", "refs/heads/topic")
    again = fashion.get_pull_request(repo, 1)
    assert again["merged"] is True and again["merge_commit_sha"] == merge

    # a fast-forward: the head itself is the merge commit
    fast = _commit(bare, tmp_path / "fast", "fast", "main", {"fast.txt": "f\n"})
    fashion.create_pull(repo, "Fast", "fast", "main", "body")
    _git(bare, "update-ref", "refs/heads/main", fast, fashion.branch_sha(repo, "main"))
    ff = fashion.get_pull_request(repo, 2)
    assert ff["merged"] is True and ff["merge_commit_sha"] == fast
    assert ff["state"] == "closed"


def test_merge_pull_lands_nothing_when_the_head_was_merged_outside_after_the_read(
    tmp_path, forge
) -> None:
    fashion, repo, bare = forge
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Raced", "topic", "main", "body")
    outside = _external_merge(bare, tmp_path / "merge", "topic", "main", {"m.txt": "m\n"})
    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is False
    assert fashion.branch_sha(repo, "main") == outside  # no second merge commit
    assert fashion.get_pull_request(repo, 1)["merge_commit_sha"] == outside


def test_a_merged_record_keeps_the_head_the_merge_landed(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    merged_head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merged", "topic", "main", "body")
    merge = _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True and pr["merge_commit_sha"] == merge
    assert pr["head"]["sha"] == merged_head

    # a later push to the branch is not what landed
    later = _commit(bare, tmp_path / "topic-2", "topic", "topic", {"notes.txt": "two\n"})
    again = fashion.get_pull_request(repo, 1)
    assert again["head"]["sha"] == merged_head != later
    assert again["merge_commit_sha"] == merge
    # a write that stamps the head leaves the merged record alone too
    fashion.append_pull_body(repo, 1, "---\n**Edit:** after the merge")
    assert fashion.get_pull_request(repo, 1)["head"]["sha"] == merged_head


def test_a_merge_of_an_advanced_head_survives_the_branch_deletion(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    seen = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Advanced", "topic", "main", "body")
    # the head advances between the kernel's reads, then is merged --no-ff
    advanced = _commit(bare, tmp_path / "topic-2", "topic", "topic", {"notes.txt": "two\n"})
    merge = _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})
    _git(bare, "update-ref", "-d", "refs/heads/topic")

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True
    assert pr["merge_commit_sha"] == merge
    assert pr["head"]["sha"] == advanced
    assert advanced != seen


def test_a_fast_forward_is_the_head_on_the_first_parent_chain(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    fast = _commit(bare, tmp_path / "fast", "fast", "main", {"fast.txt": "f\n"})
    fashion.create_pull(repo, "Fast", "fast", "main", "body")
    _git(bare, "update-ref", "refs/heads/main", fast, fashion.branch_sha(repo, "main"))
    # the base moves on linearly; the head sits on its first-parent chain
    _commit(bare, tmp_path / "after", "main", "main", {"after.txt": "a\n"})

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True
    assert pr["merge_commit_sha"] == fast
    assert pr["head"]["sha"] == fast


def test_a_concurrent_close_does_not_hide_a_landed_merge(tmp_path, forge, monkeypatch) -> None:
    fashion, repo, bare = forge
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Raced", "topic", "main", "body")

    original = fashion._land_merge

    def land_then_close(*args, **kwargs):
        landed = original(*args, **kwargs)
        if landed:
            fashion.close_issue(repo, 1)  # a concurrent writer closes the record
        return landed

    monkeypatch.setattr(fashion, "_land_merge", land_then_close)
    assert fashion.merge_pull(repo, 1, "merge", expected_head=head) is True

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True and pr["state"] == "closed"
    assert pr["merge_commit_sha"] == fashion.branch_sha(repo, "main")


def test_find_open_pull_skips_a_pull_it_just_found_merged(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merged by hand", "topic", "main", "body")
    _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})

    # the record is still open on disk; the read records the outside merge
    assert fashion.find_open_pull_for_head(repo, "topic", "main") is None
    assert fashion.get_pull_request(repo, 1)["merged"] is True


def test_create_pull_refuses_a_head_the_base_already_contains(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    with pytest.raises(GitHubError) as same:
        fashion.create_pull(repo, "Same", "main", "main", "body")
    assert same.value.status == 422

    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})
    with pytest.raises(GitHubError) as contained:
        fashion.create_pull(repo, "Nothing to merge", "topic", "main", "body")
    assert contained.value.status == 422


def test_unrelated_record_directories_keep_their_subtree(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    fashion.create_issue(repo, "issue", "body")
    fashion.comment(repo, 1, "note")
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    url = fashion.create_pull(repo, "pull", "topic", "main", "body")
    pull_number = int(url.rsplit("/", 1)[-1])

    def subtree(path: str) -> str:
        tree = _git(bare, "rev-parse", f"{RECORDS_REF}^{{tree}}")
        return _git(bare, "rev-parse", f"{tree}:{path}")

    issues, threads = subtree("issues"), subtree("threads")
    fashion.append_pull_body(repo, pull_number, "---\n**Edit:** a pull write")
    assert subtree("issues") == issues
    assert subtree("threads") == threads

    pulls = subtree("pulls")
    fashion.create_issue(repo, "another issue", "body")
    assert subtree("pulls") == pulls


def test_local_forge_requires_git_238(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(forge_local, "_git_version", lambda _text: (2, 37, 4))
    with pytest.raises(GitError, match=r"Git 2\.38 or newer"):
        LocalForge(tmp_path / "forge")


def test_concurrent_record_writers_retry_without_losing_a_write(forge, monkeypatch) -> None:
    first, repo, _ = forge
    second = LocalForge(first.root)
    barrier = threading.Barrier(2)
    original = LocalForge._move

    def move(self, repo_name, ref, new, old):
        if ref == RECORDS_REF and old and set(old) == {"0"}:
            barrier.wait(timeout=10)  # both writers commit against the same tip
        return original(self, repo_name, ref, new, old)

    monkeypatch.setattr(LocalForge, "_move", move)
    with ThreadPoolExecutor(max_workers=2) as pool:
        numbers = list(
            pool.map(
                lambda pair: pair[0].create_issue(pair[1], f"issue from {pair[0].bot_login}", ""),
                [(first, repo), (second, repo)],
            )
        )
    assert sorted(numbers) == [1, 2]
    assert [i["number"] for i in first.list_open_issues(repo)] == [1, 2]


def test_a_write_reads_only_the_records_it_changes(forge, monkeypatch) -> None:
    """A record write names the blobs it needs; it must not load every record
    in the tree (the cost grows with the deployment's history)."""
    fashion, repo, _ = forge
    for number in range(1, 4):
        fashion.create_issue(repo, f"issue {number}", "body")
    reads: list[set[str]] = []
    original = LocalForge._blobs

    def blobs(self, repo_name, shas):
        reads.append(set(shas))
        return original(self, repo_name, shas)

    monkeypatch.setattr(LocalForge, "_blobs", blobs)
    fashion.close_issue(repo, 2)
    assert reads == [{"issues/2.json"}]
    reads.clear()
    assert fashion.create_issue(repo, "another", "body") == 4
    assert reads == [{"counters.json"}]


def test_mergeable_state_is_clean_behind_or_dirty(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "topic\n"})
    fashion.create_pull(repo, "State", "topic", "main", "body")
    assert fashion.get_pull_request(repo, 1)["mergeable_state"] == "clean"

    _commit(bare, tmp_path / "main-1", "main", "main", {"main-only.txt": "m\n"})
    assert fashion.get_pull_request(repo, 1)["mergeable_state"] == "behind"

    _commit(bare, tmp_path / "main-2", "main", "main", {"notes.txt": "base\n"})
    assert fashion.get_pull_request(repo, 1)["mergeable_state"] == "dirty"


def test_shared_number_sequence_and_issue_shape(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    first = fashion.create_issue(repo, "first", "body", labels=["outerloop:task", "bug"])
    assert first == 1
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    url = fashion.create_pull(repo, "second", "topic", "main", "body")
    assert url.endswith("/2")
    third = fashion.create_issue(repo, "third", "body")
    assert third == 3

    issues = fashion.list_open_issues(repo)
    assert [i["number"] for i in issues] == [1, 3]
    assert issues[0]["title"] == "first" and issues[0]["state"] == "open"
    assert issues[0]["user"]["login"] == "outerloop"
    assert issues[0]["author_association"] == "NONE"
    assert issues[0]["labels"] == [{"name": "outerloop:task"}, {"name": "bug"}]
    assert issues[0]["created_at"].endswith("Z")
    assert [i["number"] for i in fashion.list_open_issues(repo, creator="outerloop")] == [1, 3]
    assert fashion.list_open_issues(repo, creator="someone") == []

    fashion.close_issue(repo, 1)
    assert [i["number"] for i in fashion.list_open_issues(repo)] == [3]
    # close_issue closes a pull request too
    fashion.close_issue(repo, 2)
    assert fashion.get_pull_request(repo, 2)["state"] == "closed"
    with pytest.raises(GitHubError) as missing:
        fashion.close_issue(repo, 99)
    assert missing.value.status == 404


def test_list_open_issues_honors_max_pages(forge, monkeypatch) -> None:
    """GitHub returns at most `max_pages` pages of 100, oldest first; the local
    reader must not hand back more than that."""
    fashion, repo, _ = forge
    records = {
        f"issues/{number}.json": {
            "number": number,
            "state": "open",
            "user": {"login": "outerloop"},
        }
        for number in range(1, 126)
    }
    monkeypatch.setattr(LocalForge, "_ref_head", lambda self, repo_name, ref: "tip")
    monkeypatch.setattr(
        LocalForge, "_json_records", lambda self, repo_name, ref, directory: records
    )
    assert [i["number"] for i in fashion.list_open_issues(repo, max_pages=1)] == list(range(1, 101))
    assert [i["number"] for i in fashion.list_open_issues(repo, max_pages=2)] == list(range(1, 126))


def test_list_directory_raises_on_a_damaged_repository(forge) -> None:
    fashion, repo, bare = forge
    main = fashion.branch_sha(repo, "main")
    # the ref still parses, but there is no object behind it
    (bare / "refs" / "heads" / "main").write_text("f" * len(main) + "\n")
    with pytest.raises(GitError):
        fashion.list_directory(repo, "", "main")


def test_put_files_refuses_to_replace_a_directory_or_a_file(forge) -> None:
    fashion, repo, bare = forge
    before = fashion.branch_sha(repo, "main")
    # a file where the base has a directory
    assert fashion.put_files(repo, {"docs": "flat\n"}, "main", "bad") is False
    # a path underneath a base file
    assert fashion.put_files(repo, {".outerloop.yaml/nested": "n\n"}, "main", "bad") is False
    assert fashion.branch_sha(repo, "main") == before
    assert _git(bare, "show", "main:docs/roadmap.md") == "# roadmap"
    assert "benchmarks:" in _git(bare, "show", "main:.outerloop.yaml")

    # an ordinary write still lands
    assert fashion.put_files(repo, {"docs/new.md": "new\n"}, "main", "ok") is True
    assert _git(bare, "show", "main:docs/new.md") == "new"


def test_mark_ready_leaves_a_record_merged_during_the_read_alone(
    tmp_path, forge, monkeypatch
) -> None:
    fashion, repo, bare = forge
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Raced", "topic", "main", "body", draft=True)

    original = fashion.get_pull_request
    merge: dict[str, str] = {}

    def read_then_merge(repo_name: str, number: int) -> dict:
        panel = original(repo_name, number)
        if not merge:
            # the merge (and a later push) lands after the panel read but
            # before the compare-and-swap
            merge["sha"] = _external_merge(
                bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"}
            )
            _commit(bare, tmp_path / "topic-2", "topic", "topic", {"notes.txt": "two\n"})
            fashion._record_merge(repo_name, number, merge["sha"], head)
        return panel

    monkeypatch.setattr(fashion, "get_pull_request", read_then_merge)
    fashion.mark_ready_for_review(repo, 1)

    pr = original(repo, 1)
    assert pr["merged"] is True and pr["merge_commit_sha"] == merge["sha"]
    assert pr["draft"] is True
    assert pr["head"]["sha"] == head  # the head the merge landed, not the push


def test_dry_run_keeps_the_head_an_outside_merge_landed(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    landed = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Dry", "topic", "main", "body")
    merge = _external_merge(bare, tmp_path / "merge", "topic", "main", {"main-only.txt": "m\n"})
    advanced = _commit(bare, tmp_path / "topic-2", "topic", "topic", {"notes.txt": "two\n"})

    reader = LocalForge(fashion.root, dry_run=True)
    pr = reader.get_pull_request(repo, 1)
    assert pr["merged"] is True and pr["state"] == "closed"
    assert pr["merge_commit_sha"] == merge
    assert pr["head"]["sha"] == landed != advanced


def test_the_earliest_landing_of_a_head_wins(tmp_path, forge) -> None:
    fashion, repo, bare = forge
    head = _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Landed early", "topic", "main", "body")
    first = _external_merge(bare, tmp_path / "merge-1", "topic", "main", {"main-only.txt": "m\n"})
    # a later branch is built on top of the already-landed head and merged
    later = _commit(bare, tmp_path / "later", "later", "topic", {"later.txt": "l\n"})
    second = _external_merge(bare, tmp_path / "merge-2", "later", "main", {"more.txt": "m2\n"})
    assert _git(bare, "rev-list", "--parents", "-n", "1", later).split()[1] == head
    assert _git(bare, "rev-parse", f"{second}^2") == later

    pr = fashion.get_pull_request(repo, 1)
    assert pr["merged"] is True
    assert pr["merge_commit_sha"] == first != second
    assert pr["head"]["sha"] == head


@pytest.mark.parametrize("configured", [True, False])
def test_cli_merge_uses_operator_git_author(tmp_path, forge, monkeypatch, configured):
    from outerloop import cli

    fashion, repo, bare = forge
    _commit(bare, tmp_path / "topic", "topic", "main", {"notes.txt": "one\n"})
    fashion.create_pull(repo, "Merge me", "topic", "main", "body")
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n name = Operator\n email = operator@example.com\n" if configured else ""
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("OUTERLOOP_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("OUTERLOOP_FORGE", "local")
    monkeypatch.setenv("OUTERLOOP_TARGET", repo)
    monkeypatch.setenv("OUTERLOOP_ROOT", str(tmp_path))
    monkeypatch.delenv("OUTERLOOP_BOT_LOGIN", raising=False)
    assert cli.main(["pr", "merge", "1"]) == 0
    author = _git(bare, "log", "-1", "--format=%an <%ae>", "main")
    expected = (
        "Operator <operator@example.com>"
        if configured
        else "outerloop <outerloop@users.noreply.github.com>"
    )
    assert author == expected
    assert _git(bare, "log", "-1", "--format=%cn", "main") == "outerloop"
