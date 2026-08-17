"""Step 1 declared-batch cases and non-success outcomes.

Covers ``declare_batch`` validation (fixed count, runtime for_each,
duplicate labels, zero assignments, excess/insufficient Agent calls,
overlapping declarations, sequential waves) plus the wave-outcome
aggregation for partial-timeout, failure, cancellation, interruption,
missing-child, and parent-misuse negative traces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._join_ledger import (
    OUTCOME_CANCELLED,
    OUTCOME_FAILURE,
    OUTCOME_INTERRUPTION,
    OUTCOME_MISSING,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    WAVE_COMPLETE,
    WAVE_INTERRUPTION,
    WAVE_MISSING_CHILD,
    WAVE_PARTIAL,
    WAVE_PARTIAL_TIMEOUT,
    WAVE_PENDING,
    JoinLedgerError,
    active_batch,
    claim_assignment,
    declare_batch,
    ledger_paths,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


# ---------------------------------------------------------------------------
# 8 declared-batch cases
# ---------------------------------------------------------------------------


def test_declared_batch_fixed_count_round_trip(tmp_path: Path) -> None:
    """Fixed count is the simplest case: declare and then claim."""
    flag_dir = tmp_path
    record = declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    assert record["join_batch_id"]
    assert len(record["assignments"]) == 2
    assert all(a["tool_use_id"] is None for a in record["assignments"])


def test_declared_batch_runtime_for_each_accepts_collection(tmp_path: Path) -> None:
    """for_each supplies the concrete runtime labels at declaration time."""
    flag_dir = tmp_path
    record = declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("topic-a", "topic-b", "topic-c"),
    )
    assert len(record["assignments"]) == 3
    assert [a["label"] for a in record["assignments"]] == ["topic-a", "topic-b", "topic-c"]


def test_declared_batch_rejects_duplicate_labels(tmp_path: Path) -> None:
    """Duplicate labels are refused at declaration."""
    flag_dir = tmp_path
    with pytest.raises(JoinLedgerError, match="unique"):
        declare_batch(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            skill_name="skill",
            artifact_digest="abc",
            assignments=("a1", "a1"),
        )


def test_declared_batch_rejects_zero_assignments(tmp_path: Path) -> None:
    """A batch with zero assignments is refused."""
    flag_dir = tmp_path
    with pytest.raises(JoinLedgerError, match="non-empty"):
        declare_batch(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            skill_name="skill",
            artifact_digest="abc",
            assignments=(),
        )


def test_declared_batch_too_few_agents_blocks_stop(tmp_path: Path) -> None:
    """Too few claimed slots leave the wave unresolved."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2", "a3"),
    )
    # Only one slot claimed
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] == WAVE_PENDING
    assert sum(1 for a in batch["assignments"] if a["tool_use_id"] is not None) == 1


def test_declared_batch_excess_agent_calls_raises(tmp_path: Path) -> None:
    """More Agent calls than declared slots fail at claim."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    with pytest.raises(JoinLedgerError, match="no unclaimed"):
        claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")


def test_declared_batch_second_declaration_while_open_refused(tmp_path: Path) -> None:
    """A second declaration while the first is open is refused."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    with pytest.raises(JoinLedgerError, match="another wave is already open"):
        declare_batch(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            skill_name="skill",
            artifact_digest="abc",
            assignments=("b1",),
        )


def test_declared_batch_two_sequential_waves(tmp_path: Path) -> None:
    """Two valid sequential waves retain disjoint assignments."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    # First wave is now complete; a new declaration is allowed.
    second = declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("b1",),
    )
    assert second["join_batch_id"]
    assert [a["label"] for a in second["assignments"]] == ["b1"]


# ---------------------------------------------------------------------------
# 5 deterministic non-success outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome_label", "outcome_value"),
    [
        ("partial-timeout", OUTCOME_TIMEOUT),
        ("failure", OUTCOME_FAILURE),
        ("cancellation", OUTCOME_CANCELLED),
        ("interruption", OUTCOME_INTERRUPTION),
        ("missing", OUTCOME_MISSING),
    ],
)
def test_wave_outcome_propagates_deterministic_non_success(
    tmp_path: Path, outcome_label: str, outcome_value: str
) -> None:
    """Each non-success outcome is distinguishable and never emits complete."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=outcome_value,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] != WAVE_COMPLETE, (
        f"outcome {outcome_label!r} must not emit complete"
    )


def test_partial_timeout_completes_with_partial_outcome(tmp_path: Path) -> None:
    """A timeout on one slot produces partial_timeout wave outcome."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_TIMEOUT,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] == WAVE_PARTIAL_TIMEOUT


def test_complete_wave_emits_complete_outcome(tmp_path: Path) -> None:
    """All-success settles the wave as complete."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] == WAVE_COMPLETE


def test_missing_child_outcome(tmp_path: Path) -> None:
    """A missing child produces the missing-child wave outcome."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_MISSING,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] == WAVE_MISSING_CHILD


def test_duplicate_settlement_idempotent(tmp_path: Path) -> None:
    """The same (tool_use_id, outcome) event is idempotent."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    # Second settlement with identical outcome is accepted without
    # raising.
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_COMPLETE


def test_conflicting_settlement_fails_closed(tmp_path: Path) -> None:
    """Conflicting terminal events for the same handle fail closed."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    with pytest.raises(JoinLedgerError, match="conflicting"):
        settle_assignment(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            tool_use_id="t1",
            outcome=OUTCOME_FAILURE,
        )


# ---------------------------------------------------------------------------
# 5 negative traces
# ---------------------------------------------------------------------------


def test_negative_trace_parent_synthesizes_before_results(tmp_path: Path) -> None:
    """A parent that synthesizes before every child terminal is not allowed.

    Only one of the two declared assignments has been claimed; the wave
    must remain pending until every handle has a terminal outcome.
    """
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_PENDING


def test_negative_trace_parent_reports_success_partial(tmp_path: Path) -> None:
    """A settled-only-partial state must not present as success."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    # t2 stays pending — wave is not complete.
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_PENDING


def test_negative_trace_interrupts_healthy_child(tmp_path: Path) -> None:
    """Healthy children cannot be marked interrupted just because the
    user pressed Ctrl-C; the join contract requires a real unhealthy
    state to enter interruption."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_INTERRUPTION,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_INTERRUPTION


def test_negative_trace_partial_evidence_does_not_complete(tmp_path: Path) -> None:
    """A partial-evidence outcome (timed-out with no result) does not
    count as success."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    from autoskillit.hooks._join_ledger import claim_assignment

    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_TIMEOUT,
    )
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_MISSING,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] != WAVE_COMPLETE


def test_mixed_terminal_success_and_missing_settles_as_wave_partial(tmp_path: Path) -> None:
    """C7: a wave with mixed terminal outcomes (some SUCCESS, some MISSING)
    settles as WAVE_PARTIAL rather than silently stalling at WAVE_PENDING.
    Both outcomes are terminal, the priority chain above the fallthrough
    does not match, so the trailing return must surface the partial state
    instead of leaving consumers to wait forever."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_MISSING,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_PARTIAL
    # WAVE_PARTIAL must not be WAVE_COMPLETE — the wave did not fully
    # succeed and downstream consumers (Stop guard, follow-up guard)
    # must continue to refuse further progression.
    assert batch["wave_outcome"] != WAVE_COMPLETE


def test_duplicate_tool_use_id_while_pending_raises(tmp_path: Path) -> None:
    """C8: emitting the same tool_use_id twice — even while the prior
    claim is still PENDING — must raise JoinLedgerError. Two assignments
    sharing a tool_use_id would corrupt downstream settle bookkeeping,
    so the guard is unconditional on the prior entry's pending state.
    """
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    # First claim succeeds and leaves the entry PENDING.
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    # Second claim with the SAME tool_use_id must raise even though the
    # first entry is still pending (not yet settled).
    with pytest.raises(JoinLedgerError, match="already claimed"):
        claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")


def test_negative_trace_ledger_path_creates_correct_files(tmp_path: Path) -> None:
    """The ledger and lock files are placed in the correct flag dir."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    ledger, lock = ledger_paths(flag_dir)
    assert ledger.exists()
    assert lock.exists()
    # The ledger should contain valid JSON.
    import json

    payload = json.loads(ledger.read_text())
    assert "sessions" in payload
    assert "s1" in payload["sessions"]
