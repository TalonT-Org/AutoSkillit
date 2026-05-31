"""Cross-backend env contract tests: every build_*_cmd method must inject required env vars."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    SKILL_SESSION_REQUIRED_ENV,
    DirectInstall,
    OutputFormat,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.parametrize(
    "backend_factory",
    [ClaudeCodeBackend, CodexBackend],
    ids=["ClaudeCodeBackend", "CodexBackend"],
)
def test_skill_session_env_contains_required_vars(backend_factory) -> None:
    """Every backend's build_skill_session_cmd must inject all SKILL_SESSION_REQUIRED_ENV vars."""
    spec = backend_factory().build_skill_session_cmd(
        "/investigate foo",
        cwd="/tmp",
        completion_marker="%%DONE%%",
        model=None,
        plugin_source=None,
        output_format=OutputFormat.STREAM_JSON,
    )
    missing = SKILL_SESSION_REQUIRED_ENV - spec.env.keys()
    assert not missing, f"Missing required skill session env vars: {missing}"


@pytest.mark.parametrize(
    "backend_factory",
    [ClaudeCodeBackend, CodexBackend],
    ids=["ClaudeCodeBackend", "CodexBackend"],
)
def test_food_truck_env_contains_required_vars(backend_factory) -> None:
    """Every backend's build_food_truck_cmd must inject all ORCHESTRATOR_SESSION_REQUIRED_ENV."""
    spec = backend_factory().build_food_truck_cmd(
        orchestrator_prompt="run the pipeline",
        plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
        cwd="/tmp",
        completion_marker="%%DONE%%",
    )
    missing = ORCHESTRATOR_SESSION_REQUIRED_ENV - spec.env.keys()
    assert not missing, f"Missing required food truck env vars: {missing}"
