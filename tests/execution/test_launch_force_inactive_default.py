"""Byte-for-byte preservation test: force_inactive_agent_teams=False default.

Per Plan § Step 2.5 (REQ-EXTRACT-054), with the option disabled (default
False), the launcher produces argv/env identical to the pre-existing
behavior. This guards against accidental argv/env mutation that would
silently change every team's Claude launch.

The test runs the four builder paths and compares their output against
the same builder invoked with force_inactive_agent_teams=False.
"""

from __future__ import annotations

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
    import pytest

    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    backend = ClaudeCodeBackend()
    with pytest.raises(RuntimeError, match="project_root"):
        backend.build_headless_cmd(
            "hello",
            force_inactive_agent_teams=True,
            env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        )
