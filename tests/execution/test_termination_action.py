"""Pure termination-action decision table tests."""

from __future__ import annotations

from itertools import product

import pytest

from autoskillit.core.types import LifecycleDecision, TerminationAction, TerminationReason
from autoskillit.execution.process import decide_termination_action

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.parametrize(
    "termination,timeout_fired,process_exited,owned,decision,expected",
    [
        (
            TerminationReason.COMPLETED,
            False,
            True,
            False,
            LifecycleDecision.ELIGIBLE,
            TerminationAction.NO_KILL,
        ),
        (
            TerminationReason.COMPLETED,
            False,
            True,
            True,
            LifecycleDecision.ELIGIBLE,
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
        ),
        (
            TerminationReason.COMPLETED,
            False,
            False,
            True,
            LifecycleDecision.ELIGIBLE,
            TerminationAction.DRAIN_THEN_KILL_IF_ALIVE,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            False,
            True,
            False,
            LifecycleDecision.CONTINUE,
            TerminationAction.NO_KILL,
        ),
        (
            TerminationReason.SIGNAL_DEATH,
            False,
            True,
            False,
            LifecycleDecision.CONTINUE,
            TerminationAction.NO_KILL,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            False,
            True,
            True,
            LifecycleDecision.CONTINUE,
            TerminationAction.IMMEDIATE_KILL,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            False,
            False,
            False,
            LifecycleDecision.CONTINUE,
            TerminationAction.IMMEDIATE_KILL,
        ),
        (
            TerminationReason.TIMED_OUT,
            True,
            True,
            False,
            LifecycleDecision.CONTINUE,
            TerminationAction.IMMEDIATE_KILL,
        ),
        (
            TerminationReason.STALE,
            False,
            True,
            True,
            LifecycleDecision.CONTINUE,
            TerminationAction.IMMEDIATE_KILL,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            False,
            True,
            False,
            LifecycleDecision.CHILD_WORK_FAILED,
            TerminationAction.IMMEDIATE_KILL,
        ),
        (
            TerminationReason.NATURAL_EXIT,
            False,
            True,
            False,
            LifecycleDecision.CATCH_UP_FAILED,
            TerminationAction.IMMEDIATE_KILL,
        ),
    ],
    ids=[
        "completed-exited-clear",
        "completed-exited-retained-descendant",
        "completed-root-alive",
        "natural-exited-clear",
        "signal-death-clear",
        "natural-exited-retained-descendant",
        "natural-not-exited",
        "timeout-overrides-exit",
        "failure-retained-descendant",
        "child-work-failed",
        "catch-up-failed",
    ],
)
def test_decide_termination_action_matrix(
    termination: TerminationReason,
    timeout_fired: bool,
    process_exited: bool,
    owned: bool,
    decision: LifecycleDecision,
    expected: TerminationAction,
) -> None:
    assert (
        decide_termination_action(
            termination,
            timeout_fired=timeout_fired,
            process_exited=process_exited,
            owned_processes=owned,
            lifecycle_decision=decision,
        )
        is expected
    )


def test_no_kill_requires_exited_root_and_clear_ownership() -> None:
    for termination, process_exited, owned in product(
        TerminationReason,
        (False, True),
        (False, True),
    ):
        action = decide_termination_action(
            termination,
            timeout_fired=False,
            process_exited=process_exited,
            owned_processes=owned,
            lifecycle_decision=LifecycleDecision.CONTINUE,
        )
        if action is TerminationAction.NO_KILL:
            assert process_exited
            assert not owned


@pytest.mark.parametrize(
    "state,decision",
    [
        ("active", LifecycleDecision.CHILD_WORK_FAILED),
        ("awaiting_delivery", LifecycleDecision.CHILD_WORK_FAILED),
        ("deferred", LifecycleDecision.CHILD_WORK_FAILED),
        ("unresolved_terminal", LifecycleDecision.CHILD_WORK_FAILED),
        ("catch_up_failed", LifecycleDecision.CATCH_UP_FAILED),
    ],
)
def test_blocked_lifecycle_exit_never_uses_no_kill(
    state: str,
    decision: LifecycleDecision,
) -> None:
    action = decide_termination_action(
        TerminationReason.HEALTH_INSPECTOR,
        timeout_fired=False,
        process_exited=True,
        owned_processes=state == "active",
        lifecycle_decision=decision,
    )
    assert action is TerminationAction.IMMEDIATE_KILL


@pytest.mark.parametrize("termination", list(TerminationReason))
def test_timeout_always_immediate(termination: TerminationReason) -> None:
    assert (
        decide_termination_action(
            termination,
            timeout_fired=True,
            process_exited=True,
            owned_processes=False,
            lifecycle_decision=LifecycleDecision.ELIGIBLE,
        )
        is TerminationAction.IMMEDIATE_KILL
    )
