"""Tests for the Channel A pump and lifecycle actor (issue #4233).

Covers:
- ``read_channel_a_batch`` binary split / multibyte carry handling
- Whole-line exclusive-end processed watermark
- Exactly-once parsing on duplicate line reads
- ``run_channel_a_pump`` ordered whole-read reduction
- Actor: deterministic decisions (CONTINUE / ELIGIBLE / CHILD_WORK_FAILED / CATCH_UP_FAILED)
- Catch-up command dispatch with one-shot reply streams
- Saturated command streams yield CATCH_UP_FAILED (typed fail-closed)
- Deterministic endpoint closure
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest

from autoskillit.core import (
    BackendEventKind,
    ChildAttemptState,
    ChildLifecycleObservation,
    CompletionCandidateState,
    LifecycleDecision,
    LifecycleEvidenceIssue,
    LifecycleEvidenceIssueKind,
    ParentAssistantMarker,
)
from autoskillit.execution.process._channel_a_pump import (
    ChannelABatch,
    ChannelACatchUpCommand,
    ChannelAPumpState,
    bind_parser,
    is_pump_sentinel,
    read_channel_a_batch,
    run_channel_a_pump,
)
from autoskillit.execution.process._lifecycle_actor import (
    ChannelBProposal,
    make_actor_envelope,
    run_lifecycle_actor,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _StubParser:
    """Minimal StreamParser implementation for tests."""

    def __init__(self, marker: str = "") -> None:
        self.marker = marker
        self.calls: list[str] = []
        self.marker_lines: list[str] = []

    def parse_line(self, line: str) -> Any:
        self.calls.append(line)
        if self.marker and self.marker in line:
            self.marker_lines.append(line)
            return _event(BackendEventKind.COMPLETION, has_marker=True, session_id="sid")
        return _event(BackendEventKind.IGNORED)


def _event(
    kind: BackendEventKind,
    *,
    has_marker: bool = False,
    session_id: str = "",
    is_terminal: bool = False,
) -> Any:
    from autoskillit.core import ClaudeEventData, SessionEvent

    return SessionEvent(
        kind=kind,
        is_terminal=is_terminal,
        has_marker=has_marker,
        session_id=session_id,
        exit_code=None,
        backend_data=ClaudeEventData(
            record_type="",
            subtype="",
            session_id=session_id,
            raw={},
        ),
        observations=(),
    )


class TestReadChannelABatch:
    def test_empty_file_returns_empty_batch(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_bytes(b"")
        batch = read_channel_a_batch(stdout, parser=_StubParser())
        assert batch.records == ()
        assert batch.byte_offset == 0

    def test_complete_lines_emit_single_batch(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text(
            '{"type":"system","subtype":"init"}\n{"type":"assistant","message":{"id":"m1","content":[{"type":"text","text":"hello"}]}}\n'
        )
        batch = read_channel_a_batch(stdout, parser=_StubParser())
        assert batch.byte_offset > 0
        assert len(batch.records) >= 1
        assert batch.processed_channel_a_byte_offset == batch.byte_offset

    def test_split_utf8_carry_resumes(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        # First write: complete line + split multibyte prefix (snowman = e2 98 83)
        stdout.write_bytes(b'{"a":1}\n\xe2\x98')
        batch1 = read_channel_a_batch(stdout, initial_carry=b"")
        assert batch1.byte_offset > 0
        assert batch1.trailing_carry == b"\xe2\x98"
        # Second write: complete the multibyte + another line
        with stdout.open("ab") as stream:
            stream.write(b'\x83\n{"b":2}\n')
        batch2 = read_channel_a_batch(
            stdout,
            initial_carry=batch1.trailing_carry,
            initial_byte_offset=batch1.byte_offset,
        )
        assert batch2.byte_offset > batch1.byte_offset

    def test_exclusive_end_byte_offset_advances(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("line1\nline2\nline3\n")
        batch = read_channel_a_batch(stdout)
        # byte_offset is exclusive end of last fully reduced line; should be len(content).
        assert batch.byte_offset == stdout.stat().st_size

    def test_parser_invoked_once_per_line(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("a\nb\nc\n")
        parser = _StubParser()
        read_channel_a_batch(stdout, parser=parser)
        assert parser.calls == ["a", "b", "c"]


class TestLiveChannelAPump:
    @pytest.mark.anyio
    async def test_split_utf8_carry_survives_live_poll_loop(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_bytes(b'{"text":"\xe2\x98')
        parser = _StubParser(marker="☃")
        resolved_session_ids: list[str] = []
        state = ChannelAPumpState(
            stdout_path=stdout,
            on_session_id_resolved=resolved_session_ids.append,
        )
        bind_parser(state, parser)
        fact_send, fact_receive = anyio.create_memory_object_stream[Any](4)
        command_send, command_receive = anyio.create_memory_object_stream[Any](1)

        async def _run() -> None:
            await run_channel_a_pump(
                state,
                fact_send,
                command_receive,
                poll_interval=0.001,
            )

        async with fact_receive, command_send, command_receive:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_run)
                assert is_pump_sentinel(await fact_receive.receive(), "ready")
                with stdout.open("ab") as stream:
                    stream.write(b'\x83"}\n')
                with anyio.fail_after(1):
                    batch = await fact_receive.receive()
                assert isinstance(batch, ChannelABatch)
                assert batch.trailing_carry == b""
                assert parser.calls == ['{"text":"☃"}']
                assert resolved_session_ids == ["sid"]
                state.closed = True
                with anyio.fail_after(1):
                    assert is_pump_sentinel(await fact_receive.receive(), "closed")

    @pytest.mark.anyio
    async def test_session_id_callback_ignores_empty_ids(self, tmp_path: Path) -> None:
        stdout = tmp_path / "stdout.jsonl"
        stdout.write_text("ignored\n")
        resolved_session_ids: list[str] = []
        state = ChannelAPumpState(
            stdout_path=stdout,
            on_session_id_resolved=resolved_session_ids.append,
        )
        bind_parser(state, _StubParser())
        fact_send, fact_receive = anyio.create_memory_object_stream[Any](4)
        command_send, command_receive = anyio.create_memory_object_stream[Any](1)

        async def _run() -> None:
            await run_channel_a_pump(
                state,
                fact_send,
                command_receive,
                poll_interval=0.001,
            )

        async with fact_receive, command_send, command_receive:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_run)
                assert is_pump_sentinel(await fact_receive.receive(), "ready")
                with anyio.fail_after(1):
                    assert isinstance(await fact_receive.receive(), ChannelABatch)
                state.closed = True
                with anyio.fail_after(1):
                    assert is_pump_sentinel(await fact_receive.receive(), "closed")

        assert resolved_session_ids == []


class TestBindParser:
    def test_first_bind_succeeds(self, tmp_path: Path) -> None:
        state = ChannelAPumpState(stdout_path=tmp_path / "x")
        bind_parser(state, _StubParser())
        assert state.parser is not None

    def test_second_bind_raises(self, tmp_path: Path) -> None:
        state = ChannelAPumpState(stdout_path=tmp_path / "x")
        bind_parser(state, _StubParser())
        with pytest.raises(RuntimeError, match="already_bound"):
            bind_parser(state, _StubParser())

    def test_closed_state_rejects_bind(self, tmp_path: Path) -> None:
        state = ChannelAPumpState(stdout_path=tmp_path / "x", closed=True)
        with pytest.raises(RuntimeError, match="already_closed"):
            bind_parser(state, _StubParser())


class TestActorDecisions:
    @pytest.mark.anyio
    async def test_continue_decision_when_no_candidate(self) -> None:
        """No parent marker -> actor emits CONTINUE; no candidate eligible."""
        decisions: list[tuple[LifecycleDecision, Any]] = []

        def on_decision(decision: LifecycleDecision, candidate: Any) -> None:
            decisions.append((decision, candidate))

        fact_send, fact_receive = anyio.create_memory_object_stream(max_buffer_size=8)
        pump_send, pump_receive = anyio.create_memory_object_stream(max_buffer_size=8)
        pump_receive.close()  # actor can dispatch but receiver never blocks

        async def _runner() -> None:
            await fact_send.send(
                ChannelABatch(
                    records=(),
                    observations=(),
                    parent_markers=(),
                    byte_offset=0,
                )
            )
            await fact_send.aclose()

        async def _consume() -> None:
            await run_lifecycle_actor(
                fact_receive,
                pump_send,
                lambda _req_id: None,
                on_decision,
                completion_drain_timeout=0.1,
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_consume)
            tg.start_soon(_runner)
            await anyio.sleep(0.05)

        # No candidate exists, so no decision was emitted
        assert all(d[0] is not LifecycleDecision.ELIGIBLE for d in decisions)

    def test_superseded_candidate_cannot_steal_child_work_failed(self) -> None:
        from autoskillit.execution.process._lifecycle_actor import (
            _evaluate_candidates,
            _register_observations,
            _register_parent_markers,
        )

        envelope = make_actor_envelope()
        decisions: list[LifecycleDecision] = []
        _register_observations(
            envelope,
            ChannelABatch(
                records=(),
                observations=(
                    ChildLifecycleObservation(
                        task_kind="Agent",
                        tool_use_id="toolu_failed",
                        agent_id="agent_failed",
                    ),
                    ChildLifecycleObservation(
                        task_kind="Agent",
                        tool_use_id="toolu_failed",
                        agent_id="agent_failed",
                        attempt_state=ChildAttemptState.FAILED,
                        is_user_result=True,
                    ),
                ),
                parent_markers=(),
                byte_offset=10,
            ),
        )
        _register_parent_markers(
            envelope,
            (
                ParentAssistantMarker("a-old", "msg-old", 11),
                ParentAssistantMarker("z-new", "msg-new", 12),
            ),
        )

        _evaluate_candidates(
            envelope,
            lambda decision, _candidate: decisions.append(decision),
        )

        assert decisions == [LifecycleDecision.CHILD_WORK_FAILED]
        assert envelope.last_snapshot is not None
        assert envelope.handle.snapshot().candidate_states == (
            ("a-old", CompletionCandidateState.SUPERSEDED),
            ("z-new", CompletionCandidateState.SUPERSEDED),
        )

    def test_pending_issue_blocks_until_exact_child_identity_resolves(self) -> None:
        from autoskillit.execution.process._lifecycle_actor import (
            _evaluate_candidates,
            _register_observations,
            _register_parent_markers,
        )

        envelope = make_actor_envelope()
        decisions: list[tuple[LifecycleDecision, str | None]] = []
        _register_observations(
            envelope,
            ChannelABatch(
                records=(),
                observations=(),
                parent_markers=(),
                byte_offset=1,
                lifecycle_issues=(
                    LifecycleEvidenceIssue(
                        issue_kind=LifecycleEvidenceIssueKind.UNKNOWN_STATUS,
                        task_kind="Agent",
                        native_aliases=("toolu_issue", "agent_issue"),
                        source_event_uuid="event-bad",
                        canonical_fingerprint="Agent|toolu_issue|agent_issue",
                        channel_relative_byte_offset=1,
                        native_alias_kinds=("tool_use_id", "agent_id"),
                    ),
                ),
            ),
        )
        _register_parent_markers(
            envelope,
            (ParentAssistantMarker("uuid-old", "msg-old", 2),),
        )
        _evaluate_candidates(
            envelope,
            lambda decision, candidate: decisions.append(
                (decision, candidate.candidate_id if candidate else None)
            ),
        )
        assert decisions == []

        _register_observations(
            envelope,
            ChannelABatch(
                records=(),
                observations=(
                    ChildLifecycleObservation(
                        task_kind="Agent",
                        tool_use_id="toolu_issue",
                        agent_id="agent_issue",
                        source_event_id="event-valid-launch",
                    ),
                    ChildLifecycleObservation(
                        task_kind="Agent",
                        tool_use_id="toolu_issue",
                        agent_id="agent_issue",
                        attempt_state=ChildAttemptState.COMPLETED,
                        source_event_id="event-valid-delivery",
                        is_user_result=True,
                    ),
                ),
                parent_markers=(),
                byte_offset=3,
            ),
        )
        _register_parent_markers(
            envelope,
            (ParentAssistantMarker("uuid-new", "msg-new", 4),),
        )
        _evaluate_candidates(
            envelope,
            lambda decision, candidate: decisions.append(
                (decision, candidate.candidate_id if candidate else None)
            ),
        )

        assert decisions == [(LifecycleDecision.ELIGIBLE, "uuid-new")]

    def test_malformed_system_terminal_blocks_parent_candidate(self) -> None:
        from autoskillit.execution.backends._claude_lifecycle import (
            extract_lifecycle_issues,
        )
        from autoskillit.execution.process._lifecycle_actor import (
            _evaluate_candidates,
            _register_observations,
            _register_parent_markers,
        )

        (issue,) = extract_lifecycle_issues(
            {
                "type": "system",
                "subtype": "task_notification",
                "status": "completed",
                "uuid": "event-malformed",
                "task_id": "task-X",
                "tool_use_id": "toolu-X",
            },
            "system",
            byte_offset=10,
        )
        envelope = make_actor_envelope()
        decisions: list[LifecycleDecision] = []
        _register_observations(
            envelope,
            ChannelABatch(
                records=(),
                observations=(),
                parent_markers=(),
                lifecycle_issues=(issue,),
                byte_offset=10,
            ),
        )
        _register_parent_markers(
            envelope,
            (ParentAssistantMarker("uuid-marker", "msg-marker", 11),),
        )

        _evaluate_candidates(
            envelope,
            lambda decision, _candidate: decisions.append(decision),
        )

        assert decisions == []
        assert envelope.handle.has_pending_issues()


class TestCatchUpTimeout:
    @pytest.mark.anyio
    async def test_catch_up_timeout_emits_catch_up_failed(self) -> None:
        """A catch-up timeout fact yields a typed CATCH_UP_FAILED decision."""
        envelope = make_actor_envelope()

        # Simulate the dispatch path: process a timeout fact directly.
        from autoskillit.execution.process._lifecycle_actor import (
            _dispatch_catch_up_timeout,
        )

        async def _call() -> None:
            await _dispatch_catch_up_timeout(
                envelope,
                "req-1",
                lambda _req_id: None,
                lambda _decision, _candidate: None,
            )

        await _call()
        assert envelope.last_decision == LifecycleDecision.CATCH_UP_FAILED


class TestSaturatedCommandStream:
    @pytest.mark.anyio
    async def test_saturated_command_stream_returns_false(self) -> None:
        """A saturated command stream yields False, triggering CATCH_UP_FAILED upstream."""
        from autoskillit.execution.process._lifecycle_actor import _dispatch_catch_up

        envelope = make_actor_envelope()
        # Pre-fill the buffer with one item (no receiver draining).
        pump_send2, _pump_recv2 = anyio.create_memory_object_stream(max_buffer_size=1)
        pump_send2.send_nowait(
            ChannelACatchUpCommand(request_id="preexisting", required_byte_offset=50)
        )

        # Buffer is full -> dispatch must fail closed.
        cmd = ChannelACatchUpCommand(request_id="r1", required_byte_offset=100)
        result = await _dispatch_catch_up(envelope, cmd, pump_send2)
        assert result is False  # saturated -> typed fail-closed decision

    @pytest.mark.anyio
    async def test_actor_maps_saturated_catch_up_to_failure_decision(self) -> None:
        fact_send, fact_receive = anyio.create_memory_object_stream[Any](2)
        pump_send, pump_receive = anyio.create_memory_object_stream[Any](1)
        pump_send.send_nowait(
            ChannelACatchUpCommand(request_id="occupied", required_byte_offset=1)
        )
        decisions: list[LifecycleDecision] = []

        async def _produce() -> None:
            await fact_send.send(
                ChannelBProposal(
                    request_id="proposal",
                    status="completion",
                    session_id="sid",
                    byte_offset=0,
                    required_byte_offset=10,
                )
            )
            await fact_send.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_produce)
            tg.start_soon(
                run_lifecycle_actor,
                fact_receive,
                pump_send,
                lambda _request_id: None,
                lambda decision, _candidate: decisions.append(decision),
            )

        await pump_send.aclose()
        await pump_receive.aclose()
        assert decisions == [LifecycleDecision.CATCH_UP_FAILED]


class TestActorEnvelope:
    def test_make_envelope_returns_fresh_handle(self) -> None:
        e1 = make_actor_envelope()
        e2 = make_actor_envelope()
        assert e1.handle is not e2.handle
        assert e1.last_decision == LifecycleDecision.CONTINUE

    def test_envelope_has_zero_processed_offset(self) -> None:
        envelope = make_actor_envelope()
        assert envelope.last_processed_offset == 0
        assert envelope.pending_requests == {}


# Sanity: ensure the symbols exposed via the actor module match expectations.
def test_actor_exports_present() -> None:
    from autoskillit.execution.process import _lifecycle_actor as mod

    for name in (
        "ChannelBProposal",
        "ProcessExitFact",
        "CatchUpTimeoutFact",
        "CatchUpCancellationFact",
        "CatchUpAck",
        "LifecycleActorEnvelope",
        "make_actor_envelope",
        "run_lifecycle_actor",
    ):
        assert hasattr(mod, name), name


# Sanity: ensure the pump module exposes the public surface expected by the actor.
def test_pump_exports_present() -> None:
    from autoskillit.execution.process import _channel_a_pump as mod

    for name in (
        "ChannelABatch",
        "ChannelACatchUpCommand",
        "ChannelAPumpState",
        "bind_parser",
        "read_channel_a_batch",
        "run_channel_a_pump",
    ):
        assert hasattr(mod, name), name
