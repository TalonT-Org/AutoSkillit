"""Invocation-local lifecycle actor and its private bounded transport."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import anyio
import anyio.abc
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from autoskillit.core import (
    CandidateSighting,
    ChannelBStatus,
    ChildLifecycleSnapshot,
    CompletionCandidate,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleDecision,
    LifecycleEvidenceIssue,
    ParentAssistantMarker,
    get_logger,
)
from autoskillit.execution.process._channel_a_pump import (
    CONTROL_CAPACITY as CONTROL_CAPACITY,
)
from autoskillit.execution.process._channel_a_pump import (
    REQUEST_CAPACITY,
    ActorIngressEndpoint,
    ActorIngressTransport,
    ChannelABatch,
    ChannelACatchUpCommand,
    ChannelACommandRejected,
    ChannelARemovalAck,
    ChannelARemoveCommand,
    PermitLease,
    ProducerName,
)
from autoskillit.execution.process._channel_a_pump import (
    WAKE_CAPACITY as WAKE_CAPACITY,
)
from autoskillit.execution.process._channel_a_pump import (
    monitor_result_from_reply as _monitor_result_from_reply,
)
from autoskillit.execution.process._channel_a_pump import (
    receive_reply_or_stop as _receive_reply_or_stop,
)
from autoskillit.execution.process._child_lifecycle import (
    ChildLifecycleCoordinatorHandle,
    make_coordinator_handle,
)
from autoskillit.execution.process._process_monitor import (
    ProcessActivityTracker,
    SessionMonitorResult,
    _discover_session_log_file,
    _has_active_api_connection,
    _has_active_execution_marker,
    _initialize_session_log_tail,
    _ParsedSessionLogRecord,
    _SessionLogScanComplete,
    _tail_session_log_events,
)
from autoskillit.execution.process._process_ownership import OwnedProcessIdentityTracker
from autoskillit.execution.process._process_race import RaceAccumulator, _watch_process

logger = get_logger(__name__)


class LifecycleReplyDisposition(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    DEFERRED = "deferred"
    ELIGIBLE = "eligible"
    CHILD_WORK_FAILED = "child_work_failed"
    CATCH_UP_FAILED = "catch_up_failed"
    ADMISSION_FAILED = "admission_failed"
    COMMAND_FAILED = "command_failed"
    CANCELLED = "cancelled"
    DUPLICATE_ID = "duplicate_id"
    INCOMPLETE_EOF = "incomplete_eof"
    BROKEN_REPLY = "broken_reply"


@dataclass(frozen=True, slots=True)
class ChannelBProposal:
    request_id: str
    status: str
    session_id: str
    byte_offset: int
    required_byte_offset: int
    orphan_diagnostic: bool = False
    candidate_sighting: CandidateSighting | None = None


@dataclass(frozen=True, slots=True)
class ProcessExitFact:
    request_id: str
    returncode: int | None
    required_channel_a_byte_offset: int


@dataclass(frozen=True, slots=True)
class ProducerStopFact:
    request_id: str
    producer: ProducerName
    required_channel_a_byte_offset: int


LifecycleProposal = ChannelBProposal | ProcessExitFact | ProducerStopFact


_PermitLease = PermitLease


@dataclass(frozen=True, slots=True)
class LifecycleActorReply:
    request_id: str
    processed_channel_a_byte_offset: int
    snapshot: ChildLifecycleSnapshot | None
    issues: tuple[LifecycleEvidenceIssue, ...]
    decision: LifecycleDecision
    eligible_candidate: CompletionCandidate | None
    eligible_source: CompletionCandidateSource | None
    sightings: tuple[CandidateSighting, ...]
    disposition: LifecycleReplyDisposition


@dataclass(frozen=True, slots=True)
class LifecycleActorRequest:
    proposal: LifecycleProposal
    reply_send: MemoryObjectSendStream[LifecycleActorReply]
    deadline: float
    lease: PermitLease | None
    request_token: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def request_id(self) -> str:
        return self.proposal.request_id

    @property
    def required_byte_offset(self) -> int:
        if isinstance(self.proposal, ChannelBProposal):
            return self.proposal.required_byte_offset
        return self.proposal.required_channel_a_byte_offset


@dataclass(frozen=True, slots=True)
class LifecycleActorControl:
    request: LifecycleActorRequest
    disposition: LifecycleReplyDisposition


@dataclass(frozen=True, slots=True)
class _LifecycleActorState:
    snapshot: ChildLifecycleSnapshot | None
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible_candidate: CompletionCandidate | None = None
    eligible_source: CompletionCandidateSource | None = None
    sightings: tuple[CandidateSighting, ...] = ()


@dataclass(slots=True)
class _PendingRequest:
    request: LifecycleActorRequest
    command: ChannelACatchUpCommand | None
    deadline: float


@dataclass(slots=True)
class _RetiringRequest:
    pending: _PendingRequest
    state: _LifecycleActorState
    disposition: LifecycleReplyDisposition
    removal_sent: bool = False


@dataclass
class LifecycleActorEnvelope:
    handle: ChildLifecycleCoordinatorHandle
    pending_requests: dict[str, _PendingRequest] = field(default_factory=dict)
    pending_controls: dict[str, LifecycleActorControl] = field(default_factory=dict)
    retiring_requests: dict[str, _RetiringRequest] = field(default_factory=dict)
    last_decision: LifecycleDecision = LifecycleDecision.CONTINUE
    last_snapshot: ChildLifecycleSnapshot | None = None
    last_eligible_candidate: CompletionCandidate | None = None
    last_sightings: tuple[CandidateSighting, ...] = ()
    last_processed_offset: int = 0


class ActorIngress:
    def __init__(self, request_capacity: int = REQUEST_CAPACITY) -> None:
        self._transport = ActorIngressTransport(request_capacity)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)


def make_actor_envelope() -> LifecycleActorEnvelope:
    return LifecycleActorEnvelope(handle=make_coordinator_handle())


def make_actor_ingress(request_capacity: int = REQUEST_CAPACITY) -> ActorIngress:
    return ActorIngress(request_capacity)


def submit_actor_request_nowait(
    endpoint: ActorIngressEndpoint,
    semaphore: anyio.Semaphore,
    proposal: LifecycleProposal,
    reply_send: MemoryObjectSendStream[LifecycleActorReply],
    deadline: float,
) -> LifecycleActorRequest:
    lease = _PermitLease.acquire_nowait(semaphore)
    request = LifecycleActorRequest(proposal, reply_send, deadline, lease)
    if lease is None:
        try:
            endpoint.send_control_nowait(
                LifecycleActorControl(request, LifecycleReplyDisposition.ADMISSION_FAILED)
            )
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            reply_send.close()
        return request
    try:
        endpoint.send_ordinary_nowait(request)
    except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
        try:
            endpoint.send_control_nowait(
                LifecycleActorControl(request, LifecycleReplyDisposition.ADMISSION_FAILED)
            )
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            lease.release_by_producer()
            reply_send.close()
            return request
    lease.transfer_to_actor()
    return request


def send_request_cancellation_nowait(
    endpoint: ActorIngressEndpoint,
    request: LifecycleActorRequest,
) -> bool:
    try:
        endpoint.send_control_nowait(
            LifecycleActorControl(request, LifecycleReplyDisposition.CANCELLED)
        )
    except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
        return False
    return True


class _SuppressionState(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    EXPIRED = "expired"


def _stale_suppression_state(
    state: Any,
    *,
    pid: int,
    activity_tracker: ProcessActivityTracker,
    marker_dir: Path | None,
    marker_scope_session_id: str | None,
    max_suppression_seconds: float,
) -> _SuppressionState:
    active = _has_active_api_connection(pid) or activity_tracker.has_active_children(pid)
    if marker_dir is not None:
        active = active or _has_active_execution_marker(
            marker_dir, session_id=marker_scope_session_id
        )
    if not active:
        return _SuppressionState.INACTIVE
    now = time.monotonic()
    if state.suppression_start is None:
        state.suppression_start = now
    if now - state.suppression_start >= max_suppression_seconds:
        return _SuppressionState.EXPIRED
    state.last_change = now
    return _SuppressionState.ACTIVE


async def watch_process_with_lifecycle(
    proc: anyio.abc.Process,
    acc: RaceAccumulator,
    ownership_tracker: OwnedProcessIdentityTracker,
    endpoint: ActorIngressEndpoint,
    request_semaphore: anyio.Semaphore,
    stdout_path: Path,
    post_exit_scan: anyio.Event,
    channel_b_enabled: bool,
    producer_stop: anyio.Event,
    trigger: anyio.Event,
    completion_drain_timeout: float,
    on_result: Callable[[SessionMonitorResult], None],
) -> None:
    """Submit process exit only after a mandatory post-exit Channel B scan."""
    request: LifecycleActorRequest | None = None
    reply_receive: MemoryObjectReceiveStream[LifecycleActorReply] | None = None
    cancellation_sent = False
    try:
        await _watch_process(proc, acc, anyio.Event(), ownership_tracker)
        try:
            required_offset = stdout_path.stat().st_size
        except OSError:
            required_offset = 0
        post_exit_scan_observed = True
        if channel_b_enabled:
            with anyio.move_on_after(completion_drain_timeout):
                await post_exit_scan.wait()
            post_exit_scan_observed = post_exit_scan.is_set()
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        proposal: LifecycleProposal = (
            ProcessExitFact(
                request_id=f"process-exit-{uuid.uuid4().hex}",
                returncode=proc.returncode,
                required_channel_a_byte_offset=required_offset,
            )
            if post_exit_scan_observed
            else ProducerStopFact(
                request_id=f"process-exit-scan-timeout-{uuid.uuid4().hex}",
                producer="process_exit",
                required_channel_a_byte_offset=required_offset,
            )
        )
        request = submit_actor_request_nowait(
            endpoint,
            request_semaphore,
            proposal,
            reply_send,
            anyio.current_time() + completion_drain_timeout,
        )
        reply = await _receive_reply_or_stop(reply_receive, producer_stop)
        if reply is None:
            send_request_cancellation_nowait(endpoint, request)
            cancellation_sent = True
            return
        on_result(
            _monitor_result_from_reply(
                reply,
                status=acc.channel_b_status,
                session_id=acc.channel_b_session_id,
                orphaned_tool_result=acc.channel_b_orphaned_tool_result,
            )
        )
        trigger.set()
    finally:
        if request is not None and not cancellation_sent and producer_stop.is_set():
            send_request_cancellation_nowait(endpoint, request)
        if reply_receive is not None:
            reply_receive.close()
        await endpoint.aclose()


async def watch_session_log_with_lifecycle(
    *,
    session_log_dir: Path,
    completion_marker: str,
    stale_threshold: float,
    spawn_time: float,
    session_record_types: frozenset[str],
    pid: int,
    activity_tracker: ProcessActivityTracker,
    completion_drain_timeout: float,
    channel_b_ready: anyio.Event,
    post_exit_scan: anyio.Event,
    process_exited: anyio.Event,
    phase1_poll: float,
    phase2_poll: float,
    phase1_timeout: float,
    session_id_timeout: float,
    stdout_session_id_ready: anyio.Event,
    expected_session_id: Callable[[], str | None],
    max_suppression_seconds: float,
    marker_dir: Path | None,
    marker_scope_session_id: str | None,
    stdout_size: Callable[[], int],
    endpoint: ActorIngressEndpoint,
    semaphore: anyio.Semaphore,
    producer_stop: anyio.Event,
    parent_candidate_normalizer: Callable[[dict[str, Any], int], Any],
    on_result: Callable[[SessionMonitorResult], None],
    trigger: anyio.Event,
) -> None:
    """Persistently tail Channel B and submit every valid parent proposal."""
    active_submissions: list[tuple[LifecycleProposal, LifecycleActorRequest, Any]] = []
    cancellation_sent = False
    scan_proposals: list[ChannelBProposal] = []

    def submit_proposal(proposal: LifecycleProposal) -> None:
        reply_send, reply_receive = anyio.create_memory_object_stream[LifecycleActorReply](1)
        request = submit_actor_request_nowait(
            endpoint,
            semaphore,
            proposal,
            reply_send,
            anyio.current_time() + completion_drain_timeout,
        )
        active_submissions.append((proposal, request, reply_receive))

    async def process_submissions() -> bool:
        nonlocal cancellation_sent
        while active_submissions:
            proposal, request, reply_receive = active_submissions[0]
            reply = await _receive_reply_or_stop(reply_receive, producer_stop)
            if reply is None:
                send_request_cancellation_nowait(endpoint, request)
                cancellation_sent = True
                return False
            reply_receive.close()
            active_submissions.pop(0)
            if isinstance(proposal, ChannelBProposal):
                on_result(
                    _monitor_result_from_reply(
                        reply,
                        status=ChannelBStatus.COMPLETION,
                        session_id=proposal.session_id,
                        orphaned_tool_result=proposal.orphan_diagnostic,
                    )
                )
                if reply.decision is not LifecycleDecision.CONTINUE:
                    trigger.set()
        return True

    try:
        with anyio.move_on_after(session_id_timeout):
            await stdout_session_id_ready.wait()
        session_file, discovery_status = await _discover_session_log_file(
            session_log_dir,
            spawn_time,
            expected_session_id=expected_session_id(),
            poll_interval=phase1_poll,
            timeout=phase1_timeout,
        )
        if session_file is None:
            assert discovery_status is not None
            channel_b_ready.set()
            on_result(SessionMonitorResult(discovery_status, ""))
            trigger.set()
            return

        state = _initialize_session_log_tail(session_file)
        async for event in _tail_session_log_events(
            state,
            poll_interval=phase2_poll,
            producer_stop=producer_stop,
        ):
            if isinstance(event, _ParsedSessionLogRecord):
                normalized = parent_candidate_normalizer(event.value, event.exclusive_byte_offset)
                marker = getattr(normalized, "marker", None)
                record_type = event.value.get("type")
                if (
                    not isinstance(marker, ParentAssistantMarker)
                    or record_type not in session_record_types
                ):
                    continue
                sighting = CandidateSighting(
                    source=CompletionCandidateSource.CHANNEL_B,
                    native_uuid=marker.native_uuid,
                    native_message_id=marker.message_id,
                    channel_relative_byte_offset=event.exclusive_byte_offset,
                    backend_session_id=marker.backend_session_id,
                    record_provenance="session_log_parent_assistant_record",
                )
                scan_proposals.append(
                    ChannelBProposal(
                        request_id=f"channel-b-{uuid.uuid4().hex}",
                        status="completion",
                        session_id=event.session_id,
                        byte_offset=event.exclusive_byte_offset,
                        required_byte_offset=stdout_size(),
                        orphan_diagnostic=state.last_record_type == "user",
                        candidate_sighting=sighting,
                    )
                )
                continue

            assert isinstance(event, _SessionLogScanComplete)
            if not channel_b_ready.is_set():
                channel_b_ready.set()
            proposals = tuple(scan_proposals)
            scan_proposals.clear()
            failed_stop = event.producer_stopped and not event.scan_succeeded
            failed_stop |= event.producer_stopped and event.incomplete_carry
            if failed_stop:
                submit_proposal(
                    ProducerStopFact(
                        request_id=f"channel-b-stop-{uuid.uuid4().hex}",
                        producer="channel_b",
                        required_channel_a_byte_offset=stdout_size(),
                    )
                )
            for proposal in proposals:
                submit_proposal(proposal)
            if process_exited.is_set() and not post_exit_scan.is_set():
                post_exit_scan.set()
            if not await process_submissions():
                return
            if event.producer_stopped:
                return
            if event.changed:
                continue
            elapsed = time.monotonic() - state.last_change
            if elapsed < stale_threshold:
                continue
            suppression = _stale_suppression_state(
                state,
                pid=pid,
                activity_tracker=activity_tracker,
                marker_dir=marker_dir,
                marker_scope_session_id=marker_scope_session_id,
                max_suppression_seconds=max_suppression_seconds,
            )
            if suppression is _SuppressionState.ACTIVE:
                continue
            on_result(
                SessionMonitorResult(
                    ChannelBStatus.STALE,
                    state.session_id,
                    orphaned_tool_result=(
                        state.last_record_type == "user"
                        if suppression is _SuppressionState.INACTIVE
                        else False
                    ),
                )
            )
            trigger.set()
            return
    finally:
        if active_submissions and not cancellation_sent:
            send_request_cancellation_nowait(endpoint, active_submissions[0][1])
        for _proposal, _request, reply_receive in active_submissions:
            reply_receive.close()
        await endpoint.aclose()


def _register_observations(envelope: LifecycleActorEnvelope, batch: ChannelABatch) -> None:
    for observation in batch.observations:
        envelope.handle.observe(observation)
    for issue in batch.lifecycle_issues:
        envelope.handle.register_issue(issue)
    envelope.last_processed_offset = max(envelope.last_processed_offset, batch.byte_offset)


def _register_parent_markers(
    envelope: LifecycleActorEnvelope,
    markers: tuple[ParentAssistantMarker, ...],
) -> None:
    for marker in markers:
        envelope.handle.register_parent_marker(marker)


def _all_candidate_sightings(
    envelope: LifecycleActorEnvelope,
    snapshot: ChildLifecycleSnapshot,
) -> tuple[CandidateSighting, ...]:
    candidates = tuple(
        candidate
        for candidate_id, _state in snapshot.candidate_states
        if (candidate := envelope.handle.get_candidate(candidate_id)) is not None
    )
    if len(candidates) == 1:
        return candidates[0].sightings
    return tuple(sighting for candidate in candidates for sighting in candidate.sightings)


def _freeze_state(
    envelope: LifecycleActorEnvelope,
    decision: LifecycleDecision,
    eligible_candidate: CompletionCandidate | None = None,
) -> _LifecycleActorState:
    snapshot = envelope.handle.snapshot()
    if eligible_candidate is not None:
        snapshot = replace(snapshot, eligible_candidate=eligible_candidate)
    sightings = _all_candidate_sightings(envelope, snapshot)
    eligible_source: CompletionCandidateSource | None = None
    if decision is LifecycleDecision.ELIGIBLE and eligible_candidate is not None:
        eligible_source = (
            CompletionCandidateSource.CHANNEL_A
            if any(
                sighting.source is CompletionCandidateSource.CHANNEL_A
                for sighting in eligible_candidate.sightings
            )
            else CompletionCandidateSource.CHANNEL_B
        )
    return _LifecycleActorState(
        snapshot=snapshot,
        decision=decision,
        eligible_candidate=eligible_candidate,
        eligible_source=eligible_source,
        sightings=sightings,
    )


def _evaluate_state(envelope: LifecycleActorEnvelope) -> _LifecycleActorState:
    if envelope.last_decision is not LifecycleDecision.CONTINUE:
        terminal_eligible = envelope.last_eligible_candidate
        if terminal_eligible is not None:
            terminal_eligible = (
                envelope.handle.get_candidate(terminal_eligible.candidate_id) or terminal_eligible
            )
        return _freeze_state(envelope, envelope.last_decision, terminal_eligible)
    snapshot = envelope.handle.snapshot()
    if envelope.handle.has_pending_issues():
        return _freeze_state(envelope, LifecycleDecision.CONTINUE)
    eligible: CompletionCandidate | None = None
    for candidate_id, state in snapshot.candidate_states:
        if state is not CompletionCandidateState.DEFERRED:
            continue
        candidate = envelope.handle.get_candidate(candidate_id)
        if candidate is None:
            continue
        if snapshot.has_unresolved_terminal:
            envelope.handle.note_child_work_failed(candidate_id)
            return _freeze_state(envelope, LifecycleDecision.CHILD_WORK_FAILED)
        promoted = envelope.handle.evaluate_candidate(candidate_id)
        if promoted is not None:
            eligible = promoted
    if eligible is not None:
        return _freeze_state(envelope, LifecycleDecision.ELIGIBLE, eligible)
    return _freeze_state(envelope, LifecycleDecision.CONTINUE)


def _publish_state(
    envelope: LifecycleActorEnvelope,
    on_state: Callable[[_LifecycleActorState], None],
    state: _LifecycleActorState,
) -> None:
    if (
        state.snapshot == envelope.last_snapshot
        and state.decision is envelope.last_decision
        and state.sightings == envelope.last_sightings
    ):
        return
    envelope.last_snapshot = state.snapshot
    envelope.last_decision = state.decision
    envelope.last_eligible_candidate = state.eligible_candidate
    envelope.last_sightings = state.sightings
    on_state(state)


def _publish_actor_state(
    envelope: LifecycleActorEnvelope,
    on_state: Callable[[_LifecycleActorState], None],
    decision: LifecycleDecision,
    eligible_candidate: CompletionCandidate | None = None,
) -> None:
    _publish_state(envelope, on_state, _freeze_state(envelope, decision, eligible_candidate))


def _evaluate_candidates(
    envelope: LifecycleActorEnvelope,
    on_state: Callable[[_LifecycleActorState], None],
) -> None:
    _publish_state(envelope, on_state, _evaluate_state(envelope))


def _disposition_for_state(state: _LifecycleActorState) -> LifecycleReplyDisposition:
    if state.decision is LifecycleDecision.ELIGIBLE:
        return LifecycleReplyDisposition.ELIGIBLE
    if state.decision is LifecycleDecision.CHILD_WORK_FAILED:
        return LifecycleReplyDisposition.CHILD_WORK_FAILED
    if state.snapshot is not None and state.snapshot.candidate_states:
        return LifecycleReplyDisposition.DEFERRED
    return LifecycleReplyDisposition.ACKNOWLEDGED


def _failure_state(envelope: LifecycleActorEnvelope) -> _LifecycleActorState:
    if envelope.last_decision is not LifecycleDecision.CONTINUE:
        return _evaluate_state(envelope)
    return _freeze_state(envelope, LifecycleDecision.CATCH_UP_FAILED)


def _evaluate_exit_state(envelope: LifecycleActorEnvelope) -> _LifecycleActorState:
    if envelope.last_decision is not LifecycleDecision.CONTINUE:
        return _evaluate_state(envelope)
    snapshot = envelope.handle.snapshot()
    blocked_candidate = any(
        state in {CompletionCandidateState.DEFERRED, CompletionCandidateState.SUPERSEDED}
        for _candidate_id, state in snapshot.candidate_states
    )
    if (
        snapshot.has_active_children
        or snapshot.awaiting_delivery
        or snapshot.has_unresolved_terminal
        or envelope.handle.has_pending_issues()
        or blocked_candidate
    ):
        return _freeze_state(envelope, LifecycleDecision.CHILD_WORK_FAILED)
    eligible_id = next(
        (
            candidate_id
            for candidate_id, state in snapshot.candidate_states
            if state is CompletionCandidateState.ELIGIBLE
        ),
        None,
    )
    if eligible_id is not None:
        eligible = envelope.handle.get_candidate(eligible_id)
        if eligible is not None:
            return _freeze_state(envelope, LifecycleDecision.ELIGIBLE, eligible)
    return _freeze_state(envelope, LifecycleDecision.CONTINUE)


async def run_lifecycle_actor(
    ingress: ActorIngress,
    pump_command_send: MemoryObjectSendStream[ChannelACatchUpCommand | ChannelARemoveCommand],
    on_state: Callable[[_LifecycleActorState], None],
    actor_done: anyio.Event,
    pump_remove_send: MemoryObjectSendStream[ChannelARemoveCommand] | None = None,
) -> None:
    """Drain ingress to EOF while serializing all reducer and request mutation."""
    envelope = make_actor_envelope()
    unprocessed_facts: list[object] = []

    def _retire(
        pending: _PendingRequest,
        state: _LifecycleActorState,
        disposition: LifecycleReplyDisposition,
        *,
        publish: bool = True,
    ) -> None:
        request = pending.request
        reply = LifecycleActorReply(
            request_id=request.request_id,
            processed_channel_a_byte_offset=envelope.last_processed_offset,
            snapshot=state.snapshot,
            issues=state.snapshot.lifecycle_issues if state.snapshot is not None else (),
            decision=state.decision,
            eligible_candidate=state.eligible_candidate,
            eligible_source=state.eligible_source,
            sightings=state.sightings,
            disposition=disposition,
        )
        delivered = True
        try:
            request.reply_send.send_nowait(reply)
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            delivered = False
            logger.debug("lifecycle_reply_broken", request_id=request.request_id)
        finally:
            request.reply_send.close()
            if request.lease is not None and not request.lease.released:
                if request.lease.owner == "producer":
                    request.lease.transfer_to_actor()
                request.lease.release_by_actor()
            token = request.request_token
            if envelope.pending_requests.get(token) is pending:
                envelope.pending_requests.pop(token, None)
            envelope.pending_controls.pop(token, None)
            envelope.retiring_requests.pop(token, None)
        if not delivered:
            state = _failure_state(envelope)
            if disposition is LifecycleReplyDisposition.INCOMPLETE_EOF:
                state = _freeze_state(envelope, LifecycleDecision.CATCH_UP_FAILED)
        if publish:
            _publish_state(envelope, on_state, state)

    def _send_removal(retiring: _RetiringRequest) -> None:
        if pump_remove_send is None:
            _retire(retiring.pending, retiring.state, retiring.disposition)
            return
        try:
            pump_remove_send.send_nowait(
                ChannelARemoveCommand(retiring.pending.request.request_token)
            )
        except anyio.WouldBlock:
            return
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            _retire(retiring.pending, retiring.state, retiring.disposition)
            return
        retiring.removal_sent = True

    def _begin_retirement(
        pending: _PendingRequest,
        state: _LifecycleActorState,
        disposition: LifecycleReplyDisposition,
    ) -> None:
        if pending.command is None:
            _retire(pending, state, disposition)
            return
        token = pending.request.request_token
        envelope.pending_requests.pop(token, None)
        retiring = _RetiringRequest(pending, state, disposition)
        envelope.retiring_requests[token] = retiring
        _send_removal(retiring)

    def _fail_request(
        pending: _PendingRequest,
        disposition: LifecycleReplyDisposition,
    ) -> None:
        _begin_retirement(pending, _failure_state(envelope), disposition)

    def _complete_request(pending: _PendingRequest) -> None:
        proposal = pending.request.proposal
        if isinstance(proposal, ChannelBProposal) and proposal.candidate_sighting is not None:
            envelope.handle.register_candidate_sighting(proposal.candidate_sighting)
        state = (
            _evaluate_exit_state(envelope)
            if isinstance(proposal, ProcessExitFact)
            else _evaluate_state(envelope)
        )
        disposition = _disposition_for_state(state)
        _retire(pending, state, disposition)

    def _accept_request(request: LifecycleActorRequest) -> None:
        token = request.request_token
        if token in envelope.pending_requests or token in envelope.retiring_requests:
            return
        if any(
            pending.request.request_id == request.request_id
            for pending in (
                *envelope.pending_requests.values(),
                *(retiring.pending for retiring in envelope.retiring_requests.values()),
            )
        ):
            envelope.pending_controls.pop(token, None)
            duplicate = _PendingRequest(request, None, request.deadline)
            _fail_request(duplicate, LifecycleReplyDisposition.DUPLICATE_ID)
            return
        pending = _PendingRequest(request, None, request.deadline)
        envelope.pending_requests[token] = pending
        control = envelope.pending_controls.pop(token, None)
        if control is not None:
            _fail_request(pending, control.disposition)
            return
        if isinstance(request.proposal, ProducerStopFact):
            state = _freeze_state(envelope, LifecycleDecision.CATCH_UP_FAILED)
            _begin_retirement(pending, state, LifecycleReplyDisposition.INCOMPLETE_EOF)
            return
        if envelope.last_processed_offset >= request.required_byte_offset:
            _complete_request(pending)
            return
        command = ChannelACatchUpCommand(token, request.required_byte_offset)
        try:
            pump_command_send.send_nowait(command)
        except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
            _fail_request(pending, LifecycleReplyDisposition.COMMAND_FAILED)
            return
        pending.command = command

    def _accept_control(control: LifecycleActorControl) -> None:
        token = control.request.request_token
        pending = envelope.pending_requests.get(token)
        if pending is not None:
            _fail_request(pending, control.disposition)
            return
        if token in envelope.retiring_requests or (
            control.request.lease is not None and control.request.lease.released
        ):
            control.request.reply_send.close()
            return
        if control.disposition is LifecycleReplyDisposition.ADMISSION_FAILED:
            pending = _PendingRequest(control.request, None, control.request.deadline)
            envelope.pending_requests[token] = pending
            if control.request.lease is not None and control.request.lease.owner == "producer":
                control.request.lease.transfer_to_actor()
            _fail_request(pending, control.disposition)
            return
        envelope.pending_controls[token] = control

    def _accept_channel_a_control(
        control: ChannelACommandRejected | ChannelARemovalAck,
    ) -> None:
        if isinstance(control, ChannelARemovalAck):
            retiring = envelope.retiring_requests.get(control.request_id)
            if retiring is not None:
                _retire(retiring.pending, retiring.state, retiring.disposition)
            return
        pending = envelope.pending_requests.get(control.request_id)
        if pending is not None:
            _fail_request(pending, LifecycleReplyDisposition.COMMAND_FAILED)

    def _accept_batch(batch: ChannelABatch) -> None:
        _register_observations(envelope, batch)
        _register_parent_markers(envelope, batch.parent_markers)
        completed = [
            pending
            for pending in tuple(envelope.pending_requests.values())
            if pending.command is not None
            and batch.byte_offset >= pending.request.required_byte_offset
        ]
        retired_by_batch = [
            retiring
            for retiring in tuple(envelope.retiring_requests.values())
            if batch.byte_offset >= retiring.pending.request.required_byte_offset
        ]
        if completed:
            for pending in completed:
                _complete_request(pending)
        for retiring in retired_by_batch:
            _retire(retiring.pending, retiring.state, retiring.disposition)
        if not completed and not retired_by_batch:
            _evaluate_candidates(envelope, on_state)

    def _retry_removals() -> None:
        for retiring in tuple(envelope.retiring_requests.values()):
            if not retiring.removal_sent:
                _send_removal(retiring)

    def _expire_prearrival_control(control: LifecycleActorControl) -> None:
        request = control.request
        pending = _PendingRequest(request, None, request.deadline)
        envelope.pending_requests[request.request_token] = pending
        if request.lease is not None and request.lease.owner == "producer":
            request.lease.transfer_to_actor()
        _fail_request(pending, control.disposition)

    try:
        while True:
            next_deadline = min(
                (
                    *(pending.deadline for pending in envelope.pending_requests.values()),
                    *(control.request.deadline for control in envelope.pending_controls.values()),
                ),
                default=None,
            )
            timeout = (
                None if next_deadline is None else max(0.0, next_deadline - anyio.current_time())
            )
            await ingress.wait(timeout)
            unprocessed_facts.extend(fact for _lane, fact in ingress.drain_nowait())
            while unprocessed_facts:
                fact = unprocessed_facts.pop(0)
                if isinstance(fact, ChannelABatch):
                    _accept_batch(fact)
                elif isinstance(fact, LifecycleActorRequest):
                    _accept_request(fact)
                elif isinstance(fact, LifecycleActorControl):
                    _accept_control(fact)
                elif isinstance(fact, (ChannelACommandRejected, ChannelARemovalAck)):
                    _accept_channel_a_control(fact)
            _retry_removals()
            now = anyio.current_time()
            expired = [
                pending
                for pending in tuple(envelope.pending_requests.values())
                if pending.deadline <= now
            ]
            for pending in expired:
                _fail_request(pending, LifecycleReplyDisposition.CATCH_UP_FAILED)
            expired_controls = [
                control
                for control in tuple(envelope.pending_controls.values())
                if control.request.deadline <= now
            ]
            for control in expired_controls:
                _expire_prearrival_control(control)
            if ingress.eof:
                for control in tuple(envelope.pending_controls.values()):
                    _expire_prearrival_control(control)
                for pending in tuple(envelope.pending_requests.values()):
                    _retire(
                        pending,
                        _failure_state(envelope),
                        LifecycleReplyDisposition.INCOMPLETE_EOF,
                    )
                for retiring in tuple(envelope.retiring_requests.values()):
                    _retire(
                        retiring.pending,
                        retiring.state,
                        retiring.disposition,
                    )
                break
    finally:
        with anyio.CancelScope(shield=True):
            pump_command_send.close()
            if pump_remove_send is not None:
                pump_remove_send.close()
            unprocessed_facts.extend(fact for _lane, fact in ingress.drain_nowait())
            for fact in unprocessed_facts:
                if isinstance(fact, LifecycleActorRequest):
                    pending = _PendingRequest(fact, None, fact.deadline)
                    envelope.pending_requests.setdefault(fact.request_token, pending)
                elif isinstance(fact, LifecycleActorControl):
                    envelope.pending_controls.setdefault(fact.request.request_token, fact)
            for control in tuple(envelope.pending_controls.values()):
                request = control.request
                pending = _PendingRequest(request, None, request.deadline)
                envelope.pending_requests[request.request_token] = pending
            cleanup_state = _failure_state(envelope)
            cleanup_pending = tuple(envelope.pending_requests.values()) + tuple(
                retiring.pending for retiring in envelope.retiring_requests.values()
            )
            seen_tokens: set[str] = set()
            for pending in cleanup_pending:
                if pending.request.request_token in seen_tokens:
                    continue
                seen_tokens.add(pending.request.request_token)
                _retire(
                    pending,
                    cleanup_state,
                    LifecycleReplyDisposition.INCOMPLETE_EOF,
                    publish=False,
                )
            await ingress.aclose_receivers()
            actor_done.set()
