"""Packaged policy, launch wiring, and real macOS enforcement."""

import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from unittest.mock import Mock

import pytest

from outerloop import containment
from outerloop.harness import ClaudeCodeHarness
from outerloop.orchestrator import SubprocessEvaluator

pytestmark = pytest.mark.seatbelt


def test_packaged_profile():
    assert "(deny default)" in files("outerloop.containment").joinpath("macos.sb").read_text()


@pytest.mark.parametrize(("platform", "contained"), [("linux", False), ("darwin", True)])
def test_no_wrap(platform, contained, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", platform)
    argv = ["python", "-V"]
    assert (
        containment.seatbelt_wrap(
            argv, workspace=tmp_path, scratch=tmp_path, read_only=[], contained=contained
        )
        is argv
    )


def test_wrap_parameters(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    binary = tmp_path / "sandbox-exec"
    binary.touch()
    monkeypatch.setattr(containment, "SANDBOX_EXEC", binary)
    real = tmp_path / 'runtime "quoted"'
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    argv = containment.seatbelt_wrap(
        ["python", "-V"], workspace=tmp_path, scratch=tmp_path, read_only=[link]
    )
    assert argv[0] == str(binary)
    assert argv[-2:] == ["python", "-V"]
    assert f"READ_ONLY_0={real.resolve()}" in argv
    profile = argv[argv.index("-p") + 1]
    assert '(param "READ_ONLY_0")' in profile
    assert str(real) not in profile


def test_missing_warns_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(containment, "SANDBOX_EXEC", tmp_path / "missing")
    monkeypatch.setattr(containment, "_warned", False)
    for _ in range(2):
        assert containment.seatbelt_wrap(
            ["true"], workspace=tmp_path, scratch=tmp_path, read_only=[]
        ) == ["true"]
    assert caplog.text.count("sandbox-exec is missing") == 1


def test_executable_roots(tmp_path, monkeypatch):
    prefix = tmp_path / "python-install"
    (prefix / "bin").mkdir(parents=True)
    python = prefix / "bin/python"
    python.touch(mode=0o755)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(python)
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / "bin/python").touch(mode=0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(prefix))
    roots = containment.executable_roots(str(venv / "bin/python"), str(home / "bin/python"))
    assert home.resolve() not in roots
    assert prefix.resolve() in roots
    assert venv.resolve() in roots


@pytest.mark.parametrize("contained", [False, True])
def test_eval_wrap(tmp_path, monkeypatch, contained):
    wrap = Mock(return_value=["wrapped"])
    monkeypatch.setattr("outerloop.orchestrator.seatbelt_wrap", wrap)
    process = Mock(returncode=0)
    process.communicate.return_value = ('{"score": 1}', "")
    popen = Mock(return_value=process)
    monkeypatch.setattr("outerloop.orchestrator.subprocess.Popen", popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert (
        SubprocessEvaluator(container_image="image" if contained else "").evaluate(
            workspace, "python -m eval", "score"
        )
        == 1
    )
    assert wrap.call_count == (0 if contained else 1)
    if not contained:
        assert popen.call_args.args[0] == ["wrapped"]
        assert wrap.call_args.kwargs["workspace"] == workspace


def test_check_cache_inside_scratch_and_malformed_command(tmp_path, monkeypatch):
    wrap = Mock(return_value=["wrapped"])
    monkeypatch.setattr("outerloop.orchestrator.seatbelt_wrap", wrap)
    monkeypatch.setattr("outerloop.orchestrator.seatbelt_available", lambda: True)
    process = Mock(returncode=0)
    process.communicate.return_value = ("", "")
    popen = Mock(return_value=process)
    monkeypatch.setattr("outerloop.orchestrator.subprocess.Popen", popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    SubprocessEvaluator().check(workspace, "python -c 'unterminated")
    scratch = wrap.call_args.kwargs["scratch"]
    assert Path(popen.call_args.kwargs["env"]["UV_CACHE_DIR"]).is_relative_to(scratch)


@pytest.mark.parametrize("contained", [False, True])
def test_session_wrap(tmp_path, monkeypatch, contained):
    wrap = Mock(return_value=["wrapped"])
    monkeypatch.setattr("outerloop.harness.session_wrap", wrap)
    process = Mock(returncode=0)
    process.communicate.return_value = ('{"result": "done", "is_error": false}', "")
    popen = Mock(return_value=process)
    monkeypatch.setattr("outerloop.harness.subprocess.Popen", popen)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ClaudeCodeHarness(
        api_key="test-key", binary="/usr/bin/true", container_image="image" if contained else ""
    ).run("task", workspace)
    assert wrap.call_count == (0 if contained else 1)
    if not contained:
        assert popen.call_args.args[0] == ["wrapped"]
        assert wrap.call_args.args[1] == workspace


@pytest.mark.skipif(not containment.seatbelt_available(), reason="requires macOS sandbox-exec")
def test_real_seatbelt():
    # the default temp dir sits under /private/var/folders, beside other runs' scratch
    with tempfile.TemporaryDirectory(prefix="seatbelt-test-") as directory:
        root = Path(directory)
        workspace, scratch, home = (root / name for name in ("workspace", "scratch", "home"))
        for path in (workspace, scratch, home):
            path.mkdir()
        secret = home / "secret"
        secret.write_text("private")
        code = (
            "from pathlib import Path\n"
            f'Path({str(workspace / "written")!r}).write_text("ok")\n'
            "try:\n"
            f"    Path({str(secret)!r}).read_text()\n"
            "except PermissionError:\n"
            '    print("denied")\n'
            "else:\n"
            '    raise AssertionError("private read allowed")\n'
        )
        argv = containment.seatbelt_wrap(
            [sys.executable, "-c", code],
            workspace=workspace,
            scratch=scratch,
            read_only=containment.executable_roots(sys.executable),
        )
        result = subprocess.run(argv, capture_output=True, text=True, cwd=workspace)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "denied"
        assert (workspace / "written").read_text() == "ok"


def test_session_scratch_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    wrap = Mock(return_value=["wrapped"])
    monkeypatch.setattr(containment, "seatbelt_wrap", wrap)
    env = {"TMPDIR": "/tmp", "CLAUDE_CODE_TMPDIR": "/tmp"}
    assert containment.session_wrap(["claude"], tmp_path, tmp_path, env) == ["wrapped"]
    assert env["TMPDIR"] == str(tmp_path.resolve())
    assert env["CLAUDE_CODE_TMPDIR"] == str(tmp_path.resolve())
