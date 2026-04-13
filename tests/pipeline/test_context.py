"""Tests for ToolContext dependency injection container."""

from __future__ import annotations

from pathlib import Path
from typing import get_args, get_type_hints

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import GitHubFetcher
from autoskillit.pipeline.audit import DefaultAuditLog, FailureRecord
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.pipeline.timings import DefaultTimingLog
from autoskillit.pipeline.tokens import DefaultTokenLog


def test_tool_context_fields_accessible(tmp_path):
    """ToolContext exposes all expected fields."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
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
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_dir="/a",
        runner=None,
    )
    ctx_b = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
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
    """ToolContext allows gate field replacement via plain assignment."""
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
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
        timing_log=DefaultTimingLog(),
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
    assert ctx.clone_mgr is None
    assert ctx.github_client is None


def test_toolcontext_optional_fields_all_have_protocol_annotations() -> None:
    """Every field(default=None) on ToolContext must be annotated with a Protocol from core.

    Self-closing: automatically discovers new optional fields without requiring manual
    updates to this test. If a new service field is added to ToolContext with the wrong
    type (e.g. a concrete class), or if it lacks any recognized Protocol annotation,
    this test fails immediately.
    """
    import inspect

    from autoskillit.core import types as core_types

    # Discover all Protocol class names defined in core/types.py
    core_protocol_names = {
        name
        for name, obj in inspect.getmembers(core_types, inspect.isclass)
        if any("Protocol" in str(b) for b in getattr(obj, "__mro__", [])[1:])
        and name != "Protocol"
    }

    # All optional service fields — exactly those declared with field(default=None)
    optional_fields = {
        name: f for name, f in ToolContext.__dataclass_fields__.items() if f.default is None
    }

    violations: list[str] = []
    for field_name, field_obj in optional_fields.items():
        annotation_str = str(field_obj.type)
        if not any(proto in annotation_str for proto in core_protocol_names):
            violations.append(
                f"ToolContext.{field_name}: annotation '{annotation_str}' contains no "
                f"Protocol from core/types.py (known protocols: {sorted(core_protocol_names)})"
            )

    assert not violations, (
        "Optional ToolContext fields must be annotated with core Protocols:\n"
        + "\n".join(violations)
    )


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


def test_headless_executor_protocol_accepts_idle_output_timeout() -> None:
    """HeadlessExecutor.run() signature must include optional idle_output_timeout."""
    import inspect

    from autoskillit.core import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    params = sig.parameters
    assert "idle_output_timeout" in params, (
        "HeadlessExecutor.run missing idle_output_timeout param"
    )
    assert params["idle_output_timeout"].default is None


def test_recipe_repository_protocol_has_rich_methods() -> None:
    """RecipeRepository protocol must expose load_and_validate, validate_from_path, list_all."""
    from autoskillit.core import RecipeRepository

    for method in ("load_and_validate", "validate_from_path", "list_all"):
        assert hasattr(RecipeRepository, method), f"RecipeRepository missing {method}"


def _make_ctx(tmp_path: Path) -> ToolContext:
    """Helper: minimal ToolContext with no optional fields."""
    plugin_dir = str(tmp_path)
    return ToolContext(
        config=AutomationConfig(),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=True),
        plugin_dir=plugin_dir,
        runner=None,
    )


def test_toolcontext_github_client_annotated_with_protocol():
    """github_client annotation must reference GitHubFetcher protocol."""
    hints = get_type_hints(ToolContext)
    assert GitHubFetcher in get_args(hints["github_client"])


def test_toolcontext_response_log_annotated_with_mcp_response_store_protocol() -> None:
    """ToolContext.response_log must be annotated with the McpResponseLog protocol.

    response_log uses default_factory (Null Object pattern) not field(default=None),
    so it is excluded from test_toolcontext_optional_fields_all_have_protocol_annotations.
    This test closes that coverage gap.
    """
    from typing import get_type_hints

    from autoskillit.core import McpResponseLog

    hints = get_type_hints(ToolContext)
    assert "response_log" in hints, "ToolContext must have a response_log field"
    assert hints["response_log"] is McpResponseLog, (
        f"ToolContext.response_log must be annotated with McpResponseLog protocol, "
        f"got: {hints['response_log']!r}"
    )


def test_tool_context_has_timing_log_field(tmp_path):
    """ToolContext.timing_log is a non-None TimingLog instance."""
    from autoskillit.core import TimingLog

    ctx = _make_ctx(tmp_path)
    assert ctx.timing_log is not None
    assert isinstance(ctx.timing_log, TimingLog)


@pytest.mark.anyio
async def test_toolcontext_default_background_wired_with_audit(tmp_path):
    """ToolContext background supervisor records failures to ctx.audit."""
    from autoskillit.pipeline.background import DefaultBackgroundSupervisor

    audit = DefaultAuditLog()
    ctx = ToolContext(
        config=AutomationConfig(),
        audit=audit,
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(),
        plugin_dir=str(tmp_path),
        runner=None,
    )
    assert isinstance(ctx.background, DefaultBackgroundSupervisor)

    async def _fail() -> None:
        raise RuntimeError("deliberate test failure")

    ctx.background.submit(_fail(), label="test-task")
    await ctx.background.drain()

    records = audit.get_report()
    assert any(r.subtype == "background_exception" for r in records)
