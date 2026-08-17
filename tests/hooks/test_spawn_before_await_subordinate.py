"""Spawn-before-await subordinate tests.

Per Plan § Step 1.8, these tests assert that a spawn-before-await
sequence is NOT sufficient evidence for a join. The declared-batch
closure remains the production barrier; the spawn-before-await path is
a focused check that catches accidental degradation, not a substitute
for full-set closure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._join_ledger import (
    OUTCOME_SUCCESS,
    WAVE_COMPLETE,
    WAVE_PENDING,
    JoinLedgerError,
    active_batch,
    claim_assignment,
    declare_batch,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_spawn_before_await_alone_does_not_complete_wave(tmp_path: Path) -> None:
    """Spawning all four children before waiting does NOT close the wave.

    The Oracle cannot treat the act of spawning as evidence of completion.
    Without a declared batch, the ledger has no wave to track, so this
    shape is incompatible with the production barrier.
    """
    flag_dir = tmp_path
    # Without a declared batch, the claim Attempt returns None.
    claimed = claim_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
    )
    assert claimed is None


def test_spawn_before_await_with_declaration_must_still_settle_all(
    tmp_path: Path,
) -> None:
    """Even with a declared batch, just spawning is not enough — every
    direct handle must be settled before the wave can emit complete."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2", "a3", "a4"),
    )
    # Spawn-before-await: claim all four slots up front.
    for tool_use_id in ("t1", "t2", "t3", "t4"):
        claim_assignment(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            tool_use_id=tool_use_id,
        )
    # Settle only one handle with success — the wave remains pending.
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_PENDING


def test_spawn_before_await_with_full_settlement_completes(tmp_path: Path) -> None:
    """Spawn-before-await + full settlement reaches complete."""
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
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] == WAVE_COMPLETE


def test_spawn_before_await_with_too_few_settlements_fail_closed(
    tmp_path: Path,
) -> None:
    """Spawn-before-await with too few settled outcomes is not complete."""
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
    # Only one settlement.
    settle_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch["wave_outcome"] != WAVE_COMPLETE


def test_spawn_before_await_excess_calls_refused(tmp_path: Path) -> None:
    """Spawn-before-await with more Agent calls than declared fails."""
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
    with pytest.raises(JoinLedgerError, match="no unclaimed"):
        claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
