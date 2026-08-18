"""Byte-for-byte preservation test: force_inactive_agent_teams=False default.

Per Plan § Step 2.5 (REQ-EXTRACT-054), with the option disabled (default
False), the launcher produces argv/env identical to the pre-existing
behavior. This guards against accidental argv/env mutation that would
silently change every team's Claude launch.

The test runs the four builder paths and compares their output against
the same builder invoked with force_inactive_agent_teams=False.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_build_headless_default_matches_no_force() -> None:
    backend = ClaudeCodeBackend()
    default = backend.build_headless_cmd("hello")
    explicit = backend.build_headless_cmd("hello", force_inactive_agent_teams=False)
    assert default.cmd == explicit.cmd
    assert default.env == explicit.env


def test_build_skill_session_default_matches_no_force() -> None:
    backend = ClaudeCodeBackend()
    default = backend.build_skill_session_cmd("/test")
    explicit = backend.build_skill_session_cmd("/test", force_inactive_agent_teams=False)
    assert default.cmd == explicit.cmd
    assert default.env == explicit.env


def test_build_food_truck_default_matches_no_force() -> None:
    backend = ClaudeCodeBackend()
    default = backend.build_food_truck_cmd(
        orchestrator_prompt="orchestrate",
        plugin_binding=None,
        cwd="/tmp",
        completion_marker="DONE",
    )
    explicit = backend.build_food_truck_cmd(
        orchestrator_prompt="orchestrate",
        plugin_binding=None,
        cwd="/tmp",
        completion_marker="DONE",
        force_inactive_agent_teams=False,
    )
    assert default.cmd == explicit.cmd
    assert default.env == explicit.env


def test_build_resume_default_matches_no_force() -> None:
    backend = ClaudeCodeBackend()
    default = backend.build_resume_cmd(resume_session_id="abc", prompt="resume")
    explicit = backend.build_resume_cmd(
        resume_session_id="abc",
        prompt="resume",
        force_inactive_agent_teams=False,
    )
    assert default.cmd == explicit.cmd
    assert default.env == explicit.env


def test_build_interactive_default_matches_no_force() -> None:
    backend = ClaudeCodeBackend()
    default = backend.build_interactive_cmd()
    explicit = backend.build_interactive_cmd(force_inactive_agent_teams=False)
    assert default.cmd == explicit.cmd
    assert default.env == explicit.env


def test_force_inactive_strips_env_var() -> None:
    """When force_inactive_agent_teams=True, the env var must be removed."""
    backend = ClaudeCodeBackend()
    forced = backend.build_headless_cmd(
        "hello",
        force_inactive_agent_teams=True,
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        project_root="/tmp",
    )
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in forced.env


def test_force_inactive_without_project_root_refuses() -> None:
    """REQ-B28: refuse headless launches that pass force_inactive_agent_teams=True
    without a project_root — the settings file scan cannot confirm inactivity."""
    backend = ClaudeCodeBackend()
    with pytest.raises(RuntimeError, match="project_root"):
        backend.build_headless_cmd(
            "hello",
            force_inactive_agent_teams=True,
            env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        )


def test_force_inactive_interactive_without_project_root_refuses() -> None:
    """REQ-B28 (build_interactive_cmd): the same fail-closed guard must
    apply on the interactive corridor — the settings file scan cannot
    confirm inactivity without a project_root."""
    backend = ClaudeCodeBackend()
    with pytest.raises(RuntimeError, match="project_root"):
        backend.build_interactive_cmd(
            initial_prompt="hello",
            force_inactive_agent_teams=True,
            env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        )


def _neutralized_launch_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ClaudeCodeBackend, Path]:
    """A teams-enabled process env, a resolvable fake binary, and a project."""
    from autoskillit.core import atomic_write

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "claude"
    atomic_write(executable, "#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
    return ClaudeCodeBackend(), project


def test_force_inactive_interactive_accepts_binding_from_neutralized_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive confirmation, not blanket refusal.

    The constraint is that the binding be resolved *from* the neutralized env,
    which is exactly what the equality check proves. A blanket refusal made the
    opt-in unreachable through the probe flow, where the binding is derived
    from the neutralized env by construction.
    """
    from autoskillit.core.runtime.executable_binding import (
        resolve_executable_launch_binding,
    )

    backend, project = _neutralized_launch_fixture(tmp_path, monkeypatch)

    env_spec = backend.build_interactive_cmd(
        initial_prompt="hello",
        force_inactive_agent_teams=True,
        project_root=str(project),
    )
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env_spec.env

    binding = resolve_executable_launch_binding(
        binary_name="claude",
        environment=env_spec.env,
        cwd=project,
    )
    spec = backend.build_interactive_cmd(
        initial_prompt="hello",
        executable=binding,
        force_inactive_agent_teams=True,
        project_root=str(project),
    )

    assert spec.force_inactive_agent_teams is True
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in spec.env


def test_force_inactive_interactive_rejects_stale_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binding captured before neutralization must still be refused."""
    import os

    from autoskillit.core.runtime.executable_binding import (
        resolve_executable_launch_binding,
    )

    backend, project = _neutralized_launch_fixture(tmp_path, monkeypatch)

    stale = resolve_executable_launch_binding(
        binary_name="claude",
        environment=dict(os.environ),
        cwd=project,
    )
    assert stale.launch_environment.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1"

    with pytest.raises(ValueError, match="environment changed after executable binding"):
        backend.build_interactive_cmd(
            initial_prompt="hello",
            executable=stale,
            force_inactive_agent_teams=True,
            project_root=str(project),
        )
