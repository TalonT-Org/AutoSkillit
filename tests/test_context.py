"""Tests for ToolContext dependency injection container."""

from __future__ import annotations

from autoskillit.config import AutomationConfig
from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.pipeline.tokens import DefaultTokenLog


def test_tool_context_fields_accessible(tmp_path):
    """ToolContext exposes all expected fields."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        gate=DefaultGateState(enabled=True),
        plugin_dir=str(tmp_path),
        runner=None,
    )
    assert ctx.gate.enabled is True
    assert ctx.plugin_dir == str(tmp_path)


def test_tool_context_audit_isolation():
    """Two ToolContext instances have independent AuditLog instances."""
    ctx_a = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        gate=DefaultGateState(),
        plugin_dir="/a",
        runner=None,
    )
    ctx_b = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        gate=DefaultGateState(),
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
    """DefaultGateState (frozen) can be replaced on a mutable ToolContext."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        gate=DefaultGateState(enabled=False),
        plugin_dir="/x",
        runner=None,
    )
    assert ctx.gate.enabled is False
    ctx.gate = DefaultGateState(enabled=True)
    assert ctx.gate.enabled is True


def test_toolcontext_new_optional_fields_default_none(tmp_path):
    """New optional service fields default to None when not provided."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        gate=DefaultGateState(enabled=True),
        plugin_dir=str(tmp_path),
        runner=None,
    )
    assert ctx.executor is None
    assert ctx.tester is None
    assert ctx.recipes is None
    assert ctx.migrations is None
    assert ctx.db_reader is None
    assert ctx.workspace_mgr is None


def test_toolcontext_service_fields_annotated_with_protocols():
    """Service fields reference Protocol type names, not concrete class names."""
    fields = ToolContext.__dataclass_fields__
    assert "AuditStore" in str(fields["audit"].type)
    assert "GatePolicy" in str(fields["gate"].type)
    assert "TokenStore" in str(fields["token_log"].type)
    assert "HeadlessExecutor" in str(fields["executor"].type)
    assert "TestRunner" in str(fields["tester"].type)
    assert "RecipeRepository" in str(fields["recipes"].type)
    assert "MigrationService" in str(fields["migrations"].type)
    assert "DatabaseReader" in str(fields["db_reader"].type)
    assert "WorkspaceManager" in str(fields["workspace_mgr"].type)
    # Verify concrete class names are NOT used for service fields
    assert "DefaultAuditLog" not in str(fields["audit"].type)
    assert "DefaultGateState" not in str(fields["gate"].type)
    assert "DefaultTokenLog" not in str(fields["token_log"].type)


def test_headless_executor_protocol_accepts_timeout() -> None:
    """HeadlessExecutor.run() signature must include optional timeout and stale_threshold."""
    import inspect

    from autoskillit.core import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    params = sig.parameters
    assert "timeout" in params, "HeadlessExecutor.run missing timeout param"
    assert "stale_threshold" in params, "HeadlessExecutor.run missing stale_threshold param"
    # Both must be keyword-only with None default
    assert params["timeout"].default is None
    assert params["stale_threshold"].default is None


def test_recipe_repository_protocol_has_rich_methods() -> None:
    """RecipeRepository protocol must expose load_and_validate, validate_from_path, list_all."""
    from autoskillit.core import RecipeRepository

    for method in ("load_and_validate", "validate_from_path", "list_all"):
        assert hasattr(RecipeRepository, method), f"RecipeRepository missing {method}"
