"""Tests for ToolContext dependency injection container."""

from __future__ import annotations

from autoskillit._audit import AuditLog, FailureRecord
from autoskillit._context import ToolContext
from autoskillit._gate import GateState
from autoskillit._token_log import TokenLog
from autoskillit.config import AutomationConfig


def test_tool_context_fields_accessible(tmp_path):
    """ToolContext exposes all expected fields."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(enabled=True),
        plugin_dir=str(tmp_path),
        runner=None,
    )
    assert ctx.gate.enabled is True
    assert ctx.plugin_dir == str(tmp_path)


def test_tool_context_audit_isolation():
    """Two ToolContext instances have independent AuditLog instances."""
    ctx_a = ToolContext(
        config=AutomationConfig(),
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(),
        plugin_dir="/a",
        runner=None,
    )
    ctx_b = ToolContext(
        config=AutomationConfig(),
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(),
        plugin_dir="/b",
        runner=None,
    )
    ctx_a.audit.record_failure(
        FailureRecord(
            timestamp="2026-01-01T00:00:00",
            skill_command="/test",
            exit_code=1,
            subtype="error",
            needs_retry=False,
            retry_reason="none",
            stderr="",
        )
    )
    assert len(ctx_a.audit.get_report()) == 1
    assert len(ctx_b.audit.get_report()) == 0


def test_gate_state_replacement():
    """GateState (frozen) can be replaced on a mutable ToolContext."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=AuditLog(),
        token_log=TokenLog(),
        gate=GateState(enabled=False),
        plugin_dir="/x",
        runner=None,
    )
    assert ctx.gate.enabled is False
    ctx.gate = GateState(enabled=True)
    assert ctx.gate.enabled is True
