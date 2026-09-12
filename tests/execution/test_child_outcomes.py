"""Tests for the execution-layer child-outcome collector (issue #4623).

Covers ``execution/child_outcomes.py``: reading/projecting the durable
snapshot the stdlib-only hook authority writes, Claude native subagent
transcript enumeration/metadata backfill, Codex unplanned-child discovery
via structural rollout ``sub_agent_activity`` evidence, and the ``Step 5``
``ManagedAttemptRecorder``/module-function writers used by the managed-leaf
executor to record every physical attempt's outcome.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core import ApiFailureOutcome, InfraOutcome, RetryReason, SkillResult
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
            "message": {"id": "msg-1", "model": "model-new"},
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
    child_path = tmp_path / "child-1.jsonl"
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
    _write_rollout(
        child_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-1",
                    "parent_thread_id": "parent-1",
                    "agent_role": "plan-foundation-auditor",
                },
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-sol", "effort": "medium"},
            },
        ],
    )
    published = co.collect_codex_observed_children(
        parent_rollout_path=rollout_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda child_id: child_path if child_id == "child-1" else None,
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0] == {
        "child_id": "child-1",
        "launch_alias": "",
        "backend": "codex",
        "parent_session_id": "parent-1",
        "role": "plan-foundation-auditor",
        "attribution_skill": "",
        "effective_model": "gpt-5.6-sol",
        "effective_effort": "medium",
        "effective_provider": "",
        "terminal_reason": snap.REASON_UNKNOWN,
        "raw_reason": "",
        "raw_subtype": "",
        "raw_code": "",
        "evidence_source": "codex_rollout_metadata",
        "transcript_locator": str(child_path),
        "start_confirmed": True,
    }
    assert published is True


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
    published = co.collect_codex_observed_children(
        parent_rollout_path=rollout_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    assert published is True
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
        parent_rollout_path=rollout_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    assert (
        co.collect_child_outcomes(backend="codex", parent_session_id="parent-1", log_root=log_root)
        == ()
    )


def test_collect_codex_observed_children_missing_rollout_is_a_no_op(tmp_path) -> None:
    log_root = tmp_path / "logs"
    published = co.collect_codex_observed_children(
        parent_rollout_path=tmp_path / "missing.jsonl",
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    assert published is False
    assert (
        co.collect_child_outcomes(backend="codex", parent_session_id="parent-1", log_root=log_root)
        == ()
    )


def test_collect_codex_observed_children_retains_invalid_child_metadata(tmp_path) -> None:
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "child.jsonl"
    log_root = tmp_path / "logs"
    _write_rollout(
        parent_path,
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
    _write_rollout(child_path, [{"type": "session_meta", "payload": {"id": "wrong-child"}}])

    assert not co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: child_path,
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0]["role"] == ""


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
        parent_rollout_path=rollout_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    co.collect_codex_observed_children(
        parent_rollout_path=rollout_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1


def test_collect_codex_observed_children_refines_metadata_as_rollout_appears(
    tmp_path,
) -> None:
    log_root = tmp_path / "logs"
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "misleading-task-name.jsonl"
    _write_rollout(
        parent_path,
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

    def resolver(_child_id):
        return child_path if child_path.exists() else None

    assert not co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=resolver,
    )
    unresolved = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )[0]
    assert unresolved["role"] == ""

    _write_rollout(
        child_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-1",
                    "parent_thread_id": "parent-1",
                    "agent_role": "plan-foundation-auditor",
                },
            },
            {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        ],
    )
    assert co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=resolver,
    )
    _write_rollout(
        child_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-1",
                    "parent_thread_id": "parent-1",
                    "agent_role": "plan-foundation-auditor",
                },
            },
            {
                "type": "turn_context",
                "payload": {"model": "gpt-5.6-sol", "effort": "medium"},
            },
        ],
    )
    assert co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=resolver,
    )

    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert len(outcomes) == 1
    assert outcomes[0]["role"] == "plan-foundation-auditor"
    assert outcomes[0]["effective_model"] == "gpt-5.6-sol"
    assert outcomes[0]["effective_effort"] == "medium"


def test_collect_codex_observed_children_does_not_infer_a_null_native_role(
    tmp_path,
) -> None:
    log_root = tmp_path / "logs"
    parent_path = tmp_path / "parent.jsonl"
    child_path = tmp_path / "plan-foundation-auditor-task.jsonl"
    _write_rollout(
        parent_path,
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
    _write_rollout(
        child_path,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "child-1",
                    "parent_thread_id": "parent-1",
                    "agent_role": None,
                    "base_instructions": "role: plan-foundation-auditor",
                },
            }
        ],
    )

    assert co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: child_path,
    )
    outcome = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )[0]
    assert outcome["role"] == ""


def test_collect_codex_observed_children_rejects_parent_rollout_mismatch(tmp_path) -> None:
    parent_path = tmp_path / "parent.jsonl"
    _write_rollout(parent_path, [{"type": "session_meta", "payload": {"id": "other"}}])

    assert not co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=tmp_path / "logs",
        child_rollout_resolver=lambda _child_id: None,
    )
    assert (
        co.collect_child_outcomes(
            backend="codex", parent_session_id="parent-1", log_root=tmp_path / "logs"
        )
        == ()
    )


def test_collect_codex_observed_children_continues_after_snapshot_write_failure(
    tmp_path, monkeypatch
) -> None:
    log_root = tmp_path / "logs"
    parent_path = tmp_path / "parent.jsonl"
    _write_rollout(
        parent_path,
        [
            {"type": "session_meta", "payload": {"id": "parent-1"}},
            *(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "sub_agent_activity",
                        "kind": "started",
                        "agent_thread_id": child_id,
                    },
                }
                for child_id in ("child-1", "child-2")
            ),
        ],
    )
    original_observe = co.observe_child

    def fail_first_child(snapshot_path, **kwargs):
        if kwargs["child_id"] == "child-1":
            raise OSError("simulated write failure")
        original_observe(snapshot_path, **kwargs)

    monkeypatch.setattr(co, "observe_child", fail_first_child)

    assert not co.collect_codex_observed_children(
        parent_rollout_path=parent_path,
        parent_session_id="parent-1",
        log_root=log_root,
        child_rollout_resolver=lambda _child_id: None,
    )
    outcomes = co.collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_root
    )
    assert [outcome["child_id"] for outcome in outcomes] == ["child-2"]


def test_codex_role_rows_preserve_distinct_parent_child_pairs(tmp_path) -> None:
    log_root = tmp_path / "logs"
    rows = []
    expected_runs = set()
    for suffix in ("a", "b"):
        parent_id = f"parent-{suffix}"
        child_id = f"child-{suffix}"
        parent_path = tmp_path / f"{parent_id}.jsonl"
        child_path = tmp_path / f"{child_id}.jsonl"
        _write_rollout(
            parent_path,
            [
                {"type": "session_meta", "payload": {"id": parent_id}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "sub_agent_activity",
                        "kind": "started",
                        "agent_thread_id": child_id,
                    },
                },
            ],
        )
        _write_rollout(
            child_path,
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": child_id,
                        "parent_thread_id": parent_id,
                        "agent_role": "plan-foundation-auditor",
                    },
                },
                {
                    "type": "turn_context",
                    "payload": {"model": "gpt-5.6-sol", "effort": "medium"},
                },
            ],
        )
        assert co.collect_codex_observed_children(
            parent_rollout_path=parent_path,
            parent_session_id=parent_id,
            log_root=log_root,
            child_rollout_resolver=lambda _child_id, path=child_path: path,
        )
        rows.extend(
            co.collect_child_outcomes(
                backend="codex", parent_session_id=parent_id, log_root=log_root
            )
        )
        expected_runs.add((parent_id, child_id))

    grouped = {
        ("codex", "plan-foundation-auditor"): {
            (row["parent_session_id"], row["child_id"]) for row in rows
        }
    }
    assert grouped == {("codex", "plan-foundation-auditor"): expected_runs}


# --- Step 5: managed-attempt recording (module functions + ManagedAttemptRecorder) --------


def _minimal_skill_result(
    *,
    success: bool = True,
    subtype: str = "success",
    is_error: bool = False,
    cli_subtype: str = "",
    api_terminal_reason: str = "",
    infra_exit_category: str = "",
    session_id: str = "",
) -> SkillResult:
    """Build a SkillResult exposing only the fields record_managed_child_attempt_outcome reads."""
    return SkillResult(
        success=success,
        result="",
        session_id=session_id,
        subtype=subtype,
        is_error=is_error,
        exit_code=0 if success else 1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        cli_subtype=cli_subtype,
        api_failure=ApiFailureOutcome(terminal_reason=api_terminal_reason),
        infra=InfraOutcome(exit_category=infra_exit_category),
    )


def test_observe_managed_child_attempt_writes_unknown_row_with_metadata(tmp_path) -> None:
    co.observe_managed_child_attempt(
        log_root=tmp_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="c1",
        role="Explore",
        attribution_skill="do-a",
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="p1", log_root=tmp_path
    )
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN
    assert outcomes[0]["role"] == "Explore"
    assert outcomes[0]["attribution_skill"] == "do-a"


def test_bind_managed_child_launch_alias_merges_into_same_row(tmp_path) -> None:
    co.observe_managed_child_attempt(
        log_root=tmp_path, backend="claude_code", parent_session_id="p1", child_id="c1"
    )
    co.bind_managed_child_launch_alias(
        log_root=tmp_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="c1",
        launch_alias="native-1",
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="p1", log_root=tmp_path
    )
    assert len(outcomes) == 1
    assert outcomes[0]["launch_alias"] == "native-1"


def test_bind_managed_child_launch_alias_empty_alias_is_a_no_op(tmp_path) -> None:
    co.observe_managed_child_attempt(
        log_root=tmp_path, backend="claude_code", parent_session_id="p1", child_id="c1"
    )
    co.bind_managed_child_launch_alias(
        log_root=tmp_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="c1",
        launch_alias="",
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="p1", log_root=tmp_path
    )
    assert len(outcomes) == 1
    assert outcomes[0]["launch_alias"] == ""


def test_record_managed_child_attempt_outcome_writes_completed(tmp_path) -> None:
    co.record_managed_child_attempt_outcome(
        log_root=tmp_path,
        backend="claude_code",
        parent_session_id="p1",
        child_id="c1",
        skill_result=_minimal_skill_result(),
        evidence_source="attempt_final",
    )
    outcomes = co.collect_child_outcomes(
        backend="claude_code", parent_session_id="p1", log_root=tmp_path
    )
    assert len(outcomes) == 1
    assert outcomes[0]["terminal_reason"] == "completed"


class TestManagedAttemptRecorder:
    """Covers ``ManagedAttemptRecorder`` — the invocation-local, cache-free wrapper the
    managed-leaf executor constructs once per ``_execute_claude_headless`` call."""

    def test_noop_recorder_is_safe_and_writes_nothing(self, tmp_path) -> None:
        """log_root=None (ordinary L1/L3 session): every method is a safe no-op."""
        recorder = co.ManagedAttemptRecorder(
            log_root=None,
            backend="",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        spawn_calls: list[tuple[int, int]] = []
        recorder.on_spawn(123, 0, downstream=lambda pid, extra: spawn_calls.append((pid, extra)))
        alias_calls: list[str] = []
        recorder.bind_launch_alias("native-1", downstream=lambda sid: alias_calls.append(sid))
        recorder.record_outcome(_minimal_skill_result(), "attempt_final")
        recorder.record_exception_outcome(SkillResult.cancelled(), "cancelled")

        assert spawn_calls == [(123, 0)]
        assert alias_calls == ["native-1"]
        assert not (tmp_path / "child-outcomes").exists()
        assert (
            co.collect_child_outcomes(
                backend="claude_code", parent_session_id="p1", log_root=tmp_path
            )
            == ()
        )

    def test_start_attempt_on_spawn_writes_one_unknown_row_and_calls_downstream(
        self, tmp_path
    ) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="Explore",
            attribution_skill="do-a",
        )
        recorder.start_attempt("c1")
        spawn_calls: list[tuple[int, int]] = []
        recorder.on_spawn(123, 0, downstream=lambda pid, extra: spawn_calls.append((pid, extra)))

        assert spawn_calls == [(123, 0)]
        outcomes = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )
        assert len(outcomes) == 1
        assert outcomes[0]["child_id"] == "c1"
        assert outcomes[0]["terminal_reason"] == snap.REASON_UNKNOWN
        assert outcomes[0]["role"] == "Explore"
        assert outcomes[0]["attribution_skill"] == "do-a"

    def test_on_spawn_tolerates_a_none_downstream(self, tmp_path) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.on_spawn(123, 0, downstream=None)
        outcomes = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )
        assert len(outcomes) == 1

    def test_bind_launch_alias_merges_into_same_row_and_calls_downstream(self, tmp_path) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.on_spawn(123, 0, downstream=None)
        alias_calls: list[str] = []
        recorder.bind_launch_alias("native-1", downstream=lambda sid: alias_calls.append(sid))

        assert alias_calls == ["native-1"]
        outcomes = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )
        assert len(outcomes) == 1
        assert outcomes[0]["launch_alias"] == "native-1"

    @pytest.mark.parametrize(
        ("skill_result", "expected_reason"),
        [
            pytest.param(
                _minimal_skill_result(success=True, is_error=False, subtype="success"),
                "completed",
                id="normal_success",
            ),
            pytest.param(
                _minimal_skill_result(
                    success=True,
                    is_error=False,
                    subtype="success",
                    api_terminal_reason="api_error",
                ),
                "error",
                id="api_error_outranks_success_subtype",
            ),
            pytest.param(
                _minimal_skill_result(
                    success=False, is_error=True, subtype="error", cli_subtype="error_max_turns"
                ),
                "turn_limited",
                id="turn_limited",
            ),
            pytest.param(
                _minimal_skill_result(
                    success=False,
                    is_error=True,
                    subtype="error",
                    infra_exit_category="context_exhausted",
                ),
                "context_exhausted",
                id="context_exhausted",
            ),
            pytest.param(SkillResult.cancelled(), "interrupted", id="cancelled"),
        ],
    )
    def test_record_outcome_maps_each_shaped_skill_result(
        self, tmp_path, skill_result, expected_reason
    ) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.record_outcome(skill_result, "attempt_final")
        outcome = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )[0]
        assert outcome["terminal_reason"] == expected_reason

    def test_record_outcome_crash_shaped_result_stays_unknown_with_raw_evidence(
        self, tmp_path
    ) -> None:
        """A crash/infrastructure-fault result carries no classifying evidence key, so it
        stays unknown — but the raw subtype is still preserved for diagnosis, never omitted."""
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.record_outcome(SkillResult.crashed(Exception("boom")), "crashed")
        outcome = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )[0]
        assert outcome["terminal_reason"] == snap.REASON_UNKNOWN
        assert outcome["raw_reason"] == "crashed"

    def test_record_exception_outcome_without_confirmed_spawn_writes_nothing(
        self, tmp_path
    ) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.record_exception_outcome(SkillResult.cancelled(), "cancelled")
        assert (
            co.collect_child_outcomes(
                backend="claude_code", parent_session_id="p1", log_root=tmp_path
            )
            == ()
        )

    def test_record_exception_outcome_after_confirmed_spawn_writes_a_row(self, tmp_path) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="",
            attribution_skill="",
        )
        recorder.start_attempt("c1")
        recorder.on_spawn(123, 0, downstream=None)
        recorder.record_exception_outcome(SkillResult.cancelled(), "cancelled")
        outcomes = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )
        assert len(outcomes) == 1
        assert outcomes[0]["terminal_reason"] == "interrupted"

    def test_two_attempts_on_the_same_recorder_produce_two_distinct_rows(self, tmp_path) -> None:
        recorder = co.ManagedAttemptRecorder(
            log_root=tmp_path,
            backend="claude_code",
            parent_session_id="p1",
            role="Explore",
            attribution_skill="do-a",
        )
        recorder.start_attempt("c1")
        recorder.on_spawn(111, 0, downstream=None)
        recorder.record_outcome(
            _minimal_skill_result(success=False, is_error=False, subtype="stale"),
            "attempt_final",
        )
        recorder.start_attempt("c2")
        recorder.on_spawn(222, 0, downstream=None)
        recorder.record_outcome(_minimal_skill_result(), "attempt_final")

        outcomes = co.collect_child_outcomes(
            backend="claude_code", parent_session_id="p1", log_root=tmp_path
        )
        assert len(outcomes) == 2
        assert {o["child_id"] for o in outcomes} == {"c1", "c2"}
        by_id = {o["child_id"]: o for o in outcomes}
        assert by_id["c1"]["terminal_reason"] == snap.REASON_UNKNOWN
        assert by_id["c1"]["raw_reason"] == "stale"
        assert by_id["c2"]["terminal_reason"] == "completed"
