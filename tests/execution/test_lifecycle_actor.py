"""Transport and reducer tests for the invocation-local lifecycle actor."""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path
from typing import Any

import anyio
import pytest

from autoskillit.core import (
    BackendEventKind,
    CandidateSighting,
    ChannelBStatus,
    ChildAttemptState,
    ChildLifecycleObservation,
    CompletionCandidateSource,
    LifecycleDecision,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    ParentAssistantMarker,
)
from autoskillit.execution.process._channel_a_pump import (
    ChannelABatch,
    ChannelACatchUpCommand,
    ChannelAPumpState,
    ChannelARemovalAck,
    ChannelARemoveCommand,
    read_channel_a_batch,
    run_channel_a_pump,
)
from autoskillit.execution.process._lifecycle_actor import (
    CONTROL_CAPACITY,
    REQUEST_CAPACITY,
    WAKE_CAPACITY,
    ActorIngressEndpoint,
    ChannelBProposal,
    LifecycleActorControl,
    LifecycleActorReply,
    LifecycleActorRequest,
    LifecycleReplyDisposition,
    ProcessExitFact,
    ProducerStopFact,
    _evaluate_exit_state,
    _evaluate_state,
    _PermitLease,
    _register_observations,
    _register_parent_markers,
    _stale_suppression_state,
    _SuppressionState,
    make_actor_envelope,
    make_actor_ingress,
    run_lifecycle_actor,
    send_request_cancellation_nowait,
    submit_actor_request_nowait,
    watch_session_log_with_lifecycle,
)
from autoskillit.execution.process._process_monitor import (
    ProcessActivityTracker,
    SessionMonitorResult,
    _ParsedSessionLogRecord,
    _SessionLogScanComplete,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _StubParser:
    def __init__(self, marker: str = "") -> None:
        self.marker = marker
        self.calls: list[str] = []

    def parse_line(self, line: str) -> Any:
        self.calls.append(line)
        return _event(
            BackendEventKind.COMPLETION if self.marker in line else BackendEventKind.IGNORED,
            session_id="sid" if self.marker in line else "",
        )


def _event(kind: BackendEventKind, *, session_id: str = "") -> Any:
    from autoskillit.core import ClaudeEventData, SessionEvent

    return SessionEvent(
        kind=kind,
        is_terminal=False,
        has_marker=kind is BackendEventKind.COMPLETION,
        session_id=session_id,
        exit_code=None,
        backend_data=ClaudeEventData(record_type="", subtype="", session_id=session_id, raw={}),
        observations=(),
    )


def _proposal(
    request_id: str,
    required: int = 0,
    *,
    candidate: str | None = None,
) -> ChannelBProposal:
    sighting = None
    if candidate is not None:
        sighting = CandidateSighting(
            source=CompletionCandidateSource.CHANNEL_B,
            native_uuid=candidate,
            native_message_id=f"message-{candidate}",
            channel_relative_byte_offset=23,
            backend_session_id="candidate-session",
        )
    return ChannelBProposal(
        request_id=request_id,
        status="completion",
        session_id="filename-session",
        byte_offset=23,
        required_byte_offset=required,
        candidate_sighting=sighting,
    )


async def _close_all(ingress: Any) -> None:
    await ingress.channel_a.aclose()
    await ingress.channel_b.aclose()
    await ingress.process_exit.aclose()


async def _run_channel_b_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: tuple[_ParsedSessionLogRecord | _SessionLogScanComplete, ...],
    normalizer: Any,
    prepare_state: Any | None = None,
) -> tuple[list[Any], list[SessionMonitorResult]]:
    import autoskillit.execution.process._lifecycle_actor as actor_module

    session_file = tmp_path / "session-file.jsonl"
    session_file.write_bytes(b"")

    async def discover(*_args: Any, **_kwargs: Any) -> tuple[Path, None]:
        return session_file, None

    async def tail(*_args: Any, **_kwargs: Any) -> Any:
        if prepare_state is not None:
            prepare_state(_args[0])
        for event in events:
            yield event

    monkeypatch.setattr(actor_module, "_discover_session_log_file", discover)
    monkeypatch.setattr(actor_module, "_tail_session_log_events", tail)
    ingress = make_actor_ingress()
    await ingress.channel_a.aclose()
    await ingress.process_exit.aclose()
    semaphore = anyio.Semaphore(REQUEST_CAPACITY)
    producer_stop = anyio.Event()
    channel_b_ready = anyio.Event()
    post_exit_scan = anyio.Event()
    stdout_ready = anyio.Event()
    stdout_ready.set()
    trigger = anyio.Event()
    actor_done = anyio.Event()
    actor_states: list[Any] = []
    results: list[SessionMonitorResult] = []
    command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            run_lifecycle_actor,
            ingress,
            command_send,
            actor_states.append,
            actor_done,
        )
        tg.start_soon(
            lambda: watch_session_log_with_lifecycle(
                session_log_dir=tmp_path,
                completion_marker="MARKER",
                stale_threshold=60,
                spawn_time=0,
                session_record_types=frozenset({"assistant"}),
                pid=999_999,
                activity_tracker=ProcessActivityTracker(),
                completion_drain_timeout=0.1,
                channel_b_ready=channel_b_ready,
                post_exit_scan=post_exit_scan,
                process_exited=anyio.Event(),
                phase1_poll=0,
                phase2_poll=0,
                phase1_timeout=1,
                session_id_timeout=0,
                stdout_session_id_ready=stdout_ready,
                expected_session_id=lambda: None,
                max_suppression_seconds=1,
                marker_dir=None,
                marker_scope_session_id=None,
                stdout_size=lambda: 0,
                endpoint=ingress.channel_b,
                semaphore=semaphore,
                producer_stop=producer_stop,
                parent_candidate_normalizer=normalizer,
                on_result=results.append,
                trigger=trigger,
            )
        )
        await actor_done.wait()

    assert channel_b_ready.is_set()
    assert semaphore.value == REQUEST_CAPACITY
    await command_receive.aclose()
    return actor_states, results


class TestReadChannelABatch:
    def test_empty_file_returns_empty_batch(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_bytes(b"")
        batch = read_channel_a_batch(stdout, parser=_StubParser())
        assert batch.records == ()
        assert batch.byte_offset == 0

    def test_split_utf8_and_exclusive_offset(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_bytes(b'a\n{"text":"\xe2\x98')
        parser = _StubParser(marker="snow")
        first = read_channel_a_batch(stdout, parser=parser)
        assert first.trailing_carry.endswith(b"\xe2\x98")
        with stdout.open("ab") as stream:
            stream.write(b'\x83"}\n')
        second = read_channel_a_batch(
            stdout,
            parser=parser,
            initial_carry=first.trailing_carry,
            initial_byte_offset=first.byte_offset,
        )
        assert second.trailing_carry == b""
        assert second.byte_offset == stdout.stat().st_size
        assert parser.calls == ["a", '{"text":"☃"}']


class TestProcessExitTable:
    def test_quiescent_without_candidate_continues_naturally(self) -> None:
        state = _evaluate_exit_state(make_actor_envelope())
        assert state.decision is LifecycleDecision.CONTINUE

    def test_previously_eligible_candidate_remains_eligible(self) -> None:
        envelope = make_actor_envelope()
        _register_parent_markers(
            envelope,
            (ParentAssistantMarker("eligible-parent", "message", 1),),
        )
        eligible = _evaluate_state(envelope)
        assert eligible.decision is LifecycleDecision.ELIGIBLE
        envelope.last_decision = eligible.decision
        envelope.last_eligible_candidate = eligible.eligible_candidate

        exit_state = _evaluate_exit_state(envelope)

        assert exit_state.decision is LifecycleDecision.ELIGIBLE
        assert exit_state.eligible_candidate is eligible.eligible_candidate

    @pytest.mark.parametrize(
        "blocked_state",
        ["active", "awaiting_delivery", "unresolved_terminal", "blocking_issue"],
    )
    def test_obligation_states_fail_child_work(self, blocked_state: str) -> None:
        envelope = make_actor_envelope()
        launch = ChildLifecycleObservation(task_kind="Agent", tool_use_id="tool", agent_id="agent")
        observations: tuple[ChildLifecycleObservation, ...] = ()
        issues: tuple[LifecycleEvidenceIssue, ...] = ()
        if blocked_state == "active":
            observations = (launch,)
        elif blocked_state == "awaiting_delivery":
            observations = (
                launch,
                ChildLifecycleObservation(
                    task_kind="Agent",
                    tool_use_id="tool",
                    agent_id="agent",
                    attempt_state=ChildAttemptState.COMPLETED,
                ),
            )
        elif blocked_state == "unresolved_terminal":
            observations = (
                ChildLifecycleObservation(
                    task_kind="Agent",
                    tool_use_id="failed-tool",
                    agent_id="failed-agent",
                    attempt_state=ChildAttemptState.FAILED,
                    is_user_result=True,
                ),
            )
        else:
            issues = (
                LifecycleEvidenceIssue(
                    issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                    task_kind="Agent",
                    native_aliases=("tool", "agent"),
                    source_event_uuid="bad-event",
                    canonical_fingerprint="Agent|tool|agent",
                    channel_relative_byte_offset=1,
                    native_alias_kinds=("tool_use_id", "agent_id"),
                ),
            )
        _register_observations(
            envelope,
            ChannelABatch((), observations, (), 1, lifecycle_issues=issues),
        )

        state = _evaluate_exit_state(envelope)

        assert state.decision is LifecycleDecision.CHILD_WORK_FAILED

    @pytest.mark.parametrize("marker_count", [1, 2])
    def test_deferred_or_superseded_candidate_fails_child_work(self, marker_count: int) -> None:
        envelope = make_actor_envelope()
        _register_observations(
            envelope,
            ChannelABatch(
                (),
                (
                    ChildLifecycleObservation(
                        task_kind="Agent", tool_use_id="tool", agent_id="agent"
                    ),
                ),
                (),
                1,
            ),
        )
        _register_parent_markers(
            envelope,
            tuple(
                ParentAssistantMarker(f"parent-{index}", f"message-{index}", index + 2)
                for index in range(marker_count)
            ),
        )
        assert _evaluate_state(envelope).decision is LifecycleDecision.CONTINUE

        state = _evaluate_exit_state(envelope)

        assert state.decision is LifecycleDecision.CHILD_WORK_FAILED
        assert state.snapshot is not None
        assert any(
            candidate_state.name in {"DEFERRED", "SUPERSEDED"}
            for _candidate_id, candidate_state in state.snapshot.candidate_states
        )


class TestActorIngress:
    def test_capacity_parity_is_64_64_64(self) -> None:
        ingress = make_actor_ingress()
        assert REQUEST_CAPACITY == CONTROL_CAPACITY == 64
        assert ingress.capacities == (64, 64, 64)
        assert WAKE_CAPACITY == 1

    @pytest.mark.anyio
    async def test_wake_coalesces_and_drain_is_fair(self) -> None:
        ingress = make_actor_ingress()
        ingress.channel_a.send_ordinary_nowait("ordinary-a")
        ingress.channel_b.send_ordinary_nowait("ordinary-b")
        ingress.channel_a.send_control_nowait("control-a")
        ingress.channel_b.send_control_nowait("control-b")
        ingress.process_exit.send_control_nowait("control-exit")

        await ingress.wait(0.1)
        drained = ingress.drain_nowait()

        assert drained == [
            ("ordinary", "ordinary-a"),
            ("channel_a", "control-a"),
            ("channel_b", "control-b"),
            ("process_exit", "control-exit"),
            ("ordinary", "ordinary-b"),
        ]
        await _close_all(ingress)
        await ingress.wait(0.1)
        ingress.drain_nowait()
        assert ingress.eof
        await ingress.aclose_receivers()

    @pytest.mark.anyio
    async def test_no_creator_sender_original_keeps_eof_open(self) -> None:
        ingress = make_actor_ingress()
        await _close_all(ingress)
        await ingress.wait(0.1)
        assert ingress.drain_nowait() == []
        assert ingress.eof
        await ingress.aclose_receivers()

    @pytest.mark.anyio
    async def test_reserved_control_lane_holds_every_outstanding_request(self) -> None:
        ingress = make_actor_ingress()
        for index in range(REQUEST_CAPACITY):
            ingress.channel_b.send_control_nowait(("terminal", index))
        with pytest.raises(anyio.WouldBlock):
            ingress.channel_b.send_control_nowait(("terminal", REQUEST_CAPACITY))
        await ingress.wait(0.1)
        controls = [item for lane, item in ingress.drain_nowait() if lane == "channel_b"]
        assert controls == [("terminal", index) for index in range(REQUEST_CAPACITY)]
        await _close_all(ingress)
        await ingress.wait(0.1)
        ingress.drain_nowait()
        await ingress.aclose_receivers()

    @pytest.mark.anyio
    async def test_saturated_control_fallback_closes_reply_and_releases_permit(self) -> None:
        ingress = make_actor_ingress()
        for index in range(REQUEST_CAPACITY):
            ingress.channel_a.send_ordinary_nowait(("ordinary", index))
            ingress.channel_b.send_control_nowait(("control", index))
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)

        request = submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("saturated", required=100),
            reply_send,
            anyio.current_time() + 1,
        )

        assert request.lease is not None and request.lease.released
        assert semaphore.value == REQUEST_CAPACITY
        with pytest.raises(anyio.EndOfStream):
            await reply_receive.receive()
        await _close_all(ingress)
        ingress.drain_nowait()
        await ingress.aclose_receivers()


class TestSuppressionParity:
    @pytest.mark.parametrize(
        "active,elapsed,expected",
        [
            (False, 0.0, _SuppressionState.INACTIVE),
            (True, 0.0, _SuppressionState.ACTIVE),
            (True, 2.0, _SuppressionState.EXPIRED),
        ],
    )
    def test_suppression_state_distinguishes_expiry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        active: bool,
        elapsed: float,
        expected: _SuppressionState,
    ) -> None:
        import autoskillit.execution.process._lifecycle_actor as actor_module

        now = 10.0

        class _State:
            suppression_start = now - elapsed if active else None
            last_change = 0.0

        class _Tracker:
            def has_active_children(self, _pid: int) -> bool:
                return False

        monkeypatch.setattr(actor_module, "_has_active_api_connection", lambda _pid: active)
        monkeypatch.setattr(actor_module.time, "monotonic", lambda: now)
        state = _State()

        result = _stale_suppression_state(
            state,
            pid=1,
            activity_tracker=_Tracker(),  # type: ignore[arg-type]
            marker_dir=None,
            marker_scope_session_id=None,
            max_suppression_seconds=1.0,
        )

        assert result is expected
        if expected is _SuppressionState.ACTIVE:
            assert state.last_change == now


class TestPersistentChannelB:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "scan_succeeded,incomplete_carry,expect_failure",
        [(True, False, False), (True, True, True), (False, False, True)],
    )
    async def test_stopped_barrier_clean_vs_incomplete_eof(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        scan_succeeded: bool,
        incomplete_carry: bool,
        expect_failure: bool,
    ) -> None:
        barrier = _SessionLogScanComplete(
            cursor=0,
            observed_size=1 if incomplete_carry else 0,
            session_id="session-file",
            changed=False,
            scan_succeeded=scan_succeeded,
            producer_stopped=True,
            incomplete_carry=incomplete_carry,
        )

        states, _results = await _run_channel_b_events(
            tmp_path, monkeypatch, (barrier,), lambda _record, _offset: None
        )

        if expect_failure:
            assert states[-1].decision is LifecycleDecision.CATCH_UP_FAILED
        else:
            assert states == []

    @pytest.mark.anyio
    async def test_final_scan_normalizes_whole_batch_before_waiting_for_marker_reply(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.execution.process._lifecycle_actor as actor_module

        marker_record = {"type": "assistant", "uuid": "marker"}
        later_record = {"type": "assistant", "uuid": "later-nonmarker"}
        calls: list[tuple[dict[str, Any], int]] = []
        submitted_after_call_count: list[int] = []

        def normalize(record: dict[str, Any], offset: int) -> Any:
            calls.append((record, offset))
            marker = (
                ParentAssistantMarker("marker", "message", offset)
                if record is marker_record
                else None
            )
            return type("Normalized", (), {"marker": marker})()

        original_submit = actor_module.submit_actor_request_nowait

        def capture_submit(*args: Any, **kwargs: Any) -> LifecycleActorRequest:
            submitted_after_call_count.append(len(calls))
            return original_submit(*args, **kwargs)

        monkeypatch.setattr(actor_module, "submit_actor_request_nowait", capture_submit)
        events = (
            _ParsedSessionLogRecord(marker_record, b"marker\n", 7, "session-file"),
            _ParsedSessionLogRecord(later_record, b"later\n", 13, "session-file"),
            _SessionLogScanComplete(
                13,
                13,
                "session-file",
                True,
                producer_stopped=True,
            ),
        )

        await _run_channel_b_events(tmp_path, monkeypatch, events, normalize)

        assert calls == [(marker_record, 7), (later_record, 13)]
        assert submitted_after_call_count == [2]

    @pytest.mark.anyio
    async def test_incomplete_final_scan_dominates_complete_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker_record = {"type": "assistant", "uuid": "complete-marker"}
        calls: list[tuple[dict[str, Any], int]] = []

        def normalize(record: dict[str, Any], offset: int) -> Any:
            calls.append((record, offset))
            return type(
                "Normalized",
                (),
                {"marker": ParentAssistantMarker(record["uuid"], "message-complete", offset)},
            )()

        events = (
            _ParsedSessionLogRecord(marker_record, b"complete\n", 9, "session-file"),
            _SessionLogScanComplete(
                cursor=9,
                observed_size=24,
                session_id="session-file",
                changed=True,
                scan_succeeded=True,
                producer_stopped=True,
                incomplete_carry=True,
            ),
        )

        states, results = await _run_channel_b_events(tmp_path, monkeypatch, events, normalize)

        assert calls == [(marker_record, 9)]
        assert states[-1].decision is LifecycleDecision.CATCH_UP_FAILED
        assert len(results) == 1
        assert results[0].decision is LifecycleDecision.CATCH_UP_FAILED

    @pytest.mark.anyio
    async def test_same_scan_admits_all_proposals_before_exit_and_preserves_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.execution.process._lifecycle_actor as actor_module

        session_file = tmp_path / "session-file.jsonl"
        session_file.write_bytes(b"")
        records = (
            {"type": "assistant", "uuid": "same-scan-first"},
            {"type": "assistant", "uuid": "same-scan-second"},
        )
        events = (
            _ParsedSessionLogRecord(records[0], b"first\n", 6, "session-file"),
            _ParsedSessionLogRecord(records[1], b"second\n", 13, "session-file"),
            _SessionLogScanComplete(
                13,
                13,
                "session-file",
                True,
                producer_stopped=True,
            ),
        )

        async def discover(*_args: Any, **_kwargs: Any) -> tuple[Path, None]:
            return session_file, None

        async def tail(*_args: Any, **_kwargs: Any) -> Any:
            for event in events:
                yield event

        def normalize(record: dict[str, Any], offset: int) -> Any:
            return type(
                "Normalized",
                (),
                {
                    "marker": ParentAssistantMarker(
                        record["uuid"],
                        f"message-{record['uuid']}",
                        offset,
                        backend_session_id=f"candidate-{record['uuid']}",
                    )
                },
            )()

        monkeypatch.setattr(actor_module, "_discover_session_log_file", discover)
        monkeypatch.setattr(actor_module, "_tail_session_log_events", tail)
        original_submit = actor_module.submit_actor_request_nowait
        admissions: list[Any] = []

        def capture_submit(*args: Any, **kwargs: Any) -> LifecycleActorRequest:
            admissions.append(args[2])
            return original_submit(*args, **kwargs)

        monkeypatch.setattr(actor_module, "submit_actor_request_nowait", capture_submit)
        ingress = make_actor_ingress()
        ingress.channel_a.send_ordinary_nowait(
            ChannelABatch(
                (),
                (ChildLifecycleObservation("Agent", "active-tool", "active-agent"),),
                (),
                0,
            )
        )
        await ingress.channel_a.aclose()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        producer_stop = anyio.Event()
        channel_b_ready = anyio.Event()
        post_exit_scan = anyio.Event()
        process_exited = anyio.Event()
        process_exited.set()
        stdout_ready = anyio.Event()
        stdout_ready.set()
        trigger = anyio.Event()
        actor_done = anyio.Event()
        actor_states: list[Any] = []
        results: list[SessionMonitorResult] = []
        exit_replies: list[LifecycleActorReply] = []
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)

        async def submit_exit_after_scan() -> None:
            await post_exit_scan.wait()
            reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
            actor_module.submit_actor_request_nowait(
                ingress.process_exit,
                semaphore,
                ProcessExitFact("same-scan-exit", 0, 0),
                reply_send,
                anyio.current_time() + 1,
            )
            exit_replies.append(await reply_receive.receive())
            reply_receive.close()
            await ingress.process_exit.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                run_lifecycle_actor,
                ingress,
                command_send,
                actor_states.append,
                actor_done,
            )
            tg.start_soon(
                lambda: watch_session_log_with_lifecycle(
                    session_log_dir=tmp_path,
                    completion_marker="MARKER",
                    stale_threshold=60,
                    spawn_time=0,
                    session_record_types=frozenset({"assistant"}),
                    pid=999_999,
                    activity_tracker=ProcessActivityTracker(),
                    completion_drain_timeout=1,
                    channel_b_ready=channel_b_ready,
                    post_exit_scan=post_exit_scan,
                    process_exited=process_exited,
                    phase1_poll=0,
                    phase2_poll=0,
                    phase1_timeout=1,
                    session_id_timeout=0,
                    stdout_session_id_ready=stdout_ready,
                    expected_session_id=lambda: None,
                    max_suppression_seconds=1,
                    marker_dir=None,
                    marker_scope_session_id=None,
                    stdout_size=lambda: 0,
                    endpoint=ingress.channel_b,
                    semaphore=semaphore,
                    producer_stop=producer_stop,
                    parent_candidate_normalizer=normalize,
                    on_result=results.append,
                    trigger=trigger,
                )
            )
            tg.start_soon(submit_exit_after_scan)
            with anyio.fail_after(1):
                await actor_done.wait()

        proposals = [item for item in admissions if isinstance(item, ChannelBProposal)]
        assert [type(item) for item in admissions] == [
            ChannelBProposal,
            ChannelBProposal,
            ProcessExitFact,
        ]
        assert [item.candidate_sighting.native_uuid for item in proposals] == [
            "same-scan-first",
            "same-scan-second",
        ]
        assert len(results) == 2
        assert results[0].snapshot is not None
        assert results[1].snapshot is not None
        assert [item[0] for item in results[0].snapshot.candidate_states] == ["same-scan-first"]
        second_states = dict(results[1].snapshot.candidate_states)
        assert second_states["same-scan-first"].name == "SUPERSEDED"
        assert second_states["same-scan-second"].name == "DEFERRED"
        assert exit_replies[0].decision is LifecycleDecision.CHILD_WORK_FAILED
        assert semaphore.value == REQUEST_CAPACITY
        await command_receive.aclose()

    @pytest.mark.anyio
    async def test_same_offset_after_truncation_gets_distinct_request_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.execution.process._lifecycle_actor as actor_module

        request_ids: list[str] = []
        original_submit = actor_module.submit_actor_request_nowait

        def capture_submit(*args: Any, **kwargs: Any) -> LifecycleActorRequest:
            proposal = args[2]
            request_ids.append(proposal.request_id)
            return original_submit(*args, **kwargs)

        def normalize(record: dict[str, Any], offset: int) -> Any:
            return type(
                "Normalized",
                (),
                {
                    "marker": ParentAssistantMarker(
                        record["uuid"], f"message-{record['uuid']}", offset
                    )
                },
            )()

        monkeypatch.setattr(actor_module, "submit_actor_request_nowait", capture_submit)
        first = {"type": "assistant", "uuid": "first"}
        second = {"type": "assistant", "uuid": "second"}
        events = (
            _ParsedSessionLogRecord(first, b"first\n", 10, "session-file"),
            _SessionLogScanComplete(10, 10, "session-file", True),
            _ParsedSessionLogRecord(second, b"second\n", 10, "session-file"),
            _SessionLogScanComplete(
                10,
                10,
                "session-file",
                True,
                producer_stopped=True,
            ),
        )

        _states, results = await _run_channel_b_events(tmp_path, monkeypatch, events, normalize)

        assert len(results) == 2
        assert len(request_ids) == len(set(request_ids)) == 2

    @pytest.mark.anyio
    @pytest.mark.parametrize("active,expected_orphan", [(False, True), (True, False)])
    async def test_bounded_suppression_expiry_preserves_legacy_orphan_parity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        active: bool,
        expected_orphan: bool,
    ) -> None:
        import autoskillit.execution.process._lifecycle_actor as actor_module

        monkeypatch.setattr(actor_module, "_has_active_api_connection", lambda _pid: active)

        def prepare(state: Any) -> None:
            now = actor_module.time.monotonic()
            state.last_record_type = "user"
            state.last_change = now - 120
            state.suppression_start = now - 2 if active else None

        barrier = _SessionLogScanComplete(
            0,
            0,
            "session-file",
            False,
        )

        _states, results = await _run_channel_b_events(
            tmp_path,
            monkeypatch,
            (barrier,),
            lambda _record, _offset: None,
            prepare,
        )

        assert results == [
            SessionMonitorResult(
                ChannelBStatus.STALE,
                "session-file",
                orphaned_tool_result=expected_orphan,
            )
        ]


class TestRequestAuthority:
    @pytest.mark.anyio
    async def test_full_reply_preserves_exact_objects_before_callback(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        events: list[str] = []
        replies: list[LifecycleActorReply] = []

        class _RecordingSend:
            def send_nowait(self, reply: LifecycleActorReply) -> None:
                events.append("reply")
                replies.append(reply)

            def close(self) -> None:
                events.append("reply-close")

        lease = _PermitLease.acquire_nowait(semaphore)
        assert lease is not None
        request = LifecycleActorRequest(
            _proposal("identity", candidate="same-parent"),
            _RecordingSend(),  # type: ignore[arg-type]
            anyio.current_time() + 1,
            lease,
        )
        ingress.channel_b.send_ordinary_nowait(request)
        lease.transfer_to_actor()

        def on_state(state: Any) -> None:
            events.append("callback")
            reply = replies[0]
            assert reply.snapshot is state.snapshot
            assert reply.eligible_candidate is state.eligible_candidate
            assert reply.sightings is state.sightings

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                run_lifecycle_actor,
                ingress,
                pump_send,
                on_state,
                actor_done,
            )
            await _close_all(ingress)
            await actor_done.wait()

        assert events == ["reply", "reply-close", "callback"]
        reply = replies[0]
        assert dataclasses.is_dataclass(reply)
        assert reply.request_id == "identity"
        assert reply.decision is LifecycleDecision.ELIGIBLE
        assert reply.disposition is LifecycleReplyDisposition.ELIGIBLE
        assert reply.snapshot is not None
        assert reply.issues is reply.snapshot.lifecycle_issues
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_admission_failure_uses_reserved_control_and_replies(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(0)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        actor_done = anyio.Event()
        submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("rejected", required=10),
            reply_send,
            anyio.current_time() + 1,
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.ADMISSION_FAILED
        assert reply.decision is LifecycleDecision.CATCH_UP_FAILED
        await pump_receive.aclose()
        await reply_receive.aclose()

    @pytest.mark.anyio
    async def test_command_saturation_is_correlated_failure(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](1)
        pump_send.send_nowait(ChannelACatchUpCommand("occupied", 999))
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        actor_done = anyio.Event()
        submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("command-full", required=10),
            reply_send,
            anyio.current_time() + 1,
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.COMMAND_FAILED
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_timeout_control_before_request_is_not_dropped(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        lease = _PermitLease.acquire_nowait(semaphore)
        assert lease is not None
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        request = LifecycleActorRequest(
            _proposal("control-first", required=99),
            reply_send,
            anyio.current_time() + 10,
            lease,
        )
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            ingress.channel_b.send_control_nowait(
                LifecycleActorControl(request, LifecycleReplyDisposition.CATCH_UP_FAILED)
            )
            await anyio.sleep(0)
            ingress.channel_b.send_ordinary_nowait(request)
            lease.transfer_to_actor()
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.CATCH_UP_FAILED
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_duplicate_ids_fail_closed_and_release_both_permits(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        replies = [anyio.create_memory_object_stream[LifecycleActorReply](1) for _ in range(2)]
        requests = [
            submit_actor_request_nowait(
                ingress.channel_b,
                semaphore,
                _proposal("duplicate", required=50),
                send,
                anyio.current_time() + 1,
            )
            for send, _receive in replies
        ]
        assert all(request.lease is not None for request in requests)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            duplicate_reply = await replies[1][1].receive()
            await _close_all(ingress)
            first_reply = await replies[0][1].receive()
            await actor_done.wait()
        assert duplicate_reply.disposition is LifecycleReplyDisposition.DUPLICATE_ID
        assert first_reply.disposition is LifecycleReplyDisposition.INCOMPLETE_EOF
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_equal_deadlines_expire_every_request_once(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        deadline = anyio.current_time() + 0.01
        streams = [anyio.create_memory_object_stream[LifecycleActorReply](1) for _ in range(3)]
        for index, (send, _receive) in enumerate(streams):
            submit_actor_request_nowait(
                ingress.channel_b,
                semaphore,
                _proposal(f"deadline-{index}", required=100),
                send,
                deadline,
            )
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            replies = [await receive.receive() for _send, receive in streams]
            await _close_all(ingress)
            await actor_done.wait()
        assert [reply.request_id for reply in replies] == [
            "deadline-0",
            "deadline-1",
            "deadline-2",
        ]
        assert all(
            reply.disposition is LifecycleReplyDisposition.CATCH_UP_FAILED for reply in replies
        )
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_cancellation_then_eof_releases_actor_owned_lease_once(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        request = submit_actor_request_nowait(
            ingress.process_exit,
            semaphore,
            ProcessExitFact("exit", 0, 100),
            reply_send,
            anyio.current_time() + 1,
        )
        assert request.lease is not None and request.lease.owner == "actor"
        assert send_request_cancellation_nowait(ingress.process_exit, request)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.CANCELLED
        assert request.lease.released
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_failed_stop_barrier_is_actor_authored_incomplete_eof(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            ProducerStopFact("failed-barrier", "channel_b", 0),
            reply_send,
            anyio.current_time() + 1,
        )
        states: list[Any] = []
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, states.append, actor_done)
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.INCOMPLETE_EOF
        assert reply.decision is LifecycleDecision.CATCH_UP_FAILED
        assert states[-1].decision is LifecycleDecision.CATCH_UP_FAILED
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_unknown_cancellation_retires_at_original_deadline(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        lease = _PermitLease.acquire_nowait(semaphore)
        assert lease is not None
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        request = LifecycleActorRequest(
            _proposal("control-only", required=100),
            reply_send,
            anyio.current_time() + 0.01,
            lease,
        )
        ingress.channel_b.send_control_nowait(
            LifecycleActorControl(request, LifecycleReplyDisposition.CANCELLED)
        )
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.CANCELLED
        assert lease.released
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_broken_reply_retires_and_releases_once(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        lease = _PermitLease.acquire_nowait(semaphore)
        assert lease is not None

        class _BrokenReplySend:
            def send_nowait(self, _reply: LifecycleActorReply) -> None:
                raise anyio.BrokenResourceError

            def close(self) -> None:
                pass

        request = LifecycleActorRequest(
            _proposal("broken-reply"),
            _BrokenReplySend(),  # type: ignore[arg-type]
            anyio.current_time() + 1,
            lease,
        )
        ingress.channel_b.send_ordinary_nowait(request)
        lease.transfer_to_actor()
        states: list[Any] = []
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, states.append, actor_done)
            await _close_all(ingress)
            await actor_done.wait()
        assert lease.released
        assert semaphore.value == REQUEST_CAPACITY
        assert states[-1].decision is LifecycleDecision.CATCH_UP_FAILED
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_timeout_retains_lease_until_pump_removal_ack(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        remove_send, remove_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        request = submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("retiring", required=100),
            reply_send,
            anyio.current_time() + 0.01,
        )
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                run_lifecycle_actor,
                ingress,
                command_send,
                lambda _state: None,
                actor_done,
                remove_send,
            )
            command = await command_receive.receive()
            removal = await remove_receive.receive()
            assert command.request_id == removal.request_id == request.request_token
            with pytest.raises(anyio.WouldBlock):
                reply_receive.receive_nowait()
            assert semaphore.value == REQUEST_CAPACITY - 1
            ingress.channel_a.send_control_nowait(ChannelARemovalAck(removal.request_id))
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.CATCH_UP_FAILED
        assert semaphore.value == REQUEST_CAPACITY
        await command_receive.aclose()
        await remove_receive.aclose()

    @pytest.mark.anyio
    async def test_satisfied_request_ignores_saturated_remove_lane(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        remove_send, remove_receive = anyio.create_memory_object_stream[Any](1)
        remove_send.send_nowait(ChannelARemoveCommand("occupied"))
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("already-satisfied"),
            reply_send,
            anyio.current_time() + 1,
        )
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                run_lifecycle_actor,
                ingress,
                command_send,
                lambda _state: None,
                actor_done,
                remove_send,
            )
            reply = await reply_receive.receive()
            await _close_all(ingress)
            await actor_done.wait()
        assert reply.disposition is LifecycleReplyDisposition.ACKNOWLEDGED
        assert semaphore.value == REQUEST_CAPACITY
        await command_receive.aclose()
        await remove_receive.aclose()

    @pytest.mark.anyio
    async def test_sustained_timeout_saturation_keeps_maps_and_permits_bounded(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        remove_send, remove_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        streams = [anyio.create_memory_object_stream[LifecycleActorReply](1) for _ in range(64)]
        deadline = anyio.current_time() + 0.02
        for index, (reply_send, _reply_receive) in enumerate(streams):
            submit_actor_request_nowait(
                ingress.channel_b,
                semaphore,
                _proposal(f"saturated-timeout-{index}", required=10_000),
                reply_send,
                deadline,
            )
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                run_lifecycle_actor,
                ingress,
                command_send,
                lambda _state: None,
                actor_done,
                remove_send,
            )
            commands = [await command_receive.receive() for _ in range(64)]
            removals = [await remove_receive.receive() for _ in range(64)]
            assert {item.request_id for item in removals} == {item.request_id for item in commands}
            assert semaphore.value == 0
            for removal in removals:
                ingress.channel_a.send_control_nowait(ChannelARemovalAck(removal.request_id))
            replies = [await receive.receive() for _send, receive in streams]
            await _close_all(ingress)
            await actor_done.wait()
        assert len(replies) == REQUEST_CAPACITY
        assert semaphore.value == REQUEST_CAPACITY
        await command_receive.aclose()
        await remove_receive.aclose()

    @pytest.mark.anyio
    async def test_distinct_duplicate_control_cannot_retire_active_request(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        first_send, first_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        second_send, second_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        first = submit_actor_request_nowait(
            ingress.channel_b,
            semaphore,
            _proposal("shared-id", required=100),
            first_send,
            anyio.current_time() + 1,
        )
        second_lease = _PermitLease.acquire_nowait(semaphore)
        assert second_lease is not None
        second = LifecycleActorRequest(
            _proposal("shared-id", required=100),
            second_send,
            anyio.current_time() + 1,
            second_lease,
        )
        ingress.channel_b.send_control_nowait(
            LifecycleActorControl(second, LifecycleReplyDisposition.CANCELLED)
        )
        ingress.channel_b.send_ordinary_nowait(second)
        second_lease.transfer_to_actor()
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            duplicate = await second_receive.receive()
            assert duplicate.disposition is LifecycleReplyDisposition.DUPLICATE_ID
            assert first.lease is not None and not first.lease.released
            await _close_all(ingress)
            original = await first_receive.receive()
            await actor_done.wait()
        assert original.disposition is LifecycleReplyDisposition.INCOMPLETE_EOF
        assert semaphore.value == REQUEST_CAPACITY
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_actor_exception_retires_already_drained_requests(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        streams = [anyio.create_memory_object_stream[LifecycleActorReply](1) for _ in range(2)]
        for index, (send, _receive) in enumerate(streams):
            submit_actor_request_nowait(
                ingress.channel_b,
                semaphore,
                _proposal(f"callback-{index}"),
                send,
                anyio.current_time() + 1,
            )
        await _close_all(ingress)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()

        def fail_publication(_state: Any) -> None:
            raise RuntimeError("publication-failed")

        with pytest.raises(RuntimeError, match="publication-failed"):
            await run_lifecycle_actor(ingress, pump_send, fail_publication, actor_done)

        replies = [await receive.receive() for _send, receive in streams]
        assert replies[0].disposition is LifecycleReplyDisposition.ACKNOWLEDGED
        assert replies[1].disposition is LifecycleReplyDisposition.INCOMPLETE_EOF
        assert semaphore.value == REQUEST_CAPACITY
        assert actor_done.is_set()
        await pump_receive.aclose()

    @pytest.mark.anyio
    async def test_many_sequential_tokens_do_not_require_retired_id_history(self) -> None:
        ingress = make_actor_ingress()
        semaphore = anyio.Semaphore(REQUEST_CAPACITY)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        actor_done = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_lifecycle_actor, ingress, pump_send, lambda _state: None, actor_done)
            for index in range(REQUEST_CAPACITY * 2):
                reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](
                    1
                )
                submit_actor_request_nowait(
                    ingress.channel_b,
                    semaphore,
                    _proposal(f"sequential-{index}"),
                    reply_send,
                    anyio.current_time() + 1,
                )
                reply = await reply_receive.receive()
                assert reply.request_id == f"sequential-{index}"
                reply_receive.close()
            await _close_all(ingress)
            await actor_done.wait()
        assert semaphore.value == REQUEST_CAPACITY
        assert "retired_request_ids" not in make_actor_envelope().__dataclass_fields__
        await pump_receive.aclose()


class TestChannelAPump:
    @pytest.mark.anyio
    async def test_one_batch_satisfies_many_in_admission_order(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("one\ntwo\n")
        state = ChannelAPumpState(stdout_path=stdout)
        ingress = make_actor_ingress()
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        command_send.send_nowait(ChannelACatchUpCommand("first", 1))
        command_send.send_nowait(ChannelACatchUpCommand("second", 2))
        producer_stop = anyio.Event()
        producer_stop.set()

        await run_channel_a_pump(
            state,
            command_receive,
            producer_stop,
            ingress.channel_a,
            poll_interval=0,
        )
        await ingress.wait(0.1)
        drained = ingress.drain_nowait()
        batches = [item for _lane, item in drained if isinstance(item, ChannelABatch)]
        assert len(batches) == 1
        assert batches[0].byte_offset == stdout.stat().st_size
        await ingress.channel_b.aclose()
        await ingress.process_exit.aclose()
        await ingress.wait(0.1)
        ingress.drain_nowait()
        await ingress.aclose_receivers()
        await command_send.aclose()

    @pytest.mark.anyio
    async def test_removal_prevents_retained_request_and_no_sentinel(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("ready\n")
        state = ChannelAPumpState(stdout_path=stdout)
        ingress = make_actor_ingress()
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        remove_send, remove_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        command_send.send_nowait(ChannelACatchUpCommand("remove-me", 1))
        remove_send.send_nowait(ChannelARemoveCommand("remove-me"))
        producer_stop = anyio.Event()
        producer_stop.set()
        await run_channel_a_pump(
            state,
            command_receive,
            producer_stop,
            ingress.channel_a,
            remove_receive=remove_receive,
            poll_interval=0,
        )
        await ingress.wait(0.1)
        assert ingress.drain_nowait() == [("channel_a", ChannelARemovalAck("remove-me"))]
        await ingress.channel_b.aclose()
        await ingress.process_exit.aclose()
        await ingress.aclose_receivers()
        await command_send.aclose()
        await remove_send.aclose()

    @pytest.mark.anyio
    async def test_repeated_satisfaction_removal_race_retains_no_token_state(
        self, tmp_path: Path
    ) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("ready\n")
        state = ChannelAPumpState(stdout_path=stdout)
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        remove_send, remove_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        request_ids = [f"satisfied-remove-{index}" for index in range(REQUEST_CAPACITY)]
        for request_id in request_ids:
            command_send.send_nowait(ChannelACatchUpCommand(request_id, 1))

        class _RacingEndpoint:
            def __init__(self) -> None:
                self.batches: list[ChannelABatch] = []
                self.controls: list[Any] = []
                self.closed = False

            async def send_ordinary(self, batch: ChannelABatch) -> None:
                self.batches.append(batch)
                for request_id in request_ids:
                    remove_send.send_nowait(ChannelARemoveCommand(request_id))

            async def send_control(self, control: Any) -> None:
                self.controls.append(control)

            async def aclose(self) -> None:
                self.closed = True

        endpoint = _RacingEndpoint()
        producer_stop = anyio.Event()
        producer_stop.set()

        await run_channel_a_pump(
            state,
            command_receive,
            producer_stop,
            endpoint,
            remove_receive=remove_receive,
            poll_interval=0,
        )

        assert len(endpoint.batches) == 1
        assert endpoint.batches[0].byte_offset == stdout.stat().st_size
        assert endpoint.controls == [ChannelARemovalAck(item) for item in request_ids]
        assert command_send.statistics().current_buffer_used == 0
        assert remove_send.statistics().current_buffer_used == 0
        assert endpoint.closed
        assert "removed_before_arrival" not in inspect.getsource(run_channel_a_pump)
        await command_send.aclose()
        await remove_send.aclose()

    @pytest.mark.anyio
    async def test_duplicate_command_control_failure_does_not_escape(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_bytes(b"")
        state = ChannelAPumpState(stdout_path=stdout)
        ingress = make_actor_ingress()
        command_send, command_receive = anyio.create_memory_object_stream[Any](REQUEST_CAPACITY)
        command_send.send_nowait(ChannelACatchUpCommand("duplicate-token", 100))
        command_send.send_nowait(ChannelACatchUpCommand("duplicate-token", 100))
        producer_stop = anyio.Event()
        await ingress.aclose_receivers()

        await run_channel_a_pump(
            state, command_receive, producer_stop, ingress.channel_a, poll_interval=0
        )

        await command_send.aclose()


def test_transport_has_no_mutable_closed_flag_or_sentinels() -> None:
    import autoskillit.execution.process._channel_a_pump as pump

    assert "closed" not in ChannelAPumpState.__dataclass_fields__
    assert not hasattr(pump, "_PUMP_READY")
    assert not hasattr(pump, "_PUMP_CLOSED")
    assert not hasattr(pump, "is_pump_sentinel")


def test_full_request_and_reply_types_are_frozen() -> None:
    assert LifecycleActorRequest.__dataclass_params__.frozen
    assert LifecycleActorReply.__dataclass_params__.frozen
    assert ChannelBProposal.__dataclass_params__.frozen
    assert ProcessExitFact.__dataclass_params__.frozen


def test_endpoint_type_is_private_transport_surface() -> None:
    assert ActorIngressEndpoint.__module__.endswith("._channel_a_pump")
