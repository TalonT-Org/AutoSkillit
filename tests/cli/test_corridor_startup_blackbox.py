"""Every interactive corridor must start on a realistic developer checkout.

This is the deep tier of the corridor net, and the test that would have caught
the five consecutive fail-closed-checkpoint breakages that preceded it. Each
case launches the real CLI as a subprocess over a PTY and asserts a fake agent
binary genuinely executed — proven by the marker file it writes on exec, not by
inspecting a spec that was merely built.

Nothing is stubbed: not ``Popen``, not ``ensure_pre_launch``, not
``validate_interactive_invocation``, not ``assert_interactive_ordering``. Cook,
order, and fleet dispatch are covered separately because they do not share a
launch path — ``cook()`` has its own inline build/validate loop rather than
routing through ``_run_interactive_session``, so a test of one proves nothing
about the others.

The project carries ``.claude/settings.json`` and ``.claude/settings.local.json``
with agent teams enabled, which is ordinary developer machine state and was
precisely the state that killed every corridor at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core import atomic_write
from tests._realistic_project import (
    AGENT_TEAMS_ENV_VAR,
    launch_marker_path,
    make_realistic_project,
    write_fake_agent_binary,
)
from tests.cli._blackbox_launch import LaunchOutcome, hermetic_launch_env, run_cli_launch

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_LAUNCH_PROBE_RECIPE = """name: launch-probe
description: Hermetic interactive launch probe
summary: done
kitchen_rules:
  - Stop after the launch probe completes.
steps:
  done:
    action: stop
    message: Launch probe complete
"""

CORRIDORS = {
    "cook": ["cook"],
    "order": ["order", "launch-probe"],
    "fleet-dispatch": ["fleet", "dispatch"],
}


def _drive_corridor(
    tmp_path: Path, argv: list[str], *, exported_teams: str | None
) -> tuple[LaunchOutcome, Path, Path]:
    project = make_realistic_project(tmp_path, agent_teams="1")
    atomic_write(project / ".autoskillit" / "recipes" / "launch-probe.yaml", _LAUNCH_PROBE_RECIPE)

    roots = {
        name: tmp_path / name for name in ("home", "state", "bin", "tmp", "xc", "xa", "xd", "xs")
    }
    for directory in roots.values():
        directory.mkdir(parents=True, exist_ok=True)

    write_fake_agent_binary(roots["bin"])
    atomic_write(
        roots["home"] / ".claude" / "plugins" / "installed_plugins.json",
        '{"version": 2, "plugins": {}}\n',
    )

    extra = {"AUTOSKILLIT_FEATURES__FLEET": "true"}
    if exported_teams is not None:
        extra[AGENT_TEAMS_ENV_VAR] = exported_teams

    env = hermetic_launch_env(
        project=project,
        isolated_home=roots["home"],
        state_dir=roots["state"],
        shim_dir=roots["bin"],
        temp_dir=roots["tmp"],
        xdg_roots={
            "config": roots["xc"],
            "cache": roots["xa"],
            "data": roots["xd"],
            "state": roots["xs"],
        },
        extra=extra,
    )

    outcome = run_cli_launch(argv, cwd=project, env=env, timeout_seconds=75)
    return outcome, project, roots["state"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY launch coverage")
@pytest.mark.parametrize("corridor", sorted(CORRIDORS))
@pytest.mark.parametrize("exported_teams", [None, "1"], ids=["settings_only", "exported_env"])
def test_corridor_reaches_spawn_on_teams_enabled_project(
    tmp_path: Path, corridor: str, exported_teams: str | None
) -> None:
    """Agent teams enabled by the repository, and by the parent process env."""
    outcome, _project, state_dir = _drive_corridor(
        tmp_path, CORRIDORS[corridor], exported_teams=exported_teams
    )

    assert outcome.prompt_seen, outcome.output
    assert outcome.returncode == 0, outcome.output
    assert launch_marker_path(state_dir).is_file(), (
        f"{corridor} never reached spawn — the agent binary did not execute:\n{outcome.output}"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY launch coverage")
@pytest.mark.parametrize("corridor", sorted(CORRIDORS))
def test_corridor_leaves_repository_settings_untouched(tmp_path: Path, corridor: str) -> None:
    """Default config must not rewrite a developer's own settings files."""
    _outcome, project, _state_dir = _drive_corridor(
        tmp_path, CORRIDORS[corridor], exported_teams=None
    )

    settings_local = (project / ".claude" / "settings.local.json").read_text()
    assert AGENT_TEAMS_ENV_VAR in settings_local
    assert "AUTOSKILLIT_UNRELATED" in settings_local
