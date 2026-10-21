"""The forge factory: the default backend and the unimplemented local refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from outerloop.forge import LOCAL, forge_from_env
from outerloop.github import GitHubClient


def test_forge_defaults_to_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTERLOOP_FORGE", raising=False)
    pat = tmp_path / "pat"
    pat.write_text("token\n")
    pat.chmod(0o600)
    forge = forge_from_env(str(pat))
    assert isinstance(forge, GitHubClient)
    assert forge.clone_url("org/repo") == "https://github.com/org/repo.git"


def test_local_forge_is_refused_until_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTERLOOP_FORGE", LOCAL)
    with pytest.raises(NotImplementedError, match="OUTERLOOP_FORGE=local"):
        forge_from_env("")
