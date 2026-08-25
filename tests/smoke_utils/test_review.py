"""Smoke-utils tests relocated from the former monolith."""

from __future__ import annotations

import pytest

from autoskillit.smoke_utils import (
    check_loop_iteration,
    check_loop_with_progress,
    init_counter,
)

pytestmark = [pytest.mark.medium]


def test_check_loop_iteration_first_call() -> None:
    """First iteration (empty string) → next=1, max_exceeded=false for max=2."""
    result = check_loop_iteration(current_iteration="", max_iterations="2")
    assert result == {"next_iteration": "1", "max_exceeded": "false"}


def test_check_loop_iteration_at_budget() -> None:
    """iteration=1, max=2 → next=2, max_exceeded=true."""
    result = check_loop_iteration(current_iteration="1", max_iterations="2")
    assert result == {"next_iteration": "2", "max_exceeded": "true"}


def test_check_loop_iteration_over_budget() -> None:
    """iteration=5, max=2 → max_exceeded=true."""
    result = check_loop_iteration(current_iteration="5", max_iterations="2")
    assert result == {"next_iteration": "6", "max_exceeded": "true"}


def test_check_loop_iteration_custom_max() -> None:
    """iteration=3, max=5 → next=4, max_exceeded=false."""
    result = check_loop_iteration(current_iteration="3", max_iterations="5")
    assert result == {"next_iteration": "4", "max_exceeded": "false"}


def test_check_loop_iteration_defaults() -> None:
    """No arguments → iteration=0, max=2 → next=1, max_exceeded=false."""
    result = check_loop_iteration()
    assert result == {"next_iteration": "1", "max_exceeded": "false"}


def test_check_loop_iteration_budget_semantics_documented() -> None:
    """Document: max_iterations=N allows N-1 loop body executions (>= comparison).

    With max_iterations="3" (the corrected default for audit remediation):
    - Round 0→1: allowed (first remediation attempt)
    - Round 1→2: allowed (second remediation attempt)
    - Round 2→3: blocked (budget exhausted)
    Result: 2 remediation rounds with max_iterations="3".
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration=r1["next_iteration"], max_iterations="3")
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="3")
    assert r3["max_exceeded"] == "true"


def test_check_loop_iteration_ref_push_budget_two_attempts() -> None:
    """Ref-push budget: max_iterations='3' yields exactly 2 usable push attempts.

    Locks the ref-push recovery budget under the existing ``>=`` semantics:
    with ``max_iterations='3'`` (the production value for ``check_ref_push_loop``
    and ``check_ref_push_loop_pre_remediation`` in ``remediation.yaml``),
    the counter permits two push attempts before exhausting:

    - Round 0→1: allowed (first push attempt)
    - Round 1→2: allowed (second push attempt)
    - Round 2→3: blocked (budget exhausted)

    This matches the intent of the ref-push recovery chain — two retries are
    enough to absorb a transient ref-coherence divergence without false
    positives. Do NOT change ``check_loop_iteration``'s ``>=`` operator — the
    ``max_iterations='3'`` value is the canonical budget adjustment for
    ref-push sites (issue #4274, Part B Step 1).
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["next_iteration"] == "1"
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration="1", max_iterations="3")
    assert r2["next_iteration"] == "2"
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration="2", max_iterations="3")
    assert r3["next_iteration"] == "3"
    assert r3["max_exceeded"] == "true"


def test_check_loop_iteration_cross_cycle_budget_starvation() -> None:
    """Cross-cycle budget starvation: counter persists → new cycle has zero budget.

    Simulates: cycle 1 uses 2 fix attempts (counter reaches "2"), then cycle 2
    tries to use the counter without resetting — max_exceeded fires immediately.
    After resetting via init_counter, the budget is fresh.
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration=r1["next_iteration"], max_iterations="3")
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="3")
    assert r3["max_exceeded"] == "true"

    reset = init_counter(counter_value="")
    r4 = check_loop_iteration(current_iteration=reset["value"], max_iterations="3")
    assert r4["max_exceeded"] == "false"
    assert r4["next_iteration"] == "1"


def test_check_loop_iteration_max_iterations_two_single_push_boundary() -> None:
    """max_iterations="2" allows exactly ONE push attempt (issue #4274 boundary).

    With max_iterations="2" (the production value for ``ref_push_count``), the
    counter permits exactly one increment before exhausting the budget:

    - Round 1: 0→1 (allowed), the single permitted push attempt.
    - Reset: counter back to "" via ``init_counter``.
    - Round 2: 0→1 (allowed), the second push attempt.
    - Final increment 1→2: blocked — ``max_exceeded == "true"``.

    Any cycle that needs ≥2 pushes between resets therefore exhausts the
    budget at the second push, regardless of how many audit-rem cycles
    wrap around it. The existing ``max_iterations="3"`` test has comfortable
    margin; this ``max=2`` variant exposes the tight single-push boundary
    that the ref-push retry chain actually operates under.
    """
    # First push attempt
    r1 = check_loop_iteration(current_iteration="", max_iterations="2")
    assert r1["max_exceeded"] == "false"
    assert r1["next_iteration"] == "1"

    # Reset between cycles — init_counter returns "0" for blank input
    reset = init_counter(counter_value="")
    assert reset["value"] == "0"

    # Second push attempt — counter fresh, allowed
    r2 = check_loop_iteration(current_iteration=reset["value"], max_iterations="2")
    assert r2["max_exceeded"] == "false"
    assert r2["next_iteration"] == "1"

    # The next increment 1→2 exhausts the budget
    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="2")
    assert r3["max_exceeded"] == "true"


def test_check_loop_with_progress_zero_progress_first_iteration() -> None:
    """First zero-progress iteration returns zero_progress=false (needs 2 consecutive)."""
    result = check_loop_with_progress(
        current_iteration="1",
        max_iterations="5",
        issues_fixed_count="0",
        prev_issues_fixed_count="",
    )
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "2"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_two_consecutive_zero() -> None:
    """Two consecutive zero-progress iterations returns zero_progress=true."""
    result = check_loop_with_progress(
        current_iteration="2",
        max_iterations="5",
        issues_fixed_count="0",
        prev_issues_fixed_count="0",
    )
    assert result["zero_progress"] == "true"
    assert result["next_iteration"] == "3"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_progress_after_zero() -> None:
    """Progress after a zero-progress iteration resets the detection."""
    result = check_loop_with_progress(
        current_iteration="2",
        max_iterations="5",
        issues_fixed_count="3",
        prev_issues_fixed_count="0",
    )
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "3"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_propagates_prev_count() -> None:
    """zero_progress=false on first call propagates current as prev."""
    result = check_loop_with_progress(
        current_iteration="1",
        max_iterations="5",
        issues_fixed_count="2",
        prev_issues_fixed_count="",
    )
    assert result["prev_issues_fixed_count"] == "2"
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "2"
    assert result["max_exceeded"] == "false"


def test_check_loop_iteration_none_current() -> None:
    result = check_loop_iteration(current_iteration=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"


def test_check_loop_iteration_none_max() -> None:
    result = check_loop_iteration(current_iteration="0", max_iterations=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"
