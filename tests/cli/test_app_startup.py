"""Every registered CLI command must at least start.

The cheap tier of the corridor net: a pre-spawn check that fires on
legitimate state breaks the CLI at startup without breaking a test. This
will not catch a policy that fires only at spawn — that is what the
unstubbed corridor tests are for — but it does catch anything that breaks
import, registration, or argument parsing, for every command, without
anyone remembering to add a test.

Commands are enumerated from the live cyclopts app, so a newly registered
command is covered the moment it is added.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.cli.app import app

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_LAUNCHER = "from autoskillit.cli import main; main()"


def _registered_command_names(application: object) -> list[str]:
    """Command names registered on a cyclopts app, excluding help/version flags."""
    return [name for name in application if not name.startswith("-")]  # type: ignore[attr-defined]


def _top_level_commands() -> list[str]:
    return sorted(set(_registered_command_names(app)))


def _hermetic_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "NO_COLOR": "1",
        }
    )
    return env


def _run_help(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, "-c", _LAUNCHER, *args, "--help"],
        cwd=project,
        env=_hermetic_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_root_help_exits_zero(tmp_path: Path) -> None:
    result = _run_help([], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "autoskillit" in (result.stdout + result.stderr).lower()


def test_command_enumeration_is_not_empty() -> None:
    """A silently empty enumeration would make every parametrized case vacuous."""
    commands = _top_level_commands()
    assert len(commands) > 5
    assert "cook" in commands
    assert "order" in commands


@pytest.mark.parametrize("command", _top_level_commands())
def test_registered_command_help_exits_zero(command: str, tmp_path: Path) -> None:
    result = _run_help([command], tmp_path)
    assert result.returncode == 0, f"{command} --help failed:\n{result.stderr}"
