"""Unit tests for decide_termination_action — pure decision function (1a).

Tests the decision table that replaces the if/elif/else dispatch in process.py.
All tests here must FAIL before Phase 2 implementation introduces
decide_termination_action, TerminationAction, and KillReason.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import TerminationAction, TerminationReason
from autoskillit.execution.process import decide_termination_action


@pytest.mark.parametrize(
    "termination,timeout_fired,process_exited,expected",
    [
        # (COMPLETED, timeout=False, exited=True) → NO_KILL
        (TerminationReason.COMPLETED, False, True, TerminationAction.NO_KILL),
        # (COMPLETED, timeout=False, exited=False) → DRAIN_THEN_KILL_IF_ALIVE ← the -9 bug row
        (TerminationReason.COMPLETED, False, False, TerminationAction.DRAIN_THEN_KILL_IF_ALIVE),
        # (NATURAL_EXIT, timeout=False, exited=True) → NO_KILL
        (TerminationReason.NATURAL_EXIT, False, True, TerminationAction.NO_KILL),
        # (NATURAL_EXIT, timeout=False, exited=False) → NO_KILL
        (TerminationReason.NATURAL_EXIT, False, False, TerminationAction.NO_KILL),
        # (TIMED_OUT, timeout=True, exited=True) → IMMEDIATE_KILL
        (TerminationReason.TIMED_OUT, True, True, TerminationAction.IMMEDIATE_KILL),
        # (TIMED_OUT, timeout=True, exited=False) → IMMEDIATE_KILL
        (TerminationReason.TIMED_OUT, True, False, TerminationAction.IMMEDIATE_KILL),
        # (IDLE_STALL, timeout=False, exited=False) → IMMEDIATE_KILL
        (TerminationReason.IDLE_STALL, False, False, TerminationAction.IMMEDIATE_KILL),
        # (STALE, timeout=False, exited=False) → IMMEDIATE_KILL
        (TerminationReason.STALE, False, False, TerminationAction.IMMEDIATE_KILL),
        # timeout_fired beats process_exited
        (TerminationReason.COMPLETED, True, True, TerminationAction.IMMEDIATE_KILL),
        # IDLE_STALL with process_exited=True, no timeout → NO_KILL (process exit wins)
        (TerminationReason.IDLE_STALL, False, True, TerminationAction.NO_KILL),
        # STALE with process already exited → NO_KILL (process exit wins)
        (TerminationReason.STALE, False, True, TerminationAction.NO_KILL),
    ],
    ids=[
        "completed_exited_no_kill",
        "completed_alive_drain_kill",
        "natural_exit_exited_no_kill",
        "natural_exit_alive_no_kill",
        "timed_out_exited_immediate_kill",
        "timed_out_alive_immediate_kill",
        "idle_stall_alive_immediate_kill",
        "stale_alive_immediate_kill",
        "timeout_beats_exited",
        "idle_stall_exited_no_kill",
        "stale_exited_no_kill",
    ],
)
def test_decide_termination_action_matrix(
    termination: TerminationReason,
    timeout_fired: bool,
    process_exited: bool,
    expected: TerminationAction,
) -> None:
    """Decision table covers all meaningful (termination, timeout_fired, process_exited) combos."""
    result = decide_termination_action(
        termination, timeout_fired=timeout_fired, process_exited=process_exited
    )
    assert result == expected


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_decide_termination_action_exhaustive_no_raise(termination: TerminationReason) -> None:
    """Exhaustiveness guard: must return a valid TerminationAction for every TerminationReason."""
    result = decide_termination_action(termination, timeout_fired=False, process_exited=False)
    assert isinstance(result, TerminationAction)


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_decide_termination_action_timeout_always_immediate_kill(
    termination: TerminationReason,
) -> None:
    """timeout_fired=True always yields IMMEDIATE_KILL regardless of termination reason."""
    result = decide_termination_action(termination, timeout_fired=True, process_exited=False)
    assert result == TerminationAction.IMMEDIATE_KILL


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_decide_termination_action_process_exited_yields_no_kill_when_no_timeout(
    termination: TerminationReason,
) -> None:
    """process_exited=True with no timeout yields NO_KILL for any termination reason."""
    result = decide_termination_action(termination, timeout_fired=False, process_exited=True)
    assert result == TerminationAction.NO_KILL
