"""Sync subprocess runner for short-lived git/gh CLI commands.

Lighter than SubprocessRunner (which targets managed agent sessions).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "CmdRunner",
    "default_cmd_runner",
    "run_gh",
    "run_git",
]


@runtime_checkable
class CmdRunner(Protocol):
    """Sync callable for short-lived subprocess execution."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: str | Path | None = None,
        timeout: float | None = None,
        check: bool = False,
        input_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def default_cmd_runner(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        input=input_data,
    )


def run_git(
    args: list[str],
    *,
    cwd: str | Path,
    timeout: float | None = None,
    check: bool = False,
    runner: CmdRunner = default_cmd_runner,
) -> subprocess.CompletedProcess[str]:
    return runner(["git", *args], cwd=cwd, timeout=timeout, check=check)


def run_gh(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    check: bool = False,
    input_data: str | None = None,
    runner: CmdRunner = default_cmd_runner,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["gh", *args],
        cwd=cwd,
        timeout=timeout,
        check=check,
        input_data=input_data,
    )
