"""Shared fixtures for tests/server/."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from autoskillit.pipeline.timings import DefaultTimingLog


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    """Reset module-level _ctx in server._state after each test.

    Tests that call _initialize() directly set _state._ctx to a mock without
    cleanup. Subsequent tests in the same xdist worker then find a stale mock
    _ctx, causing _apply_triage_gate to await a regular MagicMock and fail.

    monkeypatch records the current value before yield and restores it after,
    giving each test a clean slate regardless of what _initialize() sets.
    """
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", _state._ctx)


@pytest.fixture(autouse=True)
def _reset_mcp_tags():
    """Reset MCP tag visibility to default (kitchen disabled) before each test.

    The mcp singleton is process-global. Each mcp.enable()/disable() call appends
    a Visibility transform to an internal list — the list never shrinks. Over a
    full test suite (11k+ tests), thousands of accumulated transforms can cause
    version-dependent ordering issues in FastMCP's "last match wins" evaluation.

    Fix: truncate the transforms list back to its fresh state, then explicitly
    disable all gated tags — matching the server/__init__.py import-time baseline
    and preventing orchestrator-path tests from leaking fleet-dispatch or
    kitchen-core enables into subsequent fleet visibility tests.
    """
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


@pytest.fixture()
def kitchen_enabled():
    """Enable the kitchen tag on the MCP server for the duration of the test."""
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    mcp.enable(tags={"kitchen"})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


@pytest.fixture()
def headless_enabled():
    """Enable the headless tag on the MCP server for the duration of the test."""
    from autoskillit.core import ALL_VISIBILITY_TAGS
    from autoskillit.server import mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})
    mcp.enable(tags={"headless"})
    yield
    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})


def assert_step_timed(timing_log: DefaultTimingLog, step_name: str) -> None:
    assert any(e["step_name"] == step_name for e in timing_log.get_report())


def assert_no_timing(timing_log: DefaultTimingLog) -> None:
    assert timing_log.get_report() == []


def _make_mock_ctx() -> MagicMock:
    """Return a minimal mock ToolContext with a gate."""
    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    ctx.config.subsets.disabled = []  # REQ-VIS-008: no subsets disabled by default
    return ctx


_SUCCESS_JSON = (
    '{"type": "result", "subtype": "success", "is_error": false,'
    ' "result": "done", "session_id": "s1"}'
)


@pytest.fixture
def build_ctx(tmp_path):
    """Factory: build_ctx(**overrides) → minimal ToolContext with overrides applied."""
    from autoskillit.config.settings import AutomationConfig
    from autoskillit.core.types import DirectInstall
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog

    def _factory(**overrides):
        ctx = ToolContext(
            config=AutomationConfig(features={"fleet": True}),
            audit=DefaultAuditLog(),
            token_log=DefaultTokenLog(),
            timing_log=DefaultTimingLog(),
            gate=DefaultGateState(enabled=True),
            plugin_source=DirectInstall(plugin_dir=tmp_path),
            runner=None,
            temp_dir=tmp_path / ".autoskillit" / "temp",
            project_dir=tmp_path,
        )
        for field_name, value in overrides.items():
            setattr(ctx, field_name, value)
        return ctx

    return _factory
