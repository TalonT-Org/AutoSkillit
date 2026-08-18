"""The config authority must reach spec construction, not merely a nearby parameter.

``force_claude_agent_teams_inactive`` was declared, ledgered, and never read by
any production site. Wiring that stops at an intermediate parameter is the same
defect wearing a different shape, so these tests assert on the objects that
actually reach the backend builders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import OutputFormat, SkillSessionConfig
from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.execution.headless._managed._launch_adapter import (
    _food_truck_launch_spec_builder,
    _skill_launch_spec_builder,
)
from tests._realistic_project import make_realistic_project

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _skill_builder_kwargs(cwd: str) -> dict[str, object]:
    return {
        "backend": ClaudeCodeBackend(),
        "skill_command": "/rectify",
        "cwd": cwd,
        "completion_marker": "DONE",
        "configured_model": None,
        "output_format": OutputFormat.JSON,
        "add_dirs": (),
        "exit_after_stop_delay_ms": 0,
        "stream_idle_timeout_ms": 0,
        "step_name": "",
        "temp_dir_relpath": ".autoskillit/temp",
        "allowed_write_prefix": "",
        "allowed_write_prefixes": (),
        "profile_name": "",
        "resume_session_id": "",
        "resume_checkpoint": None,
        "resume_message": None,
        "readonly_skill": False,
        "scope_discipline_skill": False,
        "network_access": False,
        "native_shell_capture_decision": None,
        "managed_lineage_ref": None,
    }


@pytest.mark.parametrize("force", [True, False])
def test_skill_spec_builder_threads_intent_into_the_spec(tmp_path: Path, force: bool) -> None:
    """Asserting on _run_headless_attempt's parameter is insufficient.

    That parameter feeds only the adapter digest; the spec is built by this
    closure, which routes intent through SkillSessionConfig and the builder
    keyword both.
    """
    project = make_realistic_project(tmp_path, agent_teams=None)
    build = _skill_launch_spec_builder(
        **_skill_builder_kwargs(str(project)),  # type: ignore[arg-type]
        force_inactive_agent_teams=force,
    )

    spec = build(None, None, None)

    assert spec.force_inactive_agent_teams is force


def test_skill_session_config_carries_intent() -> None:
    """The field the closure populates must exist on the config it builds."""
    config = SkillSessionConfig(force_inactive_agent_teams=True)
    assert config.force_inactive_agent_teams is True


@pytest.mark.parametrize("force", [True, False])
def test_food_truck_spec_builder_threads_intent_into_the_spec(tmp_path: Path, force: bool) -> None:
    project = make_realistic_project(tmp_path, agent_teams=None)
    build = _food_truck_launch_spec_builder(
        backend=ClaudeCodeBackend(),
        orchestrator_prompt="orchestrate",
        cwd=str(project),
        capability_preparation=None,
        completion_marker="DONE",
        resume_session_id=None,
        resume_checkpoint=None,
        configured_model=None,
        output_format=OutputFormat.STREAM_JSON,
        exit_after_stop_delay_ms=0,
        stream_idle_timeout_ms=0,
        step_name="",
        temp_dir_relpath=".autoskillit/temp",
        allowed_write_prefix="",
        allowed_write_prefixes=(),
        sentinel_contract="",
        resume_message=None,
        native_shell_capture_decision=None,
        managed_lineage_ref=None,
        force_inactive_agent_teams=force,
    )

    spec = build(None, None, None)

    assert spec.force_inactive_agent_teams is force
