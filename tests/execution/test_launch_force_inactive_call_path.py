"""Call-path test for force_inactive_agent_teams.

Per Plan § Step 2.5 (REQ-EXTRACT-052), the option must reach every distinct
Claude launch builder path. This test enumerates the builders and asserts
that the option is honored when set to True.
"""

from __future__ import annotations

import pytest

from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _headless_stripped(force: bool) -> bool:
    backend = ClaudeCodeBackend()
    spec = backend.build_headless_cmd(
        "hello",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
    )
    return "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in spec.env


def _skill_session_stripped(force: bool) -> bool:
    backend = ClaudeCodeBackend()
    spec = backend.build_skill_session_cmd(
        "/test",
        force_inactive_agent_teams=force,
    )
    return "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in spec.env


def _food_truck_stripped(force: bool) -> bool:
    backend = ClaudeCodeBackend()
    spec = backend.build_food_truck_cmd(
        orchestrator_prompt="orchestrate",
        plugin_binding=None,
        cwd="/tmp",
        completion_marker="DONE",
        force_inactive_agent_teams=force,
    )
    return "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in spec.env


def _resume_stripped(force: bool) -> bool:
    backend = ClaudeCodeBackend()
    spec = backend.build_resume_cmd(
        resume_session_id="abc",
        prompt="resume",
        env_extras={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        force_inactive_agent_teams=force,
    )
    return "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in spec.env


@pytest.mark.parametrize(
    "builder_name,builder_fn",
    [
        ("build_headless_cmd", _headless_stripped),
        ("build_skill_session_cmd", _skill_session_stripped),
        ("build_food_truck_cmd", _food_truck_stripped),
        ("build_resume_cmd", _resume_stripped),
    ],
)
def test_force_inactive_false_keeps_env_var(builder_name: str, builder_fn) -> None:
    """With force=False, the option does not strip the env var."""
    assert builder_fn(False) is False


def test_force_inactive_true_strips_in_every_path() -> None:
    """With force=True, every builder must strip the env var."""
    assert _headless_stripped(True) is True
    assert _skill_session_stripped(True) is True
    assert _food_truck_stripped(True) is True
    assert _resume_stripped(True) is True
