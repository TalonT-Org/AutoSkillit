"""Tests for the execution-layer child-outcome collector (issue #4623).

Covers ``execution/child_outcomes.py``: reading/projecting the durable
snapshot the stdlib-only hook authority writes, Claude native subagent
transcript enumeration/metadata backfill, and Codex unplanned-child
discovery via structural rollout ``sub_agent_activity`` evidence.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.execution import child_outcomes as co
from autoskillit.hooks import _child_outcome_snapshot as snap

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


# --- collect_child_outcomes ---------------------------------------------------


def test_collect_child_outcomes_returns_empty_for_no_snapshot(tmp_path) -> None:
    assert (
        co.collect_child_outcomes(
            backend="claude_code", parent_session_id="no-such-parent", log_root=tmp_path
        )
        == ()
    )


def test_collect_child_outcomes_projects_the_written_snapshot(tmp_path) -> None:
    snapshot_path = snap.resolve_snapshot_path(
        tmp_path, backend="claude_code", parent_session_id="p1"
    )
    snap.record_terminal_evidence(
        snapshot_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="c1",
        evidence_key="k1",
        evidence={"confirmed_completed": True, "evidence_source": "result"},
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="p1", log_root=tmp_path
    )
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == "completed"
    assert outcomes[0]["child_id"] == "c1"


def test_collect_child_outcomes_malformed_parent_id_returns_empty(tmp_path) -> None:
    assert (
        co.collect_child_outcomes(
            backend="claude_code", parent_session_id="../etc", log_root=tmp_path
        )
        == ()
    )


# --- Claude native subagent transcript collection -----------------------------


def _write_subagent_transcript(
    tmp_path, *, parent_session_id: str, agent_id: str, role: str, model: str
) -> None:
    subagents_dir = tmp_path / parent_session_id / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "assistant",
        "agentId": agent_id,
        "attributionAgent": role,
        "attributionSkill": "implement-worktree-no-merge",
        "message": {"id": "msg-1", "model": model},
        "sessionId": parent_session_id,
    }
    (subagents_dir / f"agent-{agent_id}.jsonl").write_text(json.dumps(record) + "\n")


def test_enumerate_claude_subagent_transcripts_finds_the_real_layout(tmp_path) -> None:
    parent_transcript = tmp_path / "parent-1.jsonl"
    parent_transcript.write_text("")
    _write_subagent_transcript(
        tmp_path, parent_session_id="parent-1", agent_id="agent-a", role="Explore", model="m1"
    )
    _write_subagent_transcript(
        tmp_path, parent_session_id="parent-1", agent_id="agent-b", role="Explore", model="m1"
    )
    transcripts = co.enumerate_claude_subagent_transcripts(parent_transcript)
    assert len(transcripts) == 2
    assert {p.name for p in transcripts} == {"agent-agent-a.jsonl", "agent-agent-b.jsonl"}


def test_enumerate_claude_subagent_transcripts_absent_directory_returns_empty(tmp_path) -> None:
    parent_transcript = tmp_path / "parent-1.jsonl"
    parent_transcript.write_text("")
    assert co.enumerate_claude_subagent_transcripts(parent_transcript) == ()


def test_collect_claude_native_children_observes_every_transcript_with_metadata(
    tmp_path,
) -> None:
    log_root = tmp_path / "logs"
    project_dir = tmp_path / "project"
    parent_transcript = project_dir / "parent-1.jsonl"
    parent_transcript.parent.mkdir(parents=True, exist_ok=True)
    parent_transcript.write_text("")
    _write_subagent_transcript(
        project_dir,
        parent_session_id="parent-1",
        agent_id="agent-a",
        role="Explore",
        model="claude-opus-5",
    )

    co.collect_claude_native_children(
        parent_session_id="parent-1", parent_transcript_path=parent_transcript, log_root=log_root
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["child_id"] == "agent-a"
    assert outcome["role"] == "Explore"
    assert outcome["effective_model"] == "claude-opus-5"
    assert outcome["attribution_skill"] == "implement-worktree-no-merge"
    # An observed transcript with no terminal evidence stays unknown, never omitted.
    assert outcome["terminal_reason"] == snap.REASON_UNKNOWN


def test_collect_claude_native_children_no_transcripts_is_a_no_op(tmp_path) -> None:
    log_root = tmp_path / "logs"
    parent_transcript = tmp_path / "project" / "parent-1.jsonl"
    parent_transcript.parent.mkdir(parents=True, exist_ok=True)
    parent_transcript.write_text("")
    co.collect_claude_native_children(
        parent_session_id="parent-1", parent_transcript_path=parent_transcript, log_root=log_root
    )
    assert (
        co.collect_child_outcomes(
            backend="claude_code", parent_session_id="parent-1", log_root=log_root
        )
        == ()
    )


def test_collect_claude_native_children_dedups_by_message_id(tmp_path) -> None:
    """Later records for the same message id win, mirroring merge_turn_usage's dedup rule."""
    log_root = tmp_path / "logs"
    project_dir = tmp_path / "project"
    parent_transcript = project_dir / "parent-1.jsonl"
    parent_transcript.parent.mkdir(parents=True, exist_ok=True)
    parent_transcript.write_text("")
    subagents_dir = project_dir / "parent-1" / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "assistant",
            "attributionAgent": "Explore",
            "attributionSkill": "skill-a",
            "message": {"id": "msg-1", "model": "model-old"},
        },
        {
            "type": "assistant",
            "attributionAgent": "Explore",
            "attributionSkill": "skill-b",
            "message": {"id": "msg-2", "model": "model-new"},
        },
    ]
    (subagents_dir / "agent-agent-a.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    co.collect_claude_native_children(
        parent_session_id="parent-1", parent_transcript_path=parent_transcript, log_root=log_root
    )
    outcome = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-1", log_root=log_root
    )[0]
    assert outcome["effective_model"] == "model-new"
    assert outcome["attribution_skill"] == "skill-b"


# --- Codex unplanned-child discovery -------------------------------------------


def _write_rollout(path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_collect_codex_observed_children_finds_started_activity(tmp_path) -> None:
    log_root = tmp_path / "logs"
    rollout_path = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout_path,
        [
            {"type": "session_meta", "payload": {"id": "parent-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "started",
                    "agent_thread_id": "child-1",
                },
            },
        ],
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path, parent_session_id="parent-1", log_root=log_root
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0]["child_id"] == "child-1"
    assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN


def test_collect_codex_observed_children_ignores_non_started_kinds_for_new_rows(
    tmp_path,
) -> None:
    """`interacted`/`interrupted`/`completed` without a prior `started` add no new row —
    only `kind == "started"` confirms a child thread exists."""
    log_root = tmp_path / "logs"
    rollout_path = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout_path,
        [
            {"type": "session_meta", "payload": {"id": "parent-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "interacted",
                    "agent_thread_id": "child-1",
                },
            },
        ],
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path, parent_session_id="parent-1", log_root=log_root
    )
    assert (
        co.collect_child_outcomes(backend="codex", parent_session_id="parent-1", log_root=log_root)
        == ()
    )


def test_collect_codex_observed_children_excludes_the_parent_id_itself(tmp_path) -> None:
    log_root = tmp_path / "logs"
    rollout_path = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout_path,
        [
            {"type": "session_meta", "payload": {"id": "parent-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "started",
                    "agent_thread_id": "parent-1",
                },
            },
        ],
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path, parent_session_id="parent-1", log_root=log_root
    )
    assert (
        co.collect_child_outcomes(backend="codex", parent_session_id="parent-1", log_root=log_root)
        == ()
    )


def test_collect_codex_observed_children_missing_rollout_is_a_no_op(tmp_path) -> None:
    log_root = tmp_path / "logs"
    co.collect_codex_observed_children(
        parent_rollout_path=tmp_path / "missing.jsonl",
        parent_session_id="parent-1",
        log_root=log_root,
    )
    assert (
        co.collect_child_outcomes(backend="codex", parent_session_id="parent-1", log_root=log_root)
        == ()
    )


def test_collect_codex_observed_children_is_idempotent_across_repeated_started_events(
    tmp_path,
) -> None:
    log_root = tmp_path / "logs"
    rollout_path = tmp_path / "rollout.jsonl"
    _write_rollout(
        rollout_path,
        [
            {"type": "session_meta", "payload": {"id": "parent-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "sub_agent_activity",
                    "kind": "started",
                    "agent_thread_id": "child-1",
                },
            },
        ],
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path, parent_session_id="parent-1", log_root=log_root
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path, parent_session_id="parent-1", log_root=log_root
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
