"""One local deployment end to end: a run's pull request, a person's comment,
the auto dial's merge, and the ledger observer accepting it.

Compute and model sessions are the only fakes: this drives the real factory,
Workspace transport, LocalForge records, the kernel's merge call, the inbox
reader and the ledger observer."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from outerloop import cli, inbox
from outerloop.contract import load_contract
from outerloop.forge_local import LocalForge
from outerloop.github import Workspace
from outerloop.ledger_events import (
    display_leader,
    measurement_pending,
    observe_target,
    queue_pending,
)
from outerloop.runstate import PARKED, RunRecord, save_record
from outerloop.tick import _merge_blessed_pr, _service_spec_from_env

CONTRACT = """\
benchmarks:
  - name: tsp
    command: uv run python -m pilot.eval --benchmark tsp --json
    metric: mean_tour_length
    direction: min
budgets: {gpu_hours_per_run: 1, runs_per_week: 10}
scope: {allowed: [src/pilot/solvers/]}
roadmap: docs/roadmap.md
merge: auto
"""

REPO = "org/pilot"


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


def test_a_local_deployment_from_factory_to_accepted_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    tmp_path.joinpath("config").mkdir()
    image = tmp_path / "agent.sif"
    image.write_text("")
    # The settings a local deployment has: no PAT, no App, no bot login.
    monkeypatch.setenv("OUTERLOOP_FORGE", "local")
    monkeypatch.setenv("OUTERLOOP_ROOT", str(state))
    monkeypatch.setenv("OUTERLOOP_HOME", str(tmp_path))
    monkeypatch.setenv("OUTERLOOP_TARGET", REPO)
    monkeypatch.setenv("OUTERLOOP_IMAGE", str(image))
    monkeypatch.setenv("OUTERLOOP_ENV_FILE", str(tmp_path / "config" / ".env"))
    for name in ("OUTERLOOP_PAT_FILE", "OUTERLOOP_GITHUB_APP_FILE", "OUTERLOOP_BOT_LOGIN"):
        monkeypatch.delenv(name, raising=False)

    # Bootstrap, then build the forge the way the tick does: through the
    # factory, with no credential anywhere in the environment.
    from outerloop.forge_local import bootstrap_local_repo

    bootstrap_local_repo(state / "forge", REPO, _seed(tmp_path))
    forge, spec = _service_spec_from_env(state)
    assert isinstance(forge, LocalForge)
    assert spec is not None and spec.bot_login == "outerloop"

    # Publish: a session clone pushes a candidate branch, then opens the PR.
    session = tmp_path / "session"
    ws = Workspace.clone(forge.clone_url(REPO), session, auth=None)
    ws.git("checkout", "-q", "-B", "feat/auto/agent-01", "origin/main")
    (session / "src" / "pilot" / "solvers" / "tsp.py").write_text("def solve():\n    return 1\n")
    ws.git("add", "-A")
    ws.git("-c", "user.name=agent", "-c", "user.email=a@a", "commit", "-qm", "faster")
    head = ws.git("rev-parse", "HEAD").strip()
    ws.push("feat/auto/agent-01")
    url = forge.create_pull(
        REPO,
        "[agent] tsp: 1 -> 2",
        head="feat/auto/agent-01",
        base="main",
        body="# report\n| candidate | 1 |\n\n## Research report\n\nfaster\n",
    )
    assert url.endswith("/1")

    # A person reviews from the CLI: the record carries their name and their
    # standing, so the author's inbox reads it as a person's comment.
    monkeypatch.setattr("getpass.getuser", lambda: "ada")
    assert cli.main(["pr", "comment", "1", "Nice work", "--root", str(state)]) == 0
    comments = forge.list_comments(REPO, 1)
    assert [(c["user"]["login"], c["author_association"]) for c in comments] == [("ada", "OWNER")]

    run = RunRecord("run-1", REPO, "tsp", PARKED, benchmark="tsp", pr_url=url, agent_id="agent-01")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert inbox.pending(run_dir, 0) == []
    inbox.gather_github_messages(
        run_dir, run, forge, "outerloop", 1.0, forge.get_pull_request(REPO, 1)
    )
    comments_in_inbox = [m for m in inbox.pending(run_dir, 0) if m.kind == "comment"]
    assert len(comments_in_inbox) == 1
    assert comments_in_inbox[0].payload["author"] == "ada"
    assert comments_in_inbox[0].payload["association"] == "OWNER"

    # The publish handoff queues the measured submission on the ledger; the
    # auto dial then merges the blessed head through the real kernel call.
    contract = load_contract(CONTRACT, REPO)
    pending = measurement_pending(
        ws,
        contract,
        contract.benchmark("tsp"),
        run,
        baseline=1.0,
        candidate=0.5,
        seed=7,
        measured_sha=head,
        pr_number=1,
        published_head=head,
        timestamp="2026-10-20",
        kind="SOLVER",
    )
    run = queue_pending(state, run, forge, pending, contract, 2.0)
    run = replace(
        run,
        state=PARKED,
        auto_blessed_head=head,
        auto_publish_head=head,
        auto_bless_base=forge.branch_sha(REPO, "main"),
    )
    save_record(state, run, 3.0)
    pull = forge.get_pull_request(REPO, 1)
    assert _merge_blessed_pr(state, run, forge, pull, "tick:test", 4.0, "outerloop") is True

    # The ledger observer accepts the merge: the merge commit's tree is the
    # measured tree, so the benchmark is confirmed rather than flagged.
    merged = forge.get_pull_request(REPO, 1)
    assert merged["merged"] is True and merged["merge_commit_sha"]
    assert observe_target(forge, REPO, {}) == set()
    assert display_leader(forge, REPO)["tsp"].main_commit == merged["merge_commit_sha"]


def test_a_cli_comment_from_the_kernel_login_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A person whose login is the kernel's would post a comment the inbox
    reads as the kernel's own and drops; the CLI refuses instead."""
    from outerloop.forge_local import bootstrap_local_repo

    state = tmp_path / "state"
    monkeypatch.setenv("OUTERLOOP_FORGE", "local")
    monkeypatch.delenv("OUTERLOOP_BOT_LOGIN", raising=False)
    bootstrap_local_repo(state / "forge", REPO, _seed(tmp_path))
    forge = LocalForge(state / "forge")
    assert forge.create_issue(REPO, "an issue", "body") == 1
    argv = ["pr", "comment", "1", "Nice work", "--root", str(state), "--target", REPO]

    monkeypatch.setattr("getpass.getuser", lambda: "outerloop")
    assert cli.main(argv) == 2
    assert "the kernel's login" in capsys.readouterr().err

    # the kernel's login may also be set explicitly
    monkeypatch.setenv("OUTERLOOP_BOT_LOGIN", "lab-kernel")
    monkeypatch.setattr("getpass.getuser", lambda: "lab-kernel")
    assert cli.main(argv) == 2
    assert "the kernel's login" in capsys.readouterr().err

    # ...or only in the operator's .env
    monkeypatch.delenv("OUTERLOOP_BOT_LOGIN")
    env_file = tmp_path / ".env"
    env_file.write_text("OUTERLOOP_BOT_LOGIN=file-kernel\n")
    env_file.chmod(0o600)
    monkeypatch.setattr(cli, "ENV_FILE", env_file)
    monkeypatch.delenv("OUTERLOOP_ENV_FILE", raising=False)
    monkeypatch.setattr("getpass.getuser", lambda: "file-kernel")
    assert cli.main(argv) == 2
    assert "the kernel's login" in capsys.readouterr().err

    # a resident Slurm tick lets the .env win over a conflicting shell value,
    # and the CLI must agree with it on who the kernel is
    monkeypatch.setenv("OUTERLOOP_BOT_LOGIN", "shell-kernel")
    monkeypatch.delenv("OUTERLOOP_COMPUTE", raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/" + name)
    assert cli.main(argv) == 2
    assert "the kernel's login" in capsys.readouterr().err

    # an empty .env login under that tick means the default identity
    env_file.write_text("OUTERLOOP_BOT_LOGIN=\n")
    monkeypatch.setattr("getpass.getuser", lambda: "outerloop")
    assert cli.main(argv) == 2
    assert "the kernel's login" in capsys.readouterr().err
    assert forge.list_comments(REPO, 1) == []

    # the state root is the shell's on every launch mode (the deploy step never
    # overrides it), so a conflicting .env root must not redirect the CLI
    env_file.write_text(f"OUTERLOOP_ROOT={tmp_path / 'elsewhere'}\n")
    monkeypatch.setenv("OUTERLOOP_ROOT", str(state))
    monkeypatch.setattr("getpass.getuser", lambda: "ada")
    assert cli.main(["pr", "comment", "1", "Looks right", "--target", REPO]) == 0
    assert [c["body"] for c in forge.list_comments(REPO, 1)] == ["Looks right"]
