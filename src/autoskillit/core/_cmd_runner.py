"""Backward-compat shim for _cmd_runner. Real module at autoskillit.core.install.cmd_runner."""

import subprocess  # noqa: F401  # re-exported for monkeypatch.setattr("autoskillit.core._cmd_runner.subprocess", ...)

from autoskillit.core.install.cmd_runner import (
    CmdRunner,
    default_cmd_runner,
    run_gh,
    run_git,
)

__all__ = [
    "CmdRunner",
    "default_cmd_runner",
    "run_gh",
    "run_git",
]
