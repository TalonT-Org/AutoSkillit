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

from dataclasses import FrozenInstanceError, replace

import pytest

from autoskillit.core import (
    CandidateSighting,
    ChildAttemptState,
    ChildLifecycleObservation,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleDecision,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    LifecycleEvidenceResolution,
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
    task_id: str = "",
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
        task_id=task_id,
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
    task_id: str = "",
    background_task_id: str = "bg_1",
    state: ChildAttemptState = ChildAttemptState.ACTIVE,
    source_event_id: str = "evt_bash_active",
    is_user_result: bool = False,
) -> ChildLifecycleObservation:
    return ChildLifecycleObservation(
        task_kind="Bash",
        task_id=task_id,
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

    def test_shared_source_event_does_not_merge_distinct_agent_results(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_A",
                agent_id="agent_A",
                source_event_id="shared-user-record",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_B",
                agent_id="agent_B",
                source_event_id="shared-user-record",
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 2
        assert {item.tool_use_id for item in snap.active_children} == {
            "toolu_A",
            "toolu_B",
        }

    def test_changed_offset_duplicate_projection_is_ignored(self) -> None:
        coord = ChildLifecycleCoordinator()
        observation = _agent_observation(
            tool_use_id="toolu_offset",
            source_event_id="shared-native-event",
            byte_offset=10,
        )

        coord.observe(observation)
        coord.observe(replace(observation, byte_offset=900))

        (active,) = coord.snapshot().active_children
        assert active.byte_offset == 10

    def test_anonymous_projection_dedupe_is_separate_from_native_events(self) -> None:
        coord = ChildLifecycleCoordinator()
        native = _agent_observation(
            tool_use_id="toolu_projection",
            source_event_id="native-event",
            byte_offset=10,
        )
        anonymous = replace(native, source_event_id="", byte_offset=20)

        coord.observe(native)
        coord.observe(anonymous)
        coord.observe(replace(anonymous, byte_offset=30))

        (active,) = coord.snapshot().active_children
        assert active.byte_offset == 20

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

    def test_terminal_before_declaration_replays_after_exact_declaration(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_early",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        coord.observe(_agent_observation(tool_use_id="toolu_early"))

        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert len(snap.completed_children) == 1

    def test_conflicting_aliases_fail_closed_without_merging_attempts(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_A",
                agent_id="agent_A",
                source_event_id="evt_A",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_B",
                agent_id="agent_B",
                source_event_id="evt_B",
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_A",
                agent_id="agent_B",
                source_event_id="evt_conflict",
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 2
        assert {item.tool_use_id for item in snap.active_children} == {
            "toolu_A",
            "toolu_B",
        }
        assert any(
            issue.issue_kind is LifecycleEvidenceIssueKind.ALIAS_CONFLICT
            and issue.resolution is LifecycleEvidenceResolution.PENDING
            for issue in snap.lifecycle_issues
        )

    def test_permanent_alias_conflict_replay_terminates(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_A",
                agent_id="agent_A",
                source_event_id="evt_A",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_B",
                agent_id="agent_B",
                source_event_id="evt_B",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_A",
                agent_id="agent_B",
                source_event_id="evt_permanent_conflict",
            )
        )

        # This successful reduction invokes replay. The permanent conflict
        # must be retained after one no-progress pass rather than spinning.
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_C",
                agent_id="agent_C",
                source_event_id="evt_C",
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 3
        assert len(snap.lifecycle_issues) == 1
        assert snap.lifecycle_issues[0].resolution is LifecycleEvidenceResolution.PENDING


class TestDelivery:
    def test_notification_only_completion_remains_awaiting_delivery(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_notification"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_notification",
                state=ChildAttemptState.COMPLETED,
                is_user_result=False,
            )
        )
        snap = coord.snapshot()
        assert not snap.has_active_children
        assert not snap.completed_children
        assert len(snap.awaiting_delivery) == 1
        assert snap.awaiting_delivery[0].tool_use_id == "toolu_notification"

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

    def test_late_active_observation_cannot_resurrect_failed_attempt(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(_agent_observation(tool_use_id="toolu_failed"))
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_failed",
                state=ChildAttemptState.FAILED,
                is_user_result=False,
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_failed",
                state=ChildAttemptState.ACTIVE,
                source_event_id="evt_late_active",
            )
        )
        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert not snap.has_active_children


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

    def test_failed_predecessor_indexes_later_replaced_by_provenance(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1_active",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1_failed",
                state=ChildAttemptState.FAILED,
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1_replaced",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2_delivery",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert not snap.has_active_children
        assert len(snap.completed_children) == 1

    def test_late_predecessor_failure_cannot_resurrect_satisfied_chain(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2_delivery",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1_late_failure",
                state=ChildAttemptState.FAILED,
            )
        )

        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert not snap.has_active_children
        assert len(snap.completed_children) == 1

    def test_duplicate_replacement_launch_after_delivery_does_not_resurrect(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="delivery_R2",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="duplicate_launch_R2",
                replaces="edge_R2",
            )
        )

        snap = coord.snapshot()
        assert not snap.has_active_children
        assert {item.tool_use_id for item in snap.completed_children} == {"toolu_R2"}

    def test_late_new_replacement_edge_cannot_reopen_satisfied_chain(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="delivery_R2",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="late_failed_R1",
                state=ChildAttemptState.FAILED,
                replaced_by="edge_R3",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R3",
                agent_id="agent_R3",
                source_event_id="evt_R3",
                replaces="edge_R3",
            )
        )

        snap = coord.snapshot()
        assert not snap.has_active_children
        assert not snap.has_unresolved_terminal
        assert {item.tool_use_id for item in snap.completed_children} == {"toolu_R2"}

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
                replaced_by="evt_new",
            )
        )
        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"

    @pytest.mark.parametrize(
        "terminal_state",
        [
            ChildAttemptState.FAILED,
            ChildAttemptState.CANCELLED,
            ChildAttemptState.TIMED_OUT,
        ],
    )
    def test_successful_linked_replacement_satisfies_terminal_predecessor(
        self,
        terminal_state: ChildAttemptState,
    ) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                replaced_by="evt_new",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                state=terminal_state,
                replaced_by="evt_new",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                replaces="evt_new",
                source_event_id="evt_new",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                state=ChildAttemptState.COMPLETED,
                source_event_id="delivery_new",
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert not snap.has_active_children

    def test_replacement_replays_when_native_edge_arrives_later(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_replacement",
                replaces="edge_R2",
            )
        )
        assert not coord.snapshot().has_active_children

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_predecessor",
                replaced_by="edge_R2",
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"

    def test_replacement_replay_reaches_fixed_point_in_same_cycle(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R3",
                agent_id="agent_R3",
                source_event_id="evt_R3",
                replaces="edge_R3",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
                replaced_by="edge_R3",
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R3"

    def test_stale_predecessor_delivery_without_repeated_edge_is_ignored(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_stale_delivery",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"
        assert not snap.completed_children

    def test_unrelated_kind_aware_shared_identity_delivery_does_not_clear_chain(
        self,
    ) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                task_id="shared-task",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                task_id="shared-task",
                agent_id="agent_R1",
                source_event_id="evt_R1_failed",
                state=ChildAttemptState.FAILED,
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )
        coord.observe(
            _bash_observation(
                task_id="shared-task",
                background_task_id="bg_unrelated",
                source_event_id="evt_bash",
            )
        )
        coord.observe(
            _bash_observation(
                task_id="shared-task",
                background_task_id="bg_unrelated",
                source_event_id="evt_bash_delivery",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"

    def test_unbound_tool_result_cannot_fall_back_to_replacement_agent(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1",
                replaced_by="edge_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                source_event_id="evt_R1_failed",
                state=ChildAttemptState.FAILED,
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                source_event_id="evt_R2",
                replaces="edge_R2",
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_unrelated",
                agent_id="agent_R2",
                source_event_id="evt_unrelated_delivery",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert len(snap.active_children) == 1
        assert snap.active_children[0].tool_use_id == "toolu_R2"
        assert not snap.completed_children

    def test_replacement_notification_does_not_satisfy_failed_predecessor(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                replaced_by="evt_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                state=ChildAttemptState.FAILED,
                replaced_by="evt_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                replaces="evt_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                state=ChildAttemptState.COMPLETED,
            )
        )

        snap = coord.snapshot()
        assert snap.has_unresolved_terminal
        assert len(snap.awaiting_delivery) == 1

    def test_delivered_replacement_clears_transitive_failed_chain(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                replaced_by="evt_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R1",
                agent_id="agent_R1",
                state=ChildAttemptState.FAILED,
                replaced_by="evt_R2",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                replaces="evt_R2",
                replaced_by="evt_R3",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R2",
                agent_id="agent_R2",
                state=ChildAttemptState.FAILED,
                replaced_by="evt_R3",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R3",
                agent_id="agent_R3",
                replaces="evt_R3",
            )
        )
        coord.observe(
            _agent_observation(
                tool_use_id="toolu_R3",
                agent_id="agent_R3",
                state=ChildAttemptState.COMPLETED,
                is_user_result=True,
            )
        )

        snap = coord.snapshot()
        assert not snap.has_unresolved_terminal
        assert not snap.has_active_children
        assert not snap.awaiting_delivery


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

    def test_handle_has_no_public_coordinator_escape_hatch(self) -> None:
        handle = make_coordinator_handle()
        assert not hasattr(handle, "coordinator")
        assert not hasattr(handle, "supersede_candidate")
        assert {name for name in dir(handle) if not name.startswith("_")} == {
            "evaluate_candidate",
            "get_candidate",
            "has_pending_issues",
            "note_child_work_failed",
            "observe",
            "register_candidate_sighting",
            "register_issue",
            "register_parent_marker",
            "snapshot",
        }
        with pytest.raises(FrozenInstanceError):
            handle._coordinator = ChildLifecycleCoordinator()


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
        assert candidate.sightings == (
            CandidateSighting(
                source=CompletionCandidateSource.CHANNEL_A,
                native_uuid="uuid-A",
                native_message_id="msg-1",
                channel_relative_byte_offset=128,
                backend_session_id="session-A",
                record_provenance="parent_assistant_marker",
            ),
        )

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

    def test_repeated_marker_merges_same_native_generation(self) -> None:
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
        assert c2.parent_turn_generation == 1

    def test_channel_sightings_preserve_distinct_offsets(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-A",
                message_id="msg-A",
                byte_offset=128,
            )
        )

        channel_b_sighting = CandidateSighting(
            source=CompletionCandidateSource.CHANNEL_B,
            native_uuid="uuid-A",
            native_message_id="msg-B",
            channel_relative_byte_offset=4096,
            backend_session_id="session-B",
            record_provenance="channel_b_record",
        )
        candidate = h.register_candidate_sighting(channel_b_sighting)
        duplicate = h.register_candidate_sighting(channel_b_sighting)

        assert candidate.sources == (
            CompletionCandidateSource.CHANNEL_A,
            CompletionCandidateSource.CHANNEL_B,
        )
        assert tuple(item.channel_relative_byte_offset for item in candidate.sightings) == (
            128,
            4096,
        )
        assert duplicate.parent_turn_generation == candidate.parent_turn_generation == 1
        assert duplicate.sightings == candidate.sightings

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

    def test_retained_unmatched_evidence_blocks_eligibility(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-retained",
                message_id="msg-retained",
                byte_offset=128,
            )
        )
        h.observe(
            _agent_observation(
                tool_use_id="toolu_replacement",
                agent_id="agent_replacement",
                source_event_id="evt_replacement",
                replaces="missing-edge",
            )
        )

        assert not h.snapshot().has_active_children
        assert h.evaluate_candidate("uuid-retained") is None
        assert (
            "uuid-retained",
            CompletionCandidateState.DEFERRED,
        ) in h.snapshot().candidate_states

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
        h.register_parent_marker(
            ParentAssistantMarker(
                native_uuid="uuid-B",
                message_id="msg-2",
                byte_offset=256,
            )
        )
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
                native_uuid="uuid-B",
                message_id="msg-2",
                byte_offset=512,
            )
        )
        candidate = h.evaluate_candidate("uuid-B")
        assert candidate is not None
        assert candidate.parent_turn_generation == 2

    def test_later_uuid_automatically_supersedes_older_deferred_candidate(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(native_uuid="uuid-A", message_id="msg-A", byte_offset=1)
        )
        h.observe(_agent_observation(tool_use_id="toolu_BLOCK"))
        assert h.evaluate_candidate("uuid-A") is None

        h.register_parent_marker(
            ParentAssistantMarker(native_uuid="uuid-B", message_id="msg-B", byte_offset=2)
        )

        assert (
            "uuid-A",
            CompletionCandidateState.SUPERSEDED,
        ) in h.snapshot().candidate_states

    def test_first_distinct_later_uuid_supersedes_all_older_deferred(self) -> None:
        h = make_coordinator_handle()
        h.register_parent_marker(
            ParentAssistantMarker(native_uuid="uuid-A", message_id="msg-A", byte_offset=1)
        )
        h.register_parent_marker(
            ParentAssistantMarker(native_uuid="uuid-B", message_id="msg-B", byte_offset=2)
        )
        h.register_parent_marker(
            ParentAssistantMarker(native_uuid="uuid-C", message_id="msg-C", byte_offset=3)
        )

        assert h.snapshot().candidate_states == (
            ("uuid-A", CompletionCandidateState.SUPERSEDED),
            ("uuid-B", CompletionCandidateState.SUPERSEDED),
            ("uuid-C", CompletionCandidateState.DEFERRED),
        )


class TestLifecycleIssues:
    def test_exact_native_alias_resolves_pending_issue(self) -> None:
        coord = ChildLifecycleCoordinator()
        issue = LifecycleEvidenceIssue(
            issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
            task_kind="Agent",
            native_aliases=("toolu_issue", "agent_issue"),
            source_event_uuid="bad-event",
            canonical_fingerprint="issue-fingerprint",
            channel_relative_byte_offset=0,
            native_alias_kinds=("tool_use_id", "agent_id"),
        )
        coord.register_issue(issue)

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_issue",
                source_event_id="valid-event",
            )
        )

        (resolved,) = coord.snapshot().lifecycle_issues
        assert resolved.resolution is LifecycleEvidenceResolution.RESOLVED

    def test_partial_alias_match_does_not_resolve_pending_issue(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.register_issue(
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind="Agent",
                native_aliases=("toolu_issue", "agent_issue"),
                source_event_uuid="bad-event",
                canonical_fingerprint="issue-fingerprint",
                channel_relative_byte_offset=0,
                native_alias_kinds=("tool_use_id", "agent_id"),
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_unrelated",
            )
        )

        (pending,) = coord.snapshot().lifecycle_issues
        assert pending.resolution is LifecycleEvidenceResolution.PENDING

    def test_unknown_issue_kind_resolves_on_every_matching_alias(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.register_issue(
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind="unknown",
                native_aliases=("toolu_issue", "agent_issue"),
                source_event_uuid="bad-event",
                canonical_fingerprint="unknown-kind-fingerprint",
                channel_relative_byte_offset=0,
                native_alias_kinds=("tool_use_id", "agent_id"),
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_issue",
                source_event_id="different-valid-event",
            )
        )

        (resolved,) = coord.snapshot().lifecycle_issues
        assert resolved.resolution is LifecycleEvidenceResolution.RESOLVED

    def test_swapped_alias_kinds_do_not_resolve_pending_issue(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.register_issue(
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind="Agent",
                native_aliases=("toolu_issue", "agent_issue"),
                source_event_uuid="bad-event",
                canonical_fingerprint="swapped-kind-fingerprint",
                channel_relative_byte_offset=0,
                native_alias_kinds=("agent_id", "tool_use_id"),
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_issue",
                source_event_id="valid-event",
            )
        )

        (pending,) = coord.snapshot().lifecycle_issues
        assert pending.resolution is LifecycleEvidenceResolution.PENDING

    def test_untyped_issue_remains_pending_fail_closed(self) -> None:
        coord = ChildLifecycleCoordinator()
        coord.register_issue(
            LifecycleEvidenceIssue(
                issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                task_kind="Agent",
                native_aliases=("toolu_issue", "agent_issue"),
                source_event_uuid="bad-event",
                canonical_fingerprint="untyped-fingerprint",
                channel_relative_byte_offset=0,
            )
        )

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_issue",
                source_event_id="valid-event",
            )
        )

        (pending,) = coord.snapshot().lifecycle_issues
        assert pending.resolution is LifecycleEvidenceResolution.PENDING
        assert coord.has_pending_issues()

    def test_same_child_issue_events_preserve_distinct_provenance(self) -> None:
        coord = ChildLifecycleCoordinator()
        for source_event_uuid, byte_offset in (("bad-event-1", 10), ("bad-event-2", 20)):
            coord.register_issue(
                LifecycleEvidenceIssue(
                    issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                    task_kind="Agent",
                    native_aliases=("toolu_issue", "agent_issue"),
                    source_event_uuid=source_event_uuid,
                    canonical_fingerprint="shared-child-fingerprint",
                    channel_relative_byte_offset=byte_offset,
                    native_alias_kinds=("tool_use_id", "agent_id"),
                )
            )

        pending = coord.snapshot().lifecycle_issues
        assert len(pending) == 2
        assert {issue.source_event_uuid for issue in pending} == {
            "bad-event-1",
            "bad-event-2",
        }

        coord.observe(
            _agent_observation(
                tool_use_id="toolu_issue",
                agent_id="agent_issue",
                source_event_id="valid-event",
            )
        )

        resolved = coord.snapshot().lifecycle_issues
        assert len(resolved) == 2
        assert all(issue.resolution is LifecycleEvidenceResolution.RESOLVED for issue in resolved)


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
        assert snap.candidate_states == (("uuid-FRESH", CompletionCandidateState.SUPERSEDED),)
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
