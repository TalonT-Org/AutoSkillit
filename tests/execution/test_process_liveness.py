"""Unit tests for src/autoskillit/execution/process/_process_liveness.py.

The plan (rectify_codex_l2_attempt_liveness) introduces an
``OperationLedger`` and ``LivenessCoordinator`` as the single writer and
sole decision authority for typed-operation liveness. These tests pin the
FSM table and the snapshot invariants so a future refactor cannot
silently re-introduce the multiple-watcher / no-shared-decision state
that caused the original Codex L2 idle-in-MCP bug.
"""

from __future__ import annotations

import time
from types import MappingProxyType

import pytest

from autoskillit.core import OperationObservation
from autoskillit.execution.process._process_liveness import (
    AttemptRuntime,
    LivenessCoordinator,
    LivenessSnapshot,
    OperationLedger,
    make_ledger_and_coordinator,
    operation_observation_from_codex,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _obs(op_id: str, kind: str, transition: str, start: float = 0.0) -> OperationObservation:
    return OperationObservation(
        operation_id=op_id,
        kind=kind,
        transition=transition,
        raw=MappingProxyType({}),
        start_monotonic=start,
        hard_deadline_monotonic=start + 100.0,
    )


class TestOperationLedgerFsm:
    def test_valid_start_creates_entry(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        assert ledger.has_active_under_deadline(50.0) is True

    def test_duplicate_start_same_kind_is_noop(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started", start=1.0))
        # Replay same kind — must not renew start
        ledger.apply(_obs("op-1", "mcp_tool_call", "started", start=2.0))
        snap = ledger.snapshot()
        assert "op-1" in snap
        assert snap["op-1"].start_monotonic == 1.0

    def test_duplicate_start_conflicting_kind_quarantines(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-1", "command_execution", "started"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_update_does_not_renew_cap(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started", start=1.0))
        # Update does not change start or cap
        ledger.apply(_obs("op-1", "mcp_tool_call", "updated", start=10.0))
        snap = ledger.snapshot()
        assert snap["op-1"].start_monotonic == 1.0
        assert snap["op-1"].hard_deadline_monotonic == 101.0

    def test_absent_update_grants_no_authority(self) -> None:
        ledger = OperationLedger()
        # No start, only an update — must not create an entry
        ledger.apply(_obs("op-1", "mcp_tool_call", "updated"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_terminal_removes_entry(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-1", "mcp_tool_call", "completed"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_terminal_failed_removes_entry(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-1", "mcp_tool_call", "failed"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_terminal_declined_removes_entry(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-1", "mcp_tool_call", "declined"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_missing_id_observation_is_noop(self) -> None:
        ledger = OperationLedger()
        # operation_id == "" should be ignored
        ledger.apply(_obs("", "mcp_tool_call", "started"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_expired_entries_are_pruned(self) -> None:
        ledger = OperationLedger()
        # hard_deadline_monotonic = start + 100
        ledger.apply(_obs("op-1", "mcp_tool_call", "started", start=1.0))
        # Querying past the cap prunes
        assert ledger.has_active_under_deadline(200.0) is False
        # Subsequent queries remain False (not re-added)
        assert ledger.has_active_under_deadline(300.0) is False

    def test_clear_drops_all_entries(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-2", "command_execution", "started"))
        ledger.clear()
        assert ledger.has_active_under_deadline(50.0) is False

    def test_overlap_two_active_operations(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        ledger.apply(_obs("op-2", "command_execution", "started"))
        # Both active; query returns True
        assert ledger.has_active_under_deadline(50.0) is True
        # Complete one — other remains
        ledger.apply(_obs("op-1", "mcp_tool_call", "completed"))
        assert ledger.has_active_under_deadline(50.0) is True
        # Complete the other
        ledger.apply(_obs("op-2", "command_execution", "completed"))
        assert ledger.has_active_under_deadline(50.0) is False

    def test_snapshot_is_immutable(self) -> None:
        ledger = OperationLedger()
        ledger.apply(_obs("op-1", "mcp_tool_call", "started"))
        snap = ledger.snapshot()
        with pytest.raises((TypeError, AttributeError)):
            snap["op-2"] = object()  # type: ignore[index]


class TestLivenessCoordinator:
    def test_typed_operation_yields_continue(self) -> None:
        snap = LivenessSnapshot(
            now_monotonic=10.0,
            ledger_has_active=True,
        )
        outcome = LivenessCoordinator().decide(snap)
        assert outcome.verb == "CONTINUE"
        assert outcome.reason == "typed_operation"

    def test_fallback_fresh_yields_continue(self) -> None:
        snap = LivenessSnapshot(
            now_monotonic=10.0,
            ledger_has_active=False,
            fallback_snapshot=MappingProxyType({"fallback_fresh": True}),
        )
        outcome = LivenessCoordinator().decide(snap)
        assert outcome.verb == "CONTINUE"
        assert outcome.reason == "fallback_snapshot"

    def test_no_authority_yields_terminate(self) -> None:
        snap = LivenessSnapshot(now_monotonic=10.0, ledger_has_active=False)
        outcome = LivenessCoordinator().decide(snap)
        assert outcome.verb == "TERMINATE"
        assert outcome.reason == "idle_no_authority"

    def test_typed_operation_wins_over_no_fallback(self) -> None:
        snap = LivenessSnapshot(
            now_monotonic=10.0,
            ledger_has_active=True,
            fallback_snapshot=MappingProxyType({}),
        )
        outcome = LivenessCoordinator().decide(snap)
        assert outcome.verb == "CONTINUE"


class TestAttemptRuntime:
    def test_advance_within_ceiling_updates_deadline(self) -> None:
        runtime = AttemptRuntime(
            initial_wall_deadline=100.0,
            hard_wall_ceiling=200.0,
        )
        assert runtime.advance_to(150.0) is True
        assert runtime.current_wall_deadline == 150.0
        assert runtime.coordinator_epochs == 1

    def test_advance_past_ceiling_is_clamped(self) -> None:
        runtime = AttemptRuntime(
            initial_wall_deadline=100.0,
            hard_wall_ceiling=200.0,
        )
        assert runtime.advance_to(500.0) is True
        assert runtime.current_wall_deadline == 200.0

    def test_advance_backward_is_rejected(self) -> None:
        runtime = AttemptRuntime(
            initial_wall_deadline=100.0,
            hard_wall_ceiling=200.0,
        )
        assert runtime.advance_to(50.0) is False
        assert runtime.current_wall_deadline == 100.0


class TestFactory:
    def test_make_ledger_and_coordinator(self) -> None:
        ledger, coord = make_ledger_and_coordinator(max_suppression_seconds=42.0)
        assert isinstance(ledger, OperationLedger)
        assert isinstance(coord, LivenessCoordinator)

    def test_operation_observation_from_codex_timing(self) -> None:
        before = time.monotonic()
        obs = operation_observation_from_codex(
            operation_id="op-1",
            kind="mcp_tool_call",
            transition="started",
        )
        after = time.monotonic()
        assert obs.start_monotonic >= before
        assert obs.start_monotonic <= after
        assert obs.operation_id == "op-1"
        assert obs.kind == "mcp_tool_call"
        assert obs.transition == "started"
        assert obs.hard_deadline_monotonic == 0.0
