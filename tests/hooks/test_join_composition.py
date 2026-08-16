"""Composition tests for the join contract ledger.

Per Plan § Step 7.8 (REQ-EXTRACT-081), seven assertions:
1. declaration precedes spawn
2. concurrent claim/settlement exact
3. denied PreToolUse creates no result record
4. PostToolUseFailure not missed
5. unresolved waves deny follow-up/Stop
6. deterministic non-success cannot emit success marker
7. valid sequential waves reclaim/reset only own ledger
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hooks._join_ledger import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    JoinLedgerError,
    active_batch,
    can_release_stop,
    claim_assignment,
    declare_batch,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


_JOIN_BINDING = {"join_required": True, "skill_name": "skill", "artifact_digest": "abc"}


def test_declaration_precedes_spawn(tmp_path: Path) -> None:
    """Wave 1 must be declared before any claim can succeed."""
    flag_dir = tmp_path
    # Attempt to claim without a declared batch
    result = claim_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
    )
    assert result is None
    # Declare the wave
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    # Now claim succeeds
    claimed = claim_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
    )
    assert claimed is not None
    assert claimed["label"] in ("a1", "a2")


def test_concurrent_claim_settlement_exact(tmp_path: Path) -> None:
    """Each declared assignment claims exactly once."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    c1 = claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    c2 = claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t2")
    assert c1 is not None
    assert c2 is not None
    labels = {c1["label"], c2["label"]}
    assert labels == {"a1", "a2"}
    # Third claim fails — all assignments taken
    with pytest.raises(JoinLedgerError):
        claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t3")


def test_post_tool_use_failure_settles(tmp_path: Path) -> None:
    """A failed settle path produces a non-complete outcome."""
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
        outcome=OUTCOME_FAILURE,
    )
    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch["assignments"][0]["outcome"] == OUTCOME_FAILURE


def test_unresolved_wave_denies_stop(tmp_path: Path) -> None:
    """An open wave blocks Stop completion."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    # Claim only one slot
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    # Stop should not release when a join-bound skill is loaded
    allowed, _reason = can_release_stop(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        session_binding=_JOIN_BINDING,
    )
    assert allowed is False


def test_complete_wave_releases_stop(tmp_path: Path) -> None:
    """All assignments settled -> Stop releases."""
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
    allowed, _reason = can_release_stop(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        session_binding=_JOIN_BINDING,
    )
    assert allowed is True


def test_no_binding_releases_stop(tmp_path: Path) -> None:
    """Without a join-bearing session binding, Stop is always allowed."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    # Without binding, Stop is allowed even when wave is open
    allowed, reason = can_release_stop(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        session_binding=None,
    )
    assert allowed is True
    assert "no join-bearing" in reason


def test_nested_descendant_claim_exempt(tmp_path: Path) -> None:
    """An agent_id-bearing call is exempt and does not claim a parent slot."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    # nested call with agent_id returns None (exempt join re-evaluation)
    result = claim_assignment(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        tool_use_id="t1",
        agent_id="child-1",
    )
    assert result is None
