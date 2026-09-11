"""Tests for the child-terminal-reason snapshot authority (issue #4623).

Covers the stdlib-only classifier/merge/persistence module
(``hooks/_child_outcome_snapshot.py``). Hook-replay tests against pinned
native Claude/Codex fixtures are added alongside ``hooks/child_outcome_hook.py``
in the same test file (see the "native coverage" section added in the
following implementation step).
"""

from __future__ import annotations

import fcntl
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from autoskillit.core import CliSubtype, InfraExitCategory
from autoskillit.hooks import _child_outcome_snapshot as snap
from autoskillit.hooks import _session_binding

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


# --- Contract: canonical strings equal the existing core enum values ---------


def test_context_exhausted_value_matches_infra_exit_category_enum() -> None:
    assert snap._INFRA_EXIT_CONTEXT_EXHAUSTED == InfraExitCategory.CONTEXT_EXHAUSTED.value


def test_turn_limit_input_value_matches_cli_subtype_enum() -> None:
    assert snap._CLI_SUBTYPE_ERROR_MAX_TURNS == CliSubtype.ERROR_MAX_TURNS.value


def test_canonical_reasons_are_exactly_the_seven_named_values() -> None:
    assert snap.CANONICAL_TERMINAL_REASONS == {
        "completed",
        "context_exhausted",
        "turn_limited",
        "error",
        "abandoned",
        "interrupted",
        "unknown",
    }


# --- Reason fidelity and provider independence (plan Tests §1) --------------


@pytest.mark.parametrize(
    ("evidence", "expected_reason"),
    [
        pytest.param({"confirmed_completed": True}, snap.REASON_COMPLETED, id="completed"),
        pytest.param(
            {"infra_exit_category": "context_exhausted"},
            snap.REASON_CONTEXT_EXHAUSTED,
            id="context_exhausted_infra_category",
        ),
        pytest.param(
            {"context_terminal_code": "prompt_too_long"},
            snap.REASON_CONTEXT_EXHAUSTED,
            id="context_exhausted_terminal_code",
        ),
        pytest.param(
            {"cli_subtype": "error_max_turns"},
            snap.REASON_TURN_LIMITED,
            id="turn_limited_cli_subtype",
        ),
        pytest.param(
            {"terminal_reason": "max_turns"},
            snap.REASON_TURN_LIMITED,
            id="turn_limited_terminal_reason",
        ),
        pytest.param(
            {"api_terminal_reason": "api_error", "cli_subtype": "success"},
            snap.REASON_ERROR,
            id="error_api_terminal_reason_outranks_success_subtype",
        ),
        pytest.param(
            {
                "harness_literal": (
                    "Agent terminated early due to an API error: 503 Service Unavailable"
                )
            },
            snap.REASON_ERROR,
            id="error_harness_literal_exact_substring",
        ),
        pytest.param(
            {"harness_literal": "the agent stopped, terminated early because of something else"},
            snap.REASON_UNKNOWN,
            id="error_harness_literal_requires_exact_substring_not_keywords",
        ),
        pytest.param({"confirmed_interrupted": True}, snap.REASON_INTERRUPTED, id="interrupted"),
        pytest.param({"confirmed_abandoned": True}, snap.REASON_ABANDONED, id="abandoned"),
        pytest.param({}, snap.REASON_UNKNOWN, id="unknown_no_evidence"),
        pytest.param(
            {"subagent_stop_reason": "completed", "end_turn": False},
            snap.REASON_UNKNOWN,
            id="unknown_generic_subagent_stop_and_missing_end_turn_never_determine_reason",
        ),
    ],
)
def test_classify_evidence_reason_table(evidence: dict, expected_reason: str) -> None:
    assert snap.classify_evidence(evidence) == expected_reason


@pytest.mark.parametrize("effective_model", ["claude-opus-5", "minimax-abab7", "gpt-5.1"])
@pytest.mark.parametrize("role", ["reviewer", "explorer", ""])
@pytest.mark.parametrize("carries_end_turn", [True, False])
def test_classification_is_provider_role_and_end_turn_invariant(
    effective_model: str, role: str, carries_end_turn: bool
) -> None:
    """Changing only model/provider/role tags or ``end_turn`` presence leaves the reason
    identical."""
    evidence: dict = {
        "cli_subtype": "error_max_turns",
        "effective_model": effective_model,
        "role": role,
    }
    if carries_end_turn:
        evidence["end_turn"] = True
    assert snap.classify_evidence(evidence) == snap.REASON_TURN_LIMITED


def test_subtype_success_with_api_error_terminal_reason_classifies_as_error() -> None:
    evidence = {"cli_subtype": "success", "api_terminal_reason": "api_error"}
    assert snap.classify_evidence(evidence) == snap.REASON_ERROR


def test_unknown_is_a_separately_countable_reason(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="parent-unknown"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="parent-unknown", child_id="c1"
    )
    outcomes = snap.project_outcomes(snap.read_snapshot(snapshot_path))
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN


# --- Exactly one child row (plan Tests §2) -----------------------------------


def test_duplicated_start_and_result_notifications_produce_one_row(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    for _ in range(3):
        snap.observe_child(
            snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
        )
    for _ in range(3):
        snap.record_terminal_evidence(
            snapshot_path,
            backend="claude_code",
            parent_session_id="p1",
            child_id="child-a",
            evidence_key="claude_code:child-a:result:req-1",
            evidence={"confirmed_completed": True, "evidence_source": "result"},
        )
    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 1
    outcomes = snap.project_outcomes(document)
    assert outcomes[0]["terminal_reason"] == snap.REASON_COMPLETED


def test_two_distinct_child_ids_produce_two_rows(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-b"
    )
    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 2


def test_managed_launch_alias_binds_into_the_same_row_once_known(tmp_path) -> None:
    """A managed attempt ID reserved before spawn, later bound to a backend session ID,
    stays one row: the launch alias merges in rather than creating a second row."""
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="attempt-1",
    )
    snap.observe_child(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="attempt-1",
        launch_alias="native-session-abc",
    )
    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 1
    outcomes = snap.project_outcomes(document)
    assert outcomes[0]["launch_alias"] == "native-session-abc"


def test_physical_retry_attempts_are_separate_rows(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="attempt-1",
        evidence_key="claude_code:attempt-1:result:r1",
        evidence={"api_terminal_reason": "api_error", "evidence_source": "result"},
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="attempt-2",
        evidence_key="claude_code:attempt-2:result:r2",
        evidence={"confirmed_completed": True, "evidence_source": "result"},
    )
    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 2
    outcomes = {o["child_id"]: o["terminal_reason"] for o in snap.project_outcomes(document)}
    assert outcomes == {"attempt-1": snap.REASON_ERROR, "attempt-2": snap.REASON_COMPLETED}


def test_out_of_order_result_before_start_still_creates_one_confirmed_row(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:r1",
        evidence={"confirmed_completed": True, "evidence_source": "result"},
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 1
    outcome = document["children"]["child-a"]["outcome"]
    assert outcome["terminal_reason"] == snap.REASON_COMPLETED
    assert outcome["start_confirmed"] is True


def test_late_precise_evidence_refines_unknown_without_downgrading(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:subagent_stop:generic",
        evidence={"evidence_source": "subagent_stop"},
    )
    outcome = snap.read_snapshot(snapshot_path)["children"]["child-a"]["outcome"]
    assert outcome["terminal_reason"] == snap.REASON_UNKNOWN

    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:precise",
        evidence={"cli_subtype": "error_max_turns", "evidence_source": "result"},
    )
    outcome = snap.read_snapshot(snapshot_path)["children"]["child-a"]["outcome"]
    assert outcome["terminal_reason"] == snap.REASON_TURN_LIMITED

    # Further generic silence never downgrades the now-known reason.
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:session_end:generic",
        evidence={"evidence_source": "session_end"},
    )
    outcome = snap.read_snapshot(snapshot_path)["children"]["child-a"]["outcome"]
    assert outcome["terminal_reason"] == snap.REASON_TURN_LIMITED


def test_conflicting_equal_authority_evidence_collapses_to_unknown_with_raw_evidence(
    tmp_path,
) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:a",
        evidence={"confirmed_completed": True, "evidence_source": "result-a"},
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:b",
        evidence={"api_terminal_reason": "api_error", "evidence_source": "result-b"},
    )
    outcome = snap.read_snapshot(snapshot_path)["children"]["child-a"]["outcome"]
    assert outcome["terminal_reason"] == snap.REASON_UNKNOWN
    assert "conflict" in outcome["raw_reason"]


def test_duplicate_evidence_key_is_a_true_no_op(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:r1",
        evidence={"confirmed_completed": True, "evidence_source": "result"},
    )
    reason = snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:result:r1",
        evidence={"api_terminal_reason": "api_error", "evidence_source": "different-content"},
    )
    assert reason == snap.REASON_COMPLETED


def test_rejected_reservation_that_never_confirms_leaves_no_row(tmp_path) -> None:
    """A pre-tool reservation cancelled before confirmed spawn never calls
    observe_child/record_terminal_evidence, so it produces no snapshot row at all."""
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    document = snap.read_snapshot(snapshot_path)
    assert document == {}
    assert snap.project_outcomes(document) == ()


# --- Durability (plan Tests §3) ----------------------------------------------


def test_snapshot_is_readable_by_a_fresh_process_after_atomic_write(tmp_path) -> None:
    """The module holds no module-level cache; a write is immediately durable and
    visible to a completely independent read (simulating parent-process death)."""
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    # A "fresh process" recovery is a fresh read with no shared state.
    outcomes = snap.project_outcomes(snap.read_snapshot(snapshot_path))
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN


def test_concurrent_distinct_child_writes_lose_no_row(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )

    def _write(index: int) -> None:
        snap.record_terminal_evidence(
            snapshot_path,
            backend="claude_code",
            parent_session_id="p1",
            child_id=f"child-{index}",
            evidence_key=f"claude_code:child-{index}:result:r",
            evidence={"confirmed_completed": True, "evidence_source": "result"},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_write, range(16)))

    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 16
    outcomes = snap.project_outcomes(document)
    assert all(o["terminal_reason"] == snap.REASON_COMPLETED for o in outcomes)


def test_concurrent_duplicate_callbacks_for_the_same_child_add_no_extra_row(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )

    def _write(_: int) -> None:
        snap.record_terminal_evidence(
            snapshot_path,
            backend="claude_code",
            parent_session_id="p1",
            child_id="child-a",
            evidence_key="claude_code:child-a:result:r1",
            evidence={"confirmed_completed": True, "evidence_source": "result"},
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_write, range(16)))

    document = snap.read_snapshot(snapshot_path)
    assert len(document["children"]) == 1
    entry = document["children"]["child-a"]
    assert entry["evidence_keys"] == ["claude_code:child-a:result:r1"]


def test_lock_contention_times_out_with_a_finite_wait(monkeypatch, tmp_path) -> None:
    """Mirrors the existing bounded-flock-retry pattern: a held lock causes a
    finite TimeoutError, never an unbounded block."""
    monkeypatch.setattr(_session_binding, "_FLOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(_session_binding, "_FLOCK_POLL_INTERVAL_S", 0.02)

    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    lock_path = _session_binding.binding_lock_path(snapshot_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError):
            snap.observe_child(
                snapshot_path,
                backend="claude_code",
                parent_session_id="p1",
                child_id="child-a",
            )
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_explicit_owner_cancellation_is_recorded_as_interrupted(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    reason = snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="child-a",
        evidence_key="claude_code:child-a:owner_cancel:r1",
        evidence={"confirmed_interrupted": True, "evidence_source": "owner_cancel"},
    )
    assert reason == snap.REASON_INTERRUPTED


def test_reconcile_ended_children_never_invents_a_cause_for_unknown_rows(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.observe_child(
        snapshot_path, backend="claude_code", parent_session_id="p1", child_id="child-a"
    )
    snap.reconcile_ended_children(snapshot_path, backend="claude_code", parent_session_id="p1")
    outcomes = snap.project_outcomes(snap.read_snapshot(snapshot_path))
    assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN


# --- Path/identity validation -------------------------------------------------


def test_resolve_snapshot_path_rejects_path_traversal_components(tmp_path) -> None:
    with pytest.raises(snap.ChildOutcomeSnapshotError):
        snap.resolve_snapshot_path(tmp_path, backend="claude_code", parent_session_id="../etc")


def test_resolve_snapshot_path_is_namespaced_by_backend_and_parent(tmp_path) -> None:
    claude_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="same-id"
    )
    codex_path = snap.resolve_snapshot_path(tmp_path, backend="codex", parent_session_id="same-id")
    assert claude_path != codex_path
    assert "claude_code" in str(claude_path)
    assert "codex" in str(codex_path)
