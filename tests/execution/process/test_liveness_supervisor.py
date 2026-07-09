"""Tests for ProcessLivenessSupervisor decisions and publication semantics."""

from __future__ import annotations

import time
from typing import cast

import pytest

from autoskillit.core import (
    BackendEventKind,
    OperationLiveness,
    OperationStatus,
    SessionEvent,
    SessionLivenessSpec,
)
from autoskillit.execution.process._liveness_supervisor import ProcessLivenessSupervisor

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _spec(operation_deadline_sec: float = 14464.0) -> SessionLivenessSpec:
    return SessionLivenessSpec(
        stdout_idle_timeout_sec=600.0,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=operation_deadline_sec,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=False,
        caller_session_id="caller-1",
    )


def _liveness_event(
    op_id: str = "op-1",
    status: OperationStatus = OperationStatus.STARTED,
    item_type: str = "mcp_tool_call",
) -> SessionEvent:
    return SessionEvent(
        kind=BackendEventKind.IGNORED,
        is_terminal=False,
        has_marker=False,
        operation_liveness=OperationLiveness(
            operation_id=op_id,
            item_type=item_type,
            status=status,
        ),
    )


def test_publish_event_starts_operation() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    sup.publish_event(_liveness_event())
    assert sup.in_flight_operation()
    assert sup.in_flight_under_deadline()


def test_publish_event_completed_drops_operation() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    sup.publish_event(_liveness_event(status=OperationStatus.STARTED))
    sup.publish_event(_liveness_event(status=OperationStatus.COMPLETED))
    assert not sup.in_flight_operation()


def test_publish_event_idempotent_on_started_status() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    e = _liveness_event()
    sup.publish_event(e)
    sup.publish_event(e)
    assert len(sup.operations) == 1


def test_publish_event_unknown_status_ignored() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    op = OperationLiveness(
        operation_id="op-x",
        item_type="mcp_tool_call",
        status=cast(OperationStatus, "bogus"),
    )
    event = SessionEvent(
        kind=BackendEventKind.IGNORED,
        is_terminal=False,
        has_marker=False,
        operation_liveness=op,
    )
    sup.publish_event(event)
    assert not sup.in_flight_operation()


def test_in_flight_under_deadline_false_after_deadline() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec(operation_deadline_sec=0.1))
    sup.publish_event(_liveness_event())
    time.sleep(0.2)
    assert sup.in_flight_operation()
    assert not sup.in_flight_under_deadline()


def test_should_kill_on_stdout_idle_disabled_when_explicit() -> None:
    spec = SessionLivenessSpec(
        stdout_idle_timeout_sec=None,
        stale_threshold_sec=1200.0,
        operation_deadline_sec=14464.0,
        mcp_tool_timeout_sec=14364.0,
        wall_timeout_sec=7200.0,
        explicit_idle_disabled=True,
        caller_session_id="caller-1",
    )
    sup = ProcessLivenessSupervisor(spec=spec)
    assert not sup.should_kill_on_stdout_idle(idle_seconds=10_000.0)


def test_should_kill_on_stdout_idle_suppressed_by_in_flight() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    sup.publish_event(_liveness_event())
    assert not sup.should_kill_on_stdout_idle(idle_seconds=10_000.0)


def test_should_kill_on_stdout_idle_fires_when_no_operation() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    assert sup.should_kill_on_stdout_idle(idle_seconds=10_000.0)


def test_should_kill_on_channel_b_stale_suppressed_by_in_flight() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    sup.publish_event(_liveness_event())
    assert not sup.should_kill_on_channel_b_stale(
        stale_seconds=10_000.0,
        has_api_connection=False,
        has_child_activity=False,
        has_active_marker=False,
    )


def test_should_kill_on_channel_b_stale_fires_when_no_signals() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    assert sup.should_kill_on_channel_b_stale(
        stale_seconds=10_000.0,
        has_api_connection=False,
        has_child_activity=False,
        has_active_marker=False,
    )


def test_operation_deadline_floor_returns_inf_when_no_operations() -> None:
    sup = ProcessLivenessSupervisor(spec=_spec())
    assert sup.operation_deadline_floor() == float("inf")
