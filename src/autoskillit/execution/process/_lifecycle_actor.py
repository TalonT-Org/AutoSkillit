"""Lifecycle actor — sole mutable reducer and completion authority (issue #4233).

The actor is one per ``run_managed_async`` invocation. Three persistent
producers (Channel A pump, Channel B monitor, process-exit watcher) emit
immutable facts onto a single bounded AnyIO stream. The actor consumes
the stream, applies the deterministic reducer, and emits one of:

- ``LifecycleDecision.CONTINUE`` — keep tailing; no candidate eligible
- ``LifecycleDecision.ELIGIBLE`` — a fresh parent candidate has cleared
  every obligation; authorize completion
- ``LifecycleDecision.CHILD_WORK_FAILED`` — a fresh post-quiescence
  candidate arrived while a prior turn's obligations remain unresolved
- ``LifecycleDecision.CATCH_UP_FAILED`` — Channel B or process exit
  requested catch-up but the required Channel A offset was never reached

Watermark catch-up commands travel on a dedicated bounded AnyIO stream
(``ChannelACatchUpCommand``) so the pump can drain ahead without
blocking the main fact reduction. The actor replies on a per-request
one-shot reply stream; producers wait only until the deadline captured
at request time and emit a cancellation fact under a shield on shutdown.

The actor never compares Channel B log offsets with Channel A stdout
offsets; each channel keeps its own offset universe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import anyio
from anyio.streams.memory import MemoryObjectSendStream

from autoskillit.core import (
    CandidateSighting,
    ChildLifecycleSnapshot,
    CompletionCandidate,
    CompletionCandidateSource,
    CompletionCandidateState,
    LifecycleDecision,
    ParentAssistantMarker,
    get_logger,
)
from autoskillit.execution.process._channel_a_pump import (
    ChannelABatch,
    ChannelACatchUpCommand,
    is_pump_sentinel,
)
from autoskillit.execution.process._child_lifecycle import (
    ChildLifecycleCoordinatorHandle,
    make_coordinator_handle,
)

if TYPE_CHECKING:
    import anyio.abc

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelBProposal:
    """Immutable Channel B proposal reduced from the session JSONL log.

    Distinct from a LifecycleDecision: a proposal is a *fact*, never a
    decision. The actor alone decides whether the proposal authorises
    completion. ``byte_offset`` is the Channel B log offset where the
    marker was reduced — never compared with Channel A offsets.
    ``required_byte_offset`` is the Channel A stdout size captured at
    proposal emission time, used for the watermark catch-up command.
    """

    request_id: str
    status: str  # 'completion' | 'stale' | 'dir_missing'
    session_id: str
    byte_offset: int
    required_byte_offset: int
    orphan_diagnostic: bool = False
    candidate_sighting: CandidateSighting | None = None


@dataclass(frozen=True, slots=True)
class ProcessExitFact:
    """Immutable process-exit producer fact.

    Process exit requests catch-up but never synthesizes a candidate.
    The actor captures ``required_channel_a_byte_offset`` at the moment
    of exit and routes the request to the pump.
    """

    request_id: str
    returncode: int | None
    required_channel_a_byte_offset: int


@dataclass(frozen=True, slots=True)
class CatchUpTimeoutFact:
    """Immutable timer-producer fact emitted when a catch-up deadline lapses."""

    request_id: str


@dataclass(frozen=True, slots=True)
class CatchUpCancellationFact:
    """Immutable producer-side cancellation fact emitted on shutdown/shield."""

    request_id: str


@dataclass(frozen=True, slots=True)
class CatchUpAck:
    """Pump-side fact emitted when the required offset has been reduced."""

    request_id: str
    processed_channel_a_byte_offset: int


@dataclass(frozen=True, slots=True)
class _LifecycleActorState:
    """Atomic callback value published by the sole lifecycle authority."""

    snapshot: ChildLifecycleSnapshot | None
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible_candidate: CompletionCandidate | None = None
    eligible_source: CompletionCandidateSource | None = None
    sightings: tuple[CandidateSighting, ...] = ()


@dataclass
class LifecycleActorEnvelope:
    """Wrapper exposing the actor's mutable reducer to its private transport.

    The envelope owns the AnyIO reply endpoints so the actor module can
    import anyio freely while exported core facts stay transport-free.
    Public callers see only the frozen handle. The envelope is mutable
    because the actor is the sole mutable owner of reducer state per
    invocation.
    """

    handle: ChildLifecycleCoordinatorHandle
    pending_requests: dict[str, ChannelACatchUpCommand] = field(default_factory=dict)
    pending_channel_b_sightings: dict[str, CandidateSighting] = field(default_factory=dict)
    pending_deadlines: dict[str, float] = field(default_factory=dict)
    last_decision: LifecycleDecision = LifecycleDecision.CONTINUE
    last_snapshot: ChildLifecycleSnapshot | None = None
    last_eligible_candidate: CompletionCandidate | None = None
    last_sightings: tuple[CandidateSighting, ...] = ()
    last_processed_offset: int = 0


def make_actor_envelope() -> LifecycleActorEnvelope:
    """Build a fresh actor envelope for one invocation."""
    return LifecycleActorEnvelope(handle=make_coordinator_handle())


def _register_observations(
    envelope: LifecycleActorEnvelope,
    batch: ChannelABatch,
) -> None:
    """Apply one Channel A batch's typed observations to the reducer.

    Observations are ordered within a batch and reduced in order so
    source-relative provenance is preserved. ``lifecycle_issues`` are
    registered against the coordinator's pending blocking-evidence store
    so unresolved issues propagate into the snapshot and fail closed.
    """
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


async def _dispatch_catch_up(
    envelope: LifecycleActorEnvelope,
    command: ChannelACatchUpCommand,
    pump_send: MemoryObjectSendStream[ChannelACatchUpCommand],
) -> bool:
    """Best-effort dispatch; never block the main fact reduction.

    The pump command stream has bounded capacity. A saturated stream is a
    typed fail-closed actor decision (``CATCH_UP_FAILED``) rather than a
    blocking producer. Tests assert this in
    ``test_saturated_command_stream_yields_catch_up_failed``.

    Uses ``send_nowait`` so a violated capacity becomes a typed
    fail-closed decision instead of blocking main fact reduction.
    """
    try:
        pump_send.send_nowait(command)
    except (anyio.WouldBlock, anyio.ClosedResourceError):
        logger.warning(
            "catch_up_command_stream_saturated",
            request_id=command.request_id,
        )
        return False
    envelope.pending_requests[command.request_id] = command
    return True


async def run_lifecycle_actor(
    fact_receive: anyio.abc.ObjectReceiveStream[Any],
    pump_command_send: MemoryObjectSendStream[ChannelACatchUpCommand],
    reply_send_by_request: Callable[[str], MemoryObjectSendStream[Any] | None],
    on_state: Callable[[_LifecycleActorState], None],
    *,
    completion_drain_timeout: float = 5.0,
) -> None:
    """Consume the bounded fact stream and drive lifecycle adjudication.

    Each fact reduces through ``envelope.handle``. When a candidate
    ``on_state`` publishes one atomic frozen view of the snapshot, decision,
    eligible candidate/source, and deterministic sightings. It is the single
    race-wakeup vs completion-authority boundary: only the
    actor classifies a wakeup as ``COMPLETED``; race wakes from other
    conditions do not originate eligible state here.
    """
    envelope = make_actor_envelope()
    deadline_to_request: dict[float, str] = {}
    pending_replies: dict[str, anyio.Event] = {}

    async def _timer() -> None:
        while True:
            now = anyio.current_time()
            expired = [(dl, req) for dl, req in deadline_to_request.items() if dl <= now]
            for dl, req in expired:
                await _dispatch_catch_up_timeout(
                    envelope,
                    req,
                    reply_send_by_request,
                    on_state,
                )
                pending_replies.pop(req, None)
                deadline_to_request.pop(dl, None)
            await anyio.sleep(0.01)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_timer)
        async for fact in fact_receive:
            if is_pump_sentinel(fact, "closed"):
                break
            if is_pump_sentinel(fact, "ready"):
                continue
            if isinstance(fact, ChannelABatch):
                _register_observations(envelope, fact)
                _register_parent_markers(envelope, fact.parent_markers)
                # Drain pending catch-up requests satisfied by this batch.
                completed = [
                    req_id
                    for req_id, cmd in envelope.pending_requests.items()
                    if fact.byte_offset >= cmd.required_byte_offset
                ]
                for req_id in completed:
                    envelope.pending_requests.pop(req_id, None)
                    sighting = envelope.pending_channel_b_sightings.pop(req_id, None)
                    if sighting is not None:
                        envelope.handle.register_candidate_sighting(sighting)
                    expired_dl = [dl for dl, rid in deadline_to_request.items() if rid == req_id]
                    for dl in expired_dl:
                        deadline_to_request.pop(dl, None)
                _evaluate_candidates(envelope, on_state)
                for req_id in completed:
                    await _reply(
                        reply_send_by_request,
                        req_id,
                        CatchUpAck(
                            request_id=req_id,
                            processed_channel_a_byte_offset=fact.byte_offset,
                        ),
                    )
                continue
            if isinstance(fact, ChannelBProposal):
                cmd = ChannelACatchUpCommand(
                    request_id=fact.request_id,
                    required_byte_offset=fact.required_byte_offset,
                )
                # Channel B proposals carry their own required offset (captured at send time).
                if await _dispatch_catch_up(envelope, cmd, pump_command_send):
                    if fact.candidate_sighting is not None:
                        envelope.pending_channel_b_sightings[fact.request_id] = (
                            fact.candidate_sighting
                        )
                    deadline = anyio.current_time() + completion_drain_timeout
                    deadline_to_request[deadline] = cmd.request_id
                else:
                    _publish_actor_state(
                        envelope,
                        on_state,
                        LifecycleDecision.CATCH_UP_FAILED,
                    )
                    await _reply(
                        reply_send_by_request,
                        fact.request_id,
                        CatchUpTimeoutFact(request_id=fact.request_id),
                    )
                continue
            if isinstance(fact, ProcessExitFact):
                cmd = ChannelACatchUpCommand(
                    request_id=fact.request_id,
                    required_byte_offset=fact.required_channel_a_byte_offset,
                )
                if await _dispatch_catch_up(envelope, cmd, pump_command_send):
                    deadline = anyio.current_time() + completion_drain_timeout
                    deadline_to_request[deadline] = cmd.request_id
                else:
                    _publish_actor_state(
                        envelope,
                        on_state,
                        LifecycleDecision.CATCH_UP_FAILED,
                    )
                    await _reply(
                        reply_send_by_request,
                        fact.request_id,
                        CatchUpTimeoutFact(request_id=fact.request_id),
                    )
                # Once the exit is consumed, snapshot obligations.
                _evaluate_candidates(envelope, on_state)
                continue
            if isinstance(fact, CatchUpTimeoutFact):
                envelope.pending_requests.pop(fact.request_id, None)
                envelope.pending_channel_b_sightings.pop(fact.request_id, None)
                _publish_actor_state(
                    envelope,
                    on_state,
                    LifecycleDecision.CATCH_UP_FAILED,
                )
                continue
            if isinstance(fact, CatchUpCancellationFact):
                envelope.pending_requests.pop(fact.request_id, None)
                envelope.pending_channel_b_sightings.pop(fact.request_id, None)
                continue
            if isinstance(fact, CatchUpAck):
                envelope.pending_requests.pop(fact.request_id, None)
                envelope.pending_channel_b_sightings.pop(fact.request_id, None)
                continue
        # Drain: evaluate any remaining candidate after end-of-stream.
        _evaluate_candidates(envelope, on_state)
        tg.cancel_scope.cancel()


async def _reply(
    reply_send_by_request: Callable[[str], MemoryObjectSendStream[Any] | None],
    request_id: str,
    payload: Any,
) -> None:
    """Send a typed payload on the per-request reply stream (or drop if closed)."""
    stream = reply_send_by_request(request_id)
    if stream is None:
        return
    try:
        stream.send_nowait(payload)
    except (anyio.WouldBlock, anyio.ClosedResourceError, anyio.BrokenResourceError):
        logger.debug("lifecycle_reply_dropped", request_id=request_id)
        return


async def _dispatch_catch_up_timeout(
    envelope: LifecycleActorEnvelope,
    request_id: str,
    reply_send_by_request: Callable[[str], MemoryObjectSendStream[Any] | None],
    on_state: Callable[[_LifecycleActorState], None],
) -> None:
    """Emit a typed catch-up timeout decision and reply to the producer."""
    _publish_actor_state(envelope, on_state, LifecycleDecision.CATCH_UP_FAILED)
    await _reply(reply_send_by_request, request_id, CatchUpTimeoutFact(request_id=request_id))


def _all_candidate_sightings(
    envelope: LifecycleActorEnvelope,
    snapshot: ChildLifecycleSnapshot,
) -> tuple[CandidateSighting, ...]:
    """Return sightings in deterministic candidate/state order."""
    candidates = tuple(
        candidate
        for candidate_id, _state in snapshot.candidate_states
        if (candidate := envelope.handle.get_candidate(candidate_id)) is not None
    )
    if len(candidates) == 1:
        return candidates[0].sightings
    return tuple(sighting for candidate in candidates for sighting in candidate.sightings)


def _publish_actor_state(
    envelope: LifecycleActorEnvelope,
    on_state: Callable[[_LifecycleActorState], None],
    decision: LifecycleDecision,
    eligible_candidate: CompletionCandidate | None = None,
) -> None:
    """Freeze and publish every lifecycle field as one callback value."""
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
    state = _LifecycleActorState(
        snapshot=snapshot,
        decision=decision,
        eligible_candidate=eligible_candidate,
        eligible_source=eligible_source,
        sightings=sightings,
    )
    envelope.last_snapshot = state.snapshot
    envelope.last_decision = state.decision
    envelope.last_eligible_candidate = state.eligible_candidate
    envelope.last_sightings = state.sightings
    on_state(state)


def _evaluate_candidates(
    envelope: LifecycleActorEnvelope,
    on_state: Callable[[_LifecycleActorState], None],
) -> None:
    """Walk every candidate state and emit a typed decision when warranted.

    A fresh post-quiescence candidate encountering unresolved-terminal
    work emits ``CHILD_WORK_FAILED``. A candidate whose obligations have
    cleared and whose parent-turn generation exceeds the deferred
    generation emits ``ELIGIBLE``. All other states keep ``CONTINUE``.
    """
    if envelope.last_decision is not LifecycleDecision.CONTINUE:
        published_eligible = envelope.last_eligible_candidate
        if published_eligible is not None:
            published_eligible = (
                envelope.handle.get_candidate(published_eligible.candidate_id)
                or published_eligible
            )
        snapshot = envelope.handle.snapshot()
        if published_eligible is not None:
            snapshot = replace(snapshot, eligible_candidate=published_eligible)
        sightings = _all_candidate_sightings(envelope, snapshot)
        if snapshot == envelope.last_snapshot and sightings == envelope.last_sightings:
            return
        _publish_actor_state(
            envelope,
            on_state,
            envelope.last_decision,
            published_eligible,
        )
        return
    snapshot = envelope.handle.snapshot()
    decision: LifecycleDecision = LifecycleDecision.CONTINUE
    eligible: CompletionCandidate | None = None
    if envelope.handle.has_pending_issues():
        _publish_actor_state(envelope, on_state, LifecycleDecision.CONTINUE)
        return
    for candidate_id, state in snapshot.candidate_states:
        if state is not CompletionCandidateState.DEFERRED:
            continue
        candidate = envelope.handle.get_candidate(candidate_id)
        if candidate is None:
            continue
        if snapshot.has_unresolved_terminal:
            envelope.handle.note_child_work_failed(candidate_id)
            decision = LifecycleDecision.CHILD_WORK_FAILED
            _publish_actor_state(envelope, on_state, decision)
            return
        promoted = envelope.handle.evaluate_candidate(candidate_id)
        if promoted is not None:
            eligible = promoted
            decision = LifecycleDecision.ELIGIBLE
    _publish_actor_state(envelope, on_state, decision, eligible)
