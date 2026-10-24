"""Seatbelt containment for launches without an Apptainer image."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Iterable
from importlib.resources import files
from pathlib import Path

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_warned = False


def seatbelt_available() -> bool:
    return sys.platform == "darwin" and SANDBOX_EXEC.is_file()


def executable_roots(*executables: str) -> list[Path]:
    """Include launch prefixes as well as symlink targets (venvs need both)."""
    roots = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    for executable in (*executables, sys.executable, "uv", "python", "python3", "node"):
        located = shutil.which(executable)
        if not located:
            continue
        for path in (Path(located).absolute(), Path(located).resolve()):
            directory = path.parent
            prefix = directory.parent
            needs_prefix = directory.name == "bin" and (
                path.name.startswith("python")
                or path.name == "node"
                or (prefix / "pyvenv.cfg").is_file()
                or (prefix / "package.json").is_file()
            )
            roots.add(prefix if needs_prefix else directory)
    uv_python = Path(
        os.environ.get(
            "UV_PYTHON_INSTALL_DIR",
            str(
                Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
                / "uv/python"
            ),
        )
    )
    if uv_python.is_dir():
        roots.add(uv_python.resolve())
    # a tool installed straight under home (~/bin/python) must not open all of home
    home = Path.home().resolve()
    return sorted(root for root in roots if root not in (home, *home.parents))


def seatbelt_wrap(
    argv: list[str],
    *,
    workspace: Path,
    scratch: Path,
    read_only: Iterable[Path],
    contained: bool = False,
) -> list[str]:
    """Wrap an uncontained macOS launch; callers own the scratch lifetime."""
    global _warned
    if contained or sys.platform != "darwin":
        return argv
    if not seatbelt_available():
        if not _warned:
            logging.getLogger(__name__).warning(
                "sandbox-exec is missing: sessions and evaluations run uncontained"
            )
            _warned = True
        return argv
    roots = sorted({str(root.resolve()) for root in read_only})
    rules = "\n  ".join(f'(subpath (param "READ_ONLY_{i}"))' for i in range(len(roots)))
    profile = (
        files(__package__).joinpath("macos.sb").read_text().replace("; READ_ONLY_ROOTS", rules)
    )
    # A fresh file avoids following anything left by a previous session.
    fd, name = tempfile.mkstemp(prefix="seatbelt-", suffix=".sb", dir=scratch)
    with os.fdopen(fd, "w") as stream:
        stream.write(profile)
    parameters = {"WORKSPACE": str(workspace.resolve()), "SCRATCH": str(scratch.resolve())}
    parameters.update({f"READ_ONLY_{i}": root for i, root in enumerate(roots)})
    flags = [flag for key, value in parameters.items() for flag in ("-D", f"{key}={value}")]
    return [str(SANDBOX_EXEC), *flags, "-f", name, *argv]


def session_wrap(
    argv: list[str],
    workspace: Path,
    scratch: Path,
    env: dict[str, str],
    read_only: Iterable[Path] = (),
) -> list[str]:
    if sys.platform == "darwin":
        env["TMPDIR"] = str(scratch.resolve())
        env["CLAUDE_CODE_TMPDIR"] = str(scratch.resolve())
    return seatbelt_wrap(
        argv,
        workspace=workspace,
        scratch=scratch,
        read_only=[*executable_roots(argv[0]), *read_only],
    )
