"""Per-process liveness supervisor shared across watcher coroutines.

The supervisor owns the canonical per-session liveness state that
``_heartbeat``, ``_watch_stdout_idle``, ``_session_log_monitor``, and
``_watch_child_activity`` consult. Each of those watchers used to make
independent kill/extension decisions based on a stale view of the same
underlying signals — for backends whose legitimate operations are
stdout-silent (Codex L2 food trucks running MCP round-trips), this race
allowed the outer idle watchdog to fire while a healthy operation was
in-flight.

The supervisor fixes that: every parsed ``SessionEvent`` is published to
the supervisor; every watcher asks for decisions instead of interpreting
signals. Publication is keyed by backend operation id, so duplicate
parser consumers (e.g. ``_extract_stdout_session_id`` and ``_heartbeat``)
cannot double-count lifecycle records.

Construction is per-attempt: ``_execute_claude_headless()`` builds a fresh
``ProcessLivenessSupervisor`` at the top of each ``while True`` provider
fallback iteration, so in-flight operations from attempt 1 cannot
survive into attempt 2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from autoskillit.core import (
    LivenessSource,
    OperationStatus,
    SessionLivenessSpec,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import SessionEvent

logger = get_logger(__name__)


@dataclass
class _OperationState:
    """Per-operation mutable tracking state."""

    item_type: str
    started_monotonic: float
    updated_monotonic: float | None = None
    last_status: str = OperationStatus.STARTED


@dataclass
class ProcessLivenessSupervisor:
    """Shared mutable liveness state used by all process watchers.

    Decides whether a byte-idle kill, Channel-B stale kill, or
    wall-deadline extension is allowed based on the in-flight operation
    set and the resolved liveness contract. Construction is
    per-session-attempt so the state cannot leak across provider
    fallback attempts.
    """

    spec: SessionLivenessSpec
    operations: dict[str, _OperationState] = field(default_factory=dict)
    last_stdout_growth_monotonic: float = field(default_factory=time.monotonic)
    last_channel_b_growth_monotonic: float | None = None

    def publish_event(self, event: SessionEvent) -> None:
        """Publish a parsed ``SessionEvent`` to the supervisor.

        Only ``event.operation_liveness`` is acted upon — every other
        field is propagated unchanged to the caller. Publication is
        idempotent by ``(operation_id, status)``: replaying the same
        lifecycle record does not double-count or extend deadlines.
        """
        op_liveness = event.operation_liveness
        if op_liveness is None:
            return
        status = op_liveness.status
        if status not in (
            OperationStatus.STARTED,
            OperationStatus.PROGRESS,
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        ):
            return
        if status in (
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        ):
            self.operations.pop(op_liveness.operation_id, None)
            return
        state = self.operations.get(op_liveness.operation_id)
        now = time.monotonic()
        started = op_liveness.started_monotonic or (state.started_monotonic if state else now)
        if state is None:
            state = _OperationState(
                item_type=op_liveness.item_type,
                started_monotonic=started,
                updated_monotonic=op_liveness.updated_monotonic or now,
                last_status=status,
            )
            self.operations[op_liveness.operation_id] = state
        else:
            state.updated_monotonic = op_liveness.updated_monotonic or now
            state.last_status = status

    def record_stdout_growth(self) -> None:
        self.last_stdout_growth_monotonic = time.monotonic()

    def record_channel_b_growth(self) -> None:
        self.last_channel_b_growth_monotonic = time.monotonic()

    def in_flight_operation(self) -> bool:
        return bool(self.operations)

    def in_flight_under_deadline(self) -> bool:
        """Whether at least one in-flight operation is still within its
        operation deadline window.

        Returns False when no operation is in-flight OR every in-flight
        operation has exceeded ``spec.operation_deadline_sec`` since it
        was started. Watchers SHOULD consult this signal in preference
        to byte silence.
        """
        if not self.operations:
            return False
        now = time.monotonic()
        for state in self.operations.values():
            if now - state.started_monotonic < self.spec.operation_deadline_sec:
                return True
        return False

    def should_kill_on_stdout_idle(self, idle_seconds: float) -> bool:
        """Whether the outer stdout-idle watchdog should fire now.

        Returns False (do NOT kill) when:
        - the spec disables the outer idle watchdog entirely; or
        - at least one in-flight operation is under its deadline; or
        - the configured idle threshold has not elapsed.
        """
        if self.spec.is_idle_disabled:
            return False
        threshold = self.spec.stdout_idle_timeout_sec
        if threshold is None:
            return False
        if idle_seconds < threshold:
            return False
        if self.in_flight_under_deadline():
            return False
        return True

    def should_kill_on_channel_b_stale(
        self,
        stale_seconds: float,
        *,
        has_api_connection: bool,
        has_child_activity: bool,
        has_active_marker: bool,
    ) -> bool:
        """Whether the Channel-B stale watchdog should fire now.

        Centralizes the precedence order previously hard-wired into
        ``_session_log_monitor``: an in-flight operation under its
        deadline suppresses stale, even when none of the marker/API/CPU
        predicates fire. Existing ``max_suppression_seconds`` bounds are
        enforced by callers before invoking this method — once a
        suppression window has expired under the legacy code, this
        method returns True unconditionally.
        """
        if self.spec.is_idle_disabled:
            return False
        threshold = self.spec.stale_threshold_sec
        if stale_seconds < threshold:
            return False
        if self.in_flight_under_deadline():
            return False
        if has_api_connection or has_child_activity or has_active_marker:
            return False
        return True

    def operation_deadline_floor(self) -> float:
        """Earliest wall-clock deadline an in-flight operation permits.

        Returns the wall-clock cap derived from any in-flight operation
        that is still under its deadline. Callers use this to clamp
        ``_watch_child_activity``'s deadline extension so it cannot push
        ``scope.deadline`` past an operation's own legitimate ceiling.
        """
        if not self.operations:
            return float("inf")
        now = time.monotonic()
        deadline = float("inf")
        for state in self.operations.values():
            op_deadline = state.started_monotonic + self.spec.operation_deadline_sec
            remaining = op_deadline - now
            if remaining < deadline:
                deadline = remaining
        return deadline

    def authorized_sources(self) -> frozenset[LivenessSource]:
        return self.spec.authorized_sources
