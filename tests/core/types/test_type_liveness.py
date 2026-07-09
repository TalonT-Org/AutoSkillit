"""Tests for OperationLiveness and SessionLivenessSpec frozen dataclasses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from autoskillit.core import (
    LivenessSource,
    OperationLiveness,
    OperationStatus,
    SessionLivenessSpec,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_operation_liveness_is_frozen() -> None:
    op = OperationLiveness(
        operation_id="op-1",
        item_type="mcp_tool_call",
        status=OperationStatus.STARTED,
    )
    with pytest.raises(FrozenInstanceError):
        op.operation_id = "op-2"  # type: ignore[misc]


def test_operation_liveness_default_timing_fields_are_none() -> None:
    op = OperationLiveness(
        operation_id="op-1",
        item_type="mcp_tool_call",
        status=OperationStatus.PROGRESS,
    )
    assert op.started_monotonic is None
    assert op.updated_monotonic is None


def test_session_liveness_spec_is_idle_disabled_when_explicit() -> None:
    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=None,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=14464.0,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=True,
        caller_session_id="caller-1",
    )
    assert spec.is_idle_disabled


def test_session_liveness_spec_is_idle_disabled_when_timeout_none() -> None:
    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=None,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=14464.0,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=False,
        caller_session_id="caller-1",
    )
    assert spec.is_idle_disabled


def test_session_liveness_spec_is_idle_enabled_default() -> None:
    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=600.0,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=14464.0,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=False,
        caller_session_id="caller-1",
    )
    assert not spec.is_idle_disabled


def test_session_liveness_spec_default_authorized_sources_include_inflight() -> None:
    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=600.0,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=14464.0,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=False,
        caller_session_id="caller-1",
    )
    assert LivenessSource.OPERATION_IN_FLIGHT in spec.authorized_sources


def test_operation_status_members() -> None:
    assert OperationStatus.STARTED.value == "started"
    assert OperationStatus.PROGRESS.value == "progress"
    assert OperationStatus.COMPLETED.value == "completed"
    assert OperationStatus.FAILED.value == "failed"
    assert OperationStatus.CANCELLED.value == "cancelled"
