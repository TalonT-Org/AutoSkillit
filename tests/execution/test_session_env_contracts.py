"""Cross-backend env contract tests: every build_*_cmd method must inject required env vars."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    SKILL_SESSION_REQUIRED_ENV,
    OutputFormat,
)
from autoskillit.execution.backends._claude_prompt import (
    _CLAUDE_SKILL_SESSION_HARDENING,
    _HEADLESS_EXCLUSIVE_VARS,
    _SKILL_SESSION_EXTRAS_DENYLIST,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from tests.execution.backends._plugin_binding import plugin_binding
from tests.fixtures.codex import codex_skill_add_dirs

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.parametrize(
    "backend_factory",
    [ClaudeCodeBackend, CodexBackend],
    ids=["ClaudeCodeBackend", "CodexBackend"],
)
def test_skill_session_env_contains_required_vars(backend_factory) -> None:
    """Every backend's build_skill_session_cmd must inject all SKILL_SESSION_REQUIRED_ENV vars."""
    add_dirs = codex_skill_add_dirs("/tmp") if backend_factory is CodexBackend else ()
    spec = backend_factory().build_skill_session_cmd(
        "/investigate foo",
        cwd="/tmp",
        completion_marker="%%DONE%%",
        model=None,
        plugin_binding=None,
        output_format=OutputFormat.STREAM_JSON,
        add_dirs=add_dirs,
    )
    missing = SKILL_SESSION_REQUIRED_ENV - spec.env.keys()
    assert not missing, f"Missing required skill session env vars: {missing}"


def test_claude_skill_hardening_stays_backend_local() -> None:
    keys = _CLAUDE_SKILL_SESSION_HARDENING.keys()
    assert set(keys) == {
        "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",
        "CLAUDE_CODE_DISABLE_CRON",
    }
    assert keys.isdisjoint(SKILL_SESSION_REQUIRED_ENV)
    assert keys.isdisjoint(_SKILL_SESSION_EXTRAS_DENYLIST)
    assert keys.isdisjoint(_HEADLESS_EXCLUSIVE_VARS)
    codex_env = (
        CodexBackend()
        .build_skill_session_cmd(
            "/investigate foo",
            cwd="/tmp",
            completion_marker="%%DONE%%",
            add_dirs=codex_skill_add_dirs("/tmp"),
        )
        .env
    )
    assert keys.isdisjoint(codex_env)


@pytest.mark.parametrize(
    "backend_factory",
    [ClaudeCodeBackend, CodexBackend],
    ids=["ClaudeCodeBackend", "CodexBackend"],
)
def test_food_truck_env_contains_required_vars(backend_factory) -> None:
    """Every backend's build_food_truck_cmd must inject all ORCHESTRATOR_SESSION_REQUIRED_ENV."""
    managed_skill_catalog = (
        codex_skill_add_dirs("/tmp")[0] if backend_factory is CodexBackend else None
    )
    with plugin_binding(Path("/plugins")) as binding:
        spec = backend_factory().build_food_truck_cmd(
            orchestrator_prompt="run the pipeline",
            plugin_binding=binding,
            cwd="/tmp",
            completion_marker="%%DONE%%",
            managed_skill_catalog=managed_skill_catalog,
        )
    missing = ORCHESTRATOR_SESSION_REQUIRED_ENV - spec.env.keys()
    assert not missing, f"Missing required food truck env vars: {missing}"
