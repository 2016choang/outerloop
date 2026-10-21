"""The forge factory: the default backend and the local backend's identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from outerloop.forge import LOCAL, forge_from_env, forge_identity
from outerloop.forge_local import LocalForge
from outerloop.github import GitHubClient


def test_forge_defaults_to_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTERLOOP_FORGE", raising=False)
    pat = tmp_path / "pat"
    pat.write_text("token\n")
    pat.chmod(0o600)
    forge = forge_from_env(str(pat))
    assert isinstance(forge, GitHubClient)
    assert forge.clone_url("org/repo") == "https://github.com/org/repo.git"


def test_local_forge_is_built_under_the_state_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OUTERLOOP_FORGE", LOCAL)
    monkeypatch.setenv("OUTERLOOP_ROOT", str(tmp_path))
    monkeypatch.delenv("OUTERLOOP_BOT_LOGIN", raising=False)
    forge = forge_from_env("")
    assert isinstance(forge, LocalForge)
    assert forge.repo_path("org/repo") == tmp_path / "forge" / "org" / "repo.git"
    # the local kernel identity defaults to `outerloop`; an explicit login wins
    assert forge.bot_login == "outerloop" and forge_identity(forge) == "outerloop"
    monkeypatch.setenv("OUTERLOOP_BOT_LOGIN", "lab-kernel")
    renamed = forge_from_env("")
    assert isinstance(renamed, LocalForge) and renamed.bot_login == "lab-kernel"
    # there is no default state root to put a bare repository under
    monkeypatch.delenv("OUTERLOOP_ROOT")
    with pytest.raises(ValueError, match="OUTERLOOP_ROOT"):
        forge_from_env("")
