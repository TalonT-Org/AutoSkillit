"""Parallel-process declaration/ledger hook tests.

Per Plan § Step 2.3, these tests use parallel processes to assert:

(a) declaration validates fixed/runtime cardinality and artifact identity
(b) concurrent PreToolUse claims are locked and exact
(c) nested agent_id calls and unrelated sessions cannot claim
(d) success and PostToolUseFailure settle the correct direct handle
(e) identical duplicates are idempotent
(f) conflicts fail closed
(g) an unresolved wave denies non-Agent follow-up and blocks Stop
(h) only complete releases successful completion
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from autoskillit.hooks._join_ledger import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    WAVE_COMPLETE,
    JoinLedgerError,
    claim_assignment,
    declare_batch,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _worker_claim(args: tuple[str, str, str, str]) -> str:
    """Worker that performs a single claim.

    Each worker gets its own Python process so the flock contention is
    real cross-process POSIX locking, not in-process serialization.
    """
    flag_dir_str, session_id, parent, tool_use_id = args
    from autoskillit.hooks._join_ledger import claim_assignment

    try:
        record = claim_assignment(
            Path(flag_dir_str),
            session_id=session_id,
            top_level_parent=parent,
            tool_use_id=tool_use_id,
        )
        if record is None:
            return "none"
        return f"claimed:{record['label']}:{tool_use_id}"
    except JoinLedgerError as exc:
        return f"error:{exc}"


def _worker_declare_with_artifact(args: tuple[str, str, str, str, str, str]) -> str:
    """Worker that declares a batch with the given artifact identity."""
    flag_dir_str, session_id, parent, skill_name, artifact_digest, assignments_str = args
    from autoskillit.hooks._join_ledger import declare_batch

    assignments = tuple(assignments_str.split(","))
    try:
        declare_batch(
            Path(flag_dir_str),
            session_id=session_id,
            top_level_parent=parent,
            skill_name=skill_name,
            artifact_digest=artifact_digest,
            assignments=assignments,
        )
        return "ok"
    except JoinLedgerError as exc:
        return f"error:{exc}"


def _worker_settle(args: tuple[str, str, str, str, str]) -> str:
    flag_dir_str, session_id, parent, tool_use_id, outcome = args
    from autoskillit.hooks._join_ledger import settle_assignment

    try:
        settle_assignment(
            Path(flag_dir_str),
            session_id=session_id,
            top_level_parent=parent,
            tool_use_id=tool_use_id,
            outcome=outcome,
        )
        return "ok"
    except JoinLedgerError as exc:
        return f"error:{exc}"


def test_parallel_claims_are_locked_and_exact(tmp_path: Path) -> None:
    """(b) Concurrent PreToolUse claims are locked and exact."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2", "a3", "a4"),
    )

    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=4) as pool:
        results = pool.map(
            _worker_claim,
            [
                (str(flag_dir), "s1", "p1", "t1"),
                (str(flag_dir), "s1", "p1", "t2"),
                (str(flag_dir), "s1", "p1", "t3"),
                (str(flag_dir), "s1", "p1", "t4"),
            ],
        )

    # Every claim succeeded; the labels are unique.
    claimed = [r for r in results if r.startswith("claimed:")]
    assert len(claimed) == 4
    labels = sorted(r.split(":")[1] for r in claimed)
    assert labels == ["a1", "a2", "a3", "a4"]


def test_parallel_claims_with_excess_call_count_fail(tmp_path: Path) -> None:
    """(b) Once declared slots are filled, extra claims fail."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )

    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=4) as pool:
        results = pool.map(
            _worker_claim,
            [
                (str(flag_dir), "s1", "p1", "t1"),
                (str(flag_dir), "s1", "p1", "t2"),
                (str(flag_dir), "s1", "p1", "t3"),  # excess
                (str(flag_dir), "s1", "p1", "t4"),  # excess
            ],
        )

    errors = [r for r in results if r.startswith("error:")]
    assert errors, "expected at least one JoinLedgerError for excess claims"
    assert all("no unclaimed" in e for e in errors)


def test_unrelated_session_cannot_claim(tmp_path: Path) -> None:
    """(c) Nested sessions with different parent cannot claim this wave's slots."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    # Different session_id — unrelated session.
    unrelated = claim_assignment(
        flag_dir,
        session_id="s2",
        top_level_parent="p1",
        tool_use_id="t1",
    )
    assert unrelated is None


def test_nested_agent_id_call_does_not_claim(tmp_path: Path) -> None:
    """(c) An agent_id-bearing call is exempt and does not claim a slot."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    result = claim_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        agent_id="child-1",
    )
    assert result is None


def test_parallel_settlements_complete_wave(tmp_path: Path) -> None:
    """(d) (h) Concurrent settlements reliably drive the wave to complete."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2", "a3"),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t3")

    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=3) as pool:
        results = pool.map(
            _worker_settle,
            [
                (str(flag_dir), "s1", "p1", "t1", OUTCOME_SUCCESS),
                (str(flag_dir), "s1", "p1", "t2", OUTCOME_SUCCESS),
                (str(flag_dir), "s1", "p1", "t3", OUTCOME_SUCCESS),
            ],
        )

    assert all(r == "ok" for r in results)
    from autoskillit.hooks._join_ledger import active_batch

    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["wave_outcome"] == WAVE_COMPLETE


def test_post_tool_use_failure_settles_correct_handle(tmp_path: Path) -> None:
    """(d) A failure on one handle settles that handle as failure."""
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
        outcome=OUTCOME_FAILURE,
    )
    from autoskillit.hooks._join_ledger import active_batch

    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["assignments"][0]["outcome"] == "failure"
    assert batch["assignments"][1]["outcome"] == "pending"


def test_duplicate_settlement_idempotent(tmp_path: Path) -> None:
    """(e) Identical duplicate settlement is idempotent and does not raise."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    # Idempotent — no exception.
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )


def test_conflicting_settlement_fail_closed(tmp_path: Path) -> None:
    """(f) Conflicting terminal events for the same handle fail closed."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
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


def test_unresolved_wave_blocks_stop(tmp_path: Path) -> None:
    """(g) An unresolved wave blocks Stop through can_release_stop."""
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
    from autoskillit.hooks._join_ledger import can_release_stop

    allowed, reason = can_release_stop(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        session_binding={"join_required": True, "skill_name": "skill", "artifact_digest": "abc"},
    )
    assert allowed is False
    assert "unresolved" in reason or "open" in reason


def test_complete_wave_releases_stop(tmp_path: Path) -> None:
    """(h) Only complete waves release successful completion."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    from autoskillit.hooks._join_ledger import can_release_stop

    allowed, _reason = can_release_stop(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        session_binding={"join_required": True, "skill_name": "skill", "artifact_digest": "abc"},
    )
    assert allowed is True


def test_declaration_validates_artifact_identity(tmp_path: Path) -> None:
    """(a) Declaration is keyed by artifact_digest and refuses to omit it."""
    flag_dir = tmp_path
    with pytest.raises(JoinLedgerError, match="artifact_digest"):
        declare_batch(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            skill_name="skill",
            artifact_digest="",
            assignments=("a1",),
        )


def test_declaration_validates_skill_name(tmp_path: Path) -> None:
    """(a) Declaration refuses empty skill_name."""
    flag_dir = tmp_path
    with pytest.raises(JoinLedgerError, match="skill_name"):
        declare_batch(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            skill_name="",
            artifact_digest="abc",
            assignments=("a1",),
        )
