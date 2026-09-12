"""Backward-compat shim for _cmd_runner. Real module at autoskillit.core.install.cmd_runner."""

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
