"""Shared helpers for the _session_log_monitor test family.

Centralizes ``_liveness_spec()`` and ``_operation_event()`` constructors that
were previously duplicated across three test files
(``test_process_session_log_monitor.py``,
``test_process_session_log_monitor_dispatch_marker.py``,
``test_process_session_log_monitor_stale_suppression.py``).

Keeping these in one module prevents drift when ``SessionLivenessSpec`` or
``SessionEvent`` gain new required fields.
"""

from __future__ import annotations

from autoskillit.core import (
    BackendEventKind,
    OperationLiveness,
    OperationStatus,
    SessionEvent,
    SessionLivenessSpec,
)
from autoskillit.execution.process._liveness_supervisor import ProcessLivenessSupervisor

__all__ = [
    "build_liveness_spec",
    "build_operation_event",
    "supervisor_with_inflight_operation",
]


def build_liveness_spec(
    *,
    stdout_idle_timeout_sec: float = 0.05,
    stale_threshold_sec: float = 0.05,
    operation_deadline_sec: float = 10.0,
    mcp_tool_timeout_sec: float = 10.0,
    wall_timeout_sec: float = 30.0,
    explicit_idle_disabled: bool = False,
    caller_session_id: str = "caller-session",
) -> SessionLivenessSpec:
    """Return a ``SessionLivenessSpec`` configured for short-timeout tests.

    Defaults match the test_process_session_log_monitor_dispatch_marker /
    test_process_session_log_monitor_stale_suppression expectations. The
    monitor-suppresses-stale test (which uses production-like timeouts)
    passes explicit kwargs to override the defaults.
    """
    return SessionLivenessSpec(
        stdout_idle_timeout_sec=stdout_idle_timeout_sec,
        stale_threshold_sec=stale_threshold_sec,
        operation_deadline_sec=operation_deadline_sec,
        mcp_tool_timeout_sec=mcp_tool_timeout_sec,
        wall_timeout_sec=wall_timeout_sec,
        explicit_idle_disabled=explicit_idle_disabled,
        caller_session_id=caller_session_id,
    )


def build_operation_event(
    *,
    status: OperationStatus = OperationStatus.STARTED,
    operation_id: str = "call-1",
    item_type: str = "mcp_tool_call",
) -> SessionEvent:
    """Return a ``SessionEvent`` carrying a STARTED ``OperationLiveness`` payload."""
    return SessionEvent(
        kind=BackendEventKind.IGNORED,
        is_terminal=False,
        has_marker=False,
        operation_liveness=OperationLiveness(
            operation_id=operation_id,
            item_type=item_type,
            status=status,
        ),
    )


def supervisor_with_inflight_operation(
    *,
    spec: SessionLivenessSpec | None = None,
) -> ProcessLivenessSupervisor:
    """Return a ``ProcessLivenessSupervisor`` with one in-flight operation published."""
    supervisor = ProcessLivenessSupervisor(spec=spec or build_liveness_spec())
    supervisor.publish_event(build_operation_event())
    return supervisor
