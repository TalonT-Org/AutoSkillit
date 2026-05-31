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
from autoskillit.execution.backends._claude_prompt import _HEADLESS_EXCLUSIVE_VARS
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


def test_resume_cmd_env_uses_filtered_base(monkeypatch) -> None:
    """CodexBackend.build_resume_cmd must NOT pass _HEADLESS_EXCLUSIVE_VARS through."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
    spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
    leaking = _HEADLESS_EXCLUSIVE_VARS & spec.env.keys()
    assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into resume cmd env: {leaking}"


def test_resume_cmd_has_sandbox_flag() -> None:
    """CodexBackend.build_resume_cmd must include the --sandbox flag."""
    spec = CodexBackend().build_resume_cmd(resume_session_id="abc123", prompt="continue")
    assert "--sandbox" in spec.cmd


def test_headless_cmd_uses_filtered_base(monkeypatch) -> None:
    """CodexBackend.build_headless_cmd must NOT pass _HEADLESS_EXCLUSIVE_VARS through."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaked")
    spec = CodexBackend().build_headless_cmd("do stuff")
    leaking = _HEADLESS_EXCLUSIVE_VARS & spec.env.keys()
    assert not leaking, f"_HEADLESS_EXCLUSIVE_VARS leaked into headless cmd env: {leaking}"
