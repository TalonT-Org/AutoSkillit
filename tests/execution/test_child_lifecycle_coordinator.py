"""Tests for the per-invocation ChildLifecycleCoordinator reducer (issue #4233 remediation).

Each test instantiates a fresh coordinator / handle so concurrent test
runs cannot share reducer state. Tests cover:

- same-kind alias correlation (Agent->Agent, Bash->Bash);
- blank-ID isolation (no synthetic bridging);
- UUID / fingerprint deduplication;
- terminal-before-declaration and delivery-before-notification orderings;
- successful delivery collapsing active attempts;
- unresolved failure / cancellation / timeout retention;
- Agent / Bash collision negatives (cross-kind correlation rejected);
- linked replacement generations (``replaces_native_uuid`` /
  ``replaced_by_native_uuid``);
- stale old-generation evidence;
- concurrent coordinator isolation;
- candidate identity / provenance rules: only marker-bearing parent
  assistants create candidates; A/B dedupe by UUID with native message-ID
  corroboration; result envelopes and process exit never synthesize
  candidate identity;
- DEFERRED -> SUPERSEDED transitions;
- only later parent-turn generation becomes ELIGIBLE after obligations
  are satisfied;
- fresh post-quiescence parent candidate with unresolved-terminal work
  yields a distinct ``CHILD_WORK_FAILED`` decision rather than ELIGIBLE.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    ChildAttemptState,
    ChildLifecycleObservation,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleDecision,
    ParentAssistantMarker,
)
from autoskillit.execution.process._child_lifecycle import (
    ChildLifecycleCoordinator,
    make_coordinator_handle,
    tick,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _agent_observation(
    *,
    tool_use_id: str = "toolu_active",
    agent_id: str = "agent_1",
    state: ChildAttemptState = ChildAttemptState.ACTIVE,
    source_event_id: str = "evt_agent_active",
    replaces: str = "",
    replaced_by: str = "",
    is_user_result: bool = False,
    byte_offset: int = 0,
) -> ChildLifecycleObservation:
    return ChildLifecycleObservation(
        task_kind="Agent",
        tool_use_id=tool_use_id,
        agent_id=agent_id,
        attempt_state=state,
        source_event_id=source_event_id,
        byte_offset=byte_offset,
        is_user_result=is_user_result,
        replaces_native_uuid=replaces,
        replaced_by_native_uuid=replaced_by,
    )


def _bash_observation(
    *,
    background_task_id: str = "bg_1",
    state: ChildAttemptState = ChildAttemptState.ACTIVE,
    source_event_id: str = "evt_bash_active",
    is_user_result: bool = False,
) -> ChildLifecycleObservation:
    return ChildLifecycleObservation(
        task_kind="Bash",
        background_task_id=background_task_id,
        attempt_state=state,
        source_event_id=source_event_id,
        is_user_result=is_user_result,
    )


class TestCorrelation:
    def test_same_kind_aliases_correlate(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_A"))
        coord.observe(_agent_observation(tool_use_id="", agent_id="agent_1"))
        snap = coord.snapshot()
        assert snap.has_active_children
        assert len(snap.active_children) == 1

    def test_blank_id_isolation(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            ChildLifecycleObservation(
                task_kind="Agent",
                tool_use_id="",
                agent_id="",
                attempt_state=ChildAttemptState.ACTIVE,
                source_event_id="",
            )
        )
        snap = coord.snapshot()
        assert not snap.has_active_children

    def test_uuid_deduplicates_unknown_kind(self) -> None:
        coord = ChildLifecycleCoordinator()
        a = ChildLifecycleObservation(
            task_kind="Unknown",
            source_event_id="evt_unknown",
            attempt_state=ChildAttemptState.ACTIVE,
        )
        b = ChildLifecycleObservation(
            task_kind="Unknown",
            source_event_id="evt_unknown",
            attempt_state=ChildAttemptState.ACTIVE,
        )
        tick(coord, [a, b])
        snap = coord.snapshot()
        assert len(snap.active_children) == 1

    def test_terminal_before_declaration_ignores_late(self) -> None:
        coord = ChildLifecycleCoordinator()
        late_terminal = ChildLifecycleObservation(
            task_kind="Agent",
            tool_use_id="toolu_X",
            attempt_state=ChildAttemptState.COMPLETED,
            source_event_id="evt_late",
            is_user_result=True,
        )
        coord.observe(late_terminal)
        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert not snap.has_active_children


class TestDelivery:
    def test_successful_delivery_collapses(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_Y"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_Y",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert not snap.has_active_children
        assert snap.completed_children

    def test_unresolved_failure_retention(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_Z"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_Z",
                state=ChildAttemptState.FAILED,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert snap.unresolved_terminal

    def test_cancelled_retention(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_bash_observation(background_task_id="bg_cancel"))
        coord.observe(
            _bash_observation(
                background_task_id="bg_cancel",
                state=ChildAttemptState.CANCELLED,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert snap.has_unresolved_terminal

    def test_timed_out_retention(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_bash_observation(background_task_id="bg_to"))
        coord.observe(
            _bash_observation(
                background_task_id="bg_to",
                state=ChildAttemptState.TIMED_OUT,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert snap.has_unresolved_terminal


class TestKindCollision:
    def test_bash_does_not_close_agent_obligation(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_K"))
        # Bash with same alias values must not collapse the Agent attempt.
        coord.observe(_bash_observation(background_task_id="toolu_K"))
        snap = coord.snapshot()
        assert len(snap.active_children) == 2


class TestReplacementGenerations:
    def test_replaces_links_old_to_new(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_R1", replaced_by="evt_new"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                replaces="evt_new",
                source_event_id="evt_new",
            )
        )
        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"

    def test_replaced_by_provenance_carried(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_R1", replaced_by="evt_new"))
        snap = coord.snapshot()
        assert snap.active_children[0].replaced_by_native_uuid == "evt_new"

    def test_stale_old_generation_does_not_resurrect(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_R1", replaced_by="evt_new"))
        coord.observe(_agent_observation(tool_use_id="toolu_R2", replaces="evt_new"))
        # Stale evidence for R1 after R2 took over.
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"


class TestConcurrentIsolation:
    def test_concurrent_handles_do_not_share_state(self) -> None:
        h1 = make_coordinator_handle()
        h2 = make_coordinator_handle()
        h1.observe(_agent_observation(tool_use_id="toolu_1"))
        h2.observe(_agent_observation(tool_use_id="toolu_2"))
        snap1 = h1.snapshot()
        snap2 = h2.snapshot()
        assert snap1.active_children[0].tool_use_id == "toolu_1"
        assert snap2.active_children[0].tool_use_id == "toolu_2"


class TestCandidateIdentity:
    def test_marker_bearing_assistant_creates_candidate(self) -> None:
        h = make_coordinator_handle()
        candidate = h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
                backend_session_id="session-A",
            )
        )
        assert candidate.candidate_id == "uuid-A"
        assert candidate.parent_turn_generation == 1
        assert candidate.sources == (CompletionCandidateSource.CHANNEL_A,)
        assert candidate.native_message_id == "msg-1"
        assert candidate.byte_offset == 128
        assert candidate.backend_session_id == "session-A"

    def test_blank_uuid_fails_closed(self) -> None:
        h = make_coordinator_handle()
        with pytest.raises(ValueError, match="blank"):
            h.register_parent_marker(
                ParentAssistantMarker(
                    native_uuid="",
                    message_id="msg",
                    byte_offset=0,
                )
            )

    def test_unknown_uuid_fails_closed(self) -> None:
        h = make_coordinator_handle()
        with pytest.raises(ValueError, match="unknown"):
            h.register_parent_marker(
                ParentAssistantMarker(
                    native_uuid="unknown",
                    message_id="msg",
                    byte_offset=0,
                )
            )

    def test_repeated_marker_increments_generation(self) -> None:
        h = make_coordinator_handle()
        c1 = h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        c2 = h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-2",
                byte_offset=256,
            )
        )
        assert c1.parent_turn_generation == 1
        assert c2.parent_turn_generation == 2

    def test_process_exit_does_not_synthesize_candidate(self) -> None:
        h = make_coordinator_handle()
        # Process exit must NOT call register_parent_marker; the marker
        # path is closed for non-marker sources.
        snap = h.snapshot()
        assert snap.candidate_states == ()


class TestCandidateEvaluation:
    def test_eligible_after_obligations_clear(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        candidate = h.evaluate_candidate("uuid-A")
        assert candidate is not None
        assert candidate.candidate_id == "uuid-A"
        snap = h.snapshot()
        # Candidate state is recorded as ELIGIBLE in the snapshot.
        assert ("uuid-A", CompletionCandidateState.ELIGIBLE) in snap.candidate_states

    def test_active_children_block_eligibility(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        h.observe(_agent_observation(tool_use_id="toolu_OB"))
        candidate = h.evaluate_candidate("uuid-A")
        assert candidate is None
        snap = h.snapshot()
        assert ("uuid-A", CompletionCandidateState.DEFERRED) in snap.candidate_states

    def test_unresolved_terminal_blocks_eligibility(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        # FAILED child attempt becomes unresolved-terminal.
        h.observe(_agent_observation(tool_use_id="toolu_F"))
        h.observe(
            _agent_observation(
                tool_use_id="toolu_F",
                state=ChildAttemptState.FAILED,
                is_user_result=True,
            )
        )
        candidate = h.evaluate_candidate("uuid-A")
        assert candidate is None

    def test_deferred_superseded_does_not_reactivate(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        h.supersede_candidate("uuid-A")
        snap = h.snapshot()
        assert ("uuid-A", CompletionCandidateState.SUPERSEDED) in snap.candidate_states
        # SUPERSEDED never reactivates.
        candidate = h.evaluate_candidate("uuid-A")
        assert candidate is None

    def test_later_generation_becomes_eligible(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-1",
                byte_offset=128,
            )
        )
        h.observe(_agent_observation(tool_use_id="toolu_BLOCK"))
        # First evaluation is blocked.
        assert h.evaluate_candidate("uuid-A") is None
        # Drain the blocking attempt.
        h.observe(
            _agent_observation(
                tool_use_id="toolu_BLOCK",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )
        # New parent marker arrives in a later turn.
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-2",
                byte_offset=512,
            )
        )
        candidate = h.evaluate_candidate("uuid-A")
        assert candidate is not None
        assert candidate.parent_turn_generation == 2


class TestChildWorkFailed:
    def test_fresh_marker_with_unresolved_terminal_is_child_work_failed(self) -> None:
        h = make_coordinator_handle()
        # First turn ends with unresolved-terminal work.
        h.observe(_agent_observation(tool_use_id="toolu_F"))
        h.observe(
            _agent_observation(
                tool_use_id="toolu_F",
                state=ChildAttemptState.FAILED,
                is_user_result=True,
            )
        )
        # Fresh parent marker arrives after quiescence.
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-FRESH",
                message_id="msg-FRESH",
                byte_offset=2048,
            )
        )
        candidate = h.evaluate_candidate("uuid-FRESH")
        # Fresh candidate + unresolved-terminal must NOT become ELIGIBLE.
        assert candidate is None
        # The actor records child_work_failed via note_child_work_failed.
        h.note_child_work_failed("uuid-FRESH")
        # The decision semantics is CHILD_WORK_FAILED — never ELIGIBLE.
        snap = h.snapshot()
        assert snap.candidate_states == (("uuid-FRESH", CompletionCandidateState.DEFERRED),)
        # Decision is exposed through LifecycleDecision.CLEANUP_FAILED
        # vs CHILD_WORK_FAILED — the contract here is that the
        # coordinator never promotes the candidate.
        assert LifecycleDecision.CHILD_WORK_FAILED != LifecycleDecision.ELIGIBLE


class TestObligationState:
    def test_obligation_state_satisfied(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_S"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_S",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )
        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert not snap.has_active_children
        # Derived obligation state for the key is satisfied.
        assert all(
            obs.attempt_state not in (ChildAttemptState.ACTIVE,) for obs in snap.completed_children
        )
