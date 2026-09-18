"""Black-box coverage for the real ``autoskillit order`` launch boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import atomic_write
from tests._realistic_project import (
    AGENT_TEAMS_ENV_VAR,
    launch_marker_path,
    make_realistic_project,
    write_fake_agent_binary,
)
from tests.cli._blackbox_launch import hermetic_launch_env, run_cli_launch

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _git_status(worktree: Path) -> bytes:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _write_fixture(project: Path, isolated_home: Path, shim_dir: Path) -> None:
    atomic_write(
        project / ".autoskillit" / "recipes" / "launch-probe.yaml",
        """name: launch-probe
description: Hermetic interactive launch probe
summary: done
kitchen_rules:
  - Stop after the launch probe completes.
steps:
  done:
    action: stop
    message: Launch probe complete
""",
    )
    # A project with no .claude/settings*.json is not a machine anyone has, and
    # that specific absence is what let an ungated settings policy ship green.
    make_realistic_project(project.parent, agent_teams="1", project_dir=project)
    atomic_write(
        isolated_home / ".claude" / "plugins" / "installed_plugins.json",
        '{"version": 2, "plugins": {}}\n',
    )
    write_fake_agent_binary(shim_dir)


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY launch coverage")
def test_order_launches_real_cli_without_host_side_effects(tmp_path: Path) -> None:
    worktree = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    isolated_home = tmp_path / "home"
    state_dir = tmp_path / "state"
    shim_dir = tmp_path / "bin"
    temp_dir = tmp_path / "tmp"
    xdg_roots = {
        "config": tmp_path / "xdg-config",
        "cache": tmp_path / "xdg-cache",
        "data": tmp_path / "xdg-data",
        "state": tmp_path / "xdg-state",
    }
    for directory in (
        project,
        isolated_home,
        state_dir,
        shim_dir,
        temp_dir,
        *xdg_roots.values(),
    ):
        directory.mkdir(parents=True)
    _write_fixture(project, isolated_home, shim_dir)

    environment = hermetic_launch_env(
        project=project,
        isolated_home=isolated_home,
        state_dir=state_dir,
        shim_dir=shim_dir,
        temp_dir=temp_dir,
        xdg_roots=xdg_roots,
    )
    status_before = _git_status(worktree)

    outcome = run_cli_launch(
        ["order", "launch-probe"],
        cwd=project,
        env=environment,
    )

    assert outcome.prompt_seen, outcome.output
    assert outcome.returncode == 0, outcome.output
    assert launch_marker_path(state_dir).is_file(), outcome.output
    expected_artifacts = (
        project / ".autoskillit" / "temp",
        isolated_home / ".autoskillit" / "plugin-projections",
        isolated_home / ".claude" / "plugins" / "installed_plugins.json",
        launch_marker_path(state_dir),
    )
    allowed_roots = (project.resolve(), isolated_home.resolve(), state_dir.resolve())
    for artifact in expected_artifacts:
        assert artifact.exists(), (artifact, outcome.output)
        assert any(artifact.resolve().is_relative_to(root) for root in allowed_roots)

    assert _git_status(worktree) == status_before
    # Default config must not rewrite the developer's own settings.
    assert AGENT_TEAMS_ENV_VAR in (project / ".claude" / "settings.local.json").read_text()
