"""Persistent binary Channel A pump for the lifecycle actor (issue #4233).

The pump owns the per-invocation ``StreamParser`` instance, the byte cursor,
and any incomplete trailing bytes (including split UTF-8 sequences). One
binary read/drain yields one ordered batch of complete newline-terminated
records; that batch is emitted atomically with its session callbacks and
the processed-channel-a byte offset that advances as the exclusive end of
the last fully reduced line.

The pump must never reparse Claude JSON or synthesize identity from
``has_marker``. The lifecycle reducer is the sole authority on identity;
the pump transports only the immutable observations the parser yields.
Lifecycle-aware completion without a parser factory fails fast: the actor
cannot authorize completion when no parser is bound.

Two coroutines compose the pump:

- :func:`read_channel_a_batch` — synchronously reads the next ordered batch
  from the stdout file and yields its reduced ``SessionEvent``s. Used by
  the legacy heartbeat path and by tests.
- :func:`run_channel_a_pump` — async wrapper around
  :func:`read_channel_a_batch` that emits each batch as one
  ``ChannelABatch`` fact on the actor's bounded AnyIO stream, then waits
  for a requested offset before the next read.

The pump closure path closes both endpoints deterministically. The actor
consumes to end-of-stream; the pump closes its send endpoint only after
the reader has drained.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from autoskillit.core import (
    ChildLifecycleObservation,
    LifecycleEvidenceIssue,
    ParentAssistantMarker,
    SessionEvent,
    get_logger,
)

if TYPE_CHECKING:
    from collections.abc import Callable as Callable  # noqa: F401
    from collections.abc import Iterable as Iterable  # noqa: F401

    from autoskillit.core import StreamParser

logger = get_logger(__name__)

REQUEST_CAPACITY = 64
CONTROL_CAPACITY = 64
WAKE_CAPACITY = 1
ProducerName = Literal["channel_a", "channel_b", "process_exit"]


class PermitLease:
    """Release-once request permit whose ownership transfers on enqueue."""

    __slots__ = ("_owner", "_released", "_semaphore")

    def __init__(self, semaphore: anyio.Semaphore) -> None:
        self._semaphore = semaphore
        self._owner: Literal["producer", "actor"] = "producer"
        self._released = False

    @classmethod
    def acquire_nowait(cls, semaphore: anyio.Semaphore) -> PermitLease | None:
        try:
            semaphore.acquire_nowait()
        except anyio.WouldBlock:
            return None
        return cls(semaphore)

    @property
    def owner(self) -> Literal["producer", "actor"]:
        return self._owner

    @property
    def released(self) -> bool:
        return self._released

    def transfer_to_actor(self) -> None:
        if self._released or self._owner != "producer":
            raise RuntimeError("request_permit_invalid_transfer")
        self._owner = "actor"

    def release_by_producer(self) -> None:
        if self._owner != "producer":
            raise RuntimeError("actor_owned_request_permit")
        self._release_once()

    def release_by_actor(self) -> None:
        if self._owner != "actor":
            raise RuntimeError("producer_owned_request_permit")
        self._release_once()

    def _release_once(self) -> None:
        if self._released:
            raise RuntimeError("request_permit_released_twice")
        self._released = True
        self._semaphore.release()


class ActorIngressEndpoint:
    """Producer-owned clones for ordinary, reserved-control, and wake lanes."""

    __slots__ = ("_control_send", "_ordinary_send", "_wake_send", "producer")

    def __init__(
        self,
        producer: ProducerName,
        ordinary_send: MemoryObjectSendStream[object],
        control_send: MemoryObjectSendStream[object],
        wake_send: MemoryObjectSendStream[None],
    ) -> None:
        self.producer = producer
        self._ordinary_send = ordinary_send
        self._control_send = control_send
        self._wake_send = wake_send

    def _wake_nowait(self) -> None:
        try:
            self._wake_send.send_nowait(None)
        except anyio.WouldBlock:
            pass
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            logger.debug("actor_wake_closed", producer=self.producer)

    def send_ordinary_nowait(self, fact: object) -> None:
        self._ordinary_send.send_nowait(fact)
        self._wake_nowait()

    async def send_ordinary(self, fact: object) -> None:
        await self._ordinary_send.send(fact)
        self._wake_nowait()

    def send_control_nowait(self, fact: object) -> None:
        self._control_send.send_nowait(fact)
        self._wake_nowait()

    async def send_control(self, fact: object) -> None:
        await self._control_send.send(fact)
        self._wake_nowait()

    async def aclose(self) -> None:
        await self._ordinary_send.aclose()
        await self._control_send.aclose()
        self._wake_nowait()
        await self._wake_send.aclose()


class ActorIngressTransport:
    """Actor-owned receivers for one ordinary and three reserved control lanes."""

    def __init__(self, request_capacity: int = REQUEST_CAPACITY) -> None:
        self.request_capacity = request_capacity
        ordinary_send, self._ordinary_receive = anyio.create_memory_object_stream[object](
            request_capacity
        )
        wake_send, self._wake_receive = anyio.create_memory_object_stream[None](WAKE_CAPACITY)
        controls = {
            producer: anyio.create_memory_object_stream[object](CONTROL_CAPACITY)
            for producer in ("channel_a", "channel_b", "process_exit")
        }
        self._control_receives = {name: pair[1] for name, pair in controls.items()}
        self.channel_a = ActorIngressEndpoint(
            "channel_a", ordinary_send.clone(), controls["channel_a"][0], wake_send.clone()
        )
        self.channel_b = ActorIngressEndpoint(
            "channel_b", ordinary_send.clone(), controls["channel_b"][0], wake_send.clone()
        )
        self.process_exit = ActorIngressEndpoint(
            "process_exit",
            ordinary_send.clone(),
            controls["process_exit"][0],
            wake_send.clone(),
        )
        ordinary_send.close()
        wake_send.close()
        self._closed: set[str] = set()

    @property
    def capacities(self) -> tuple[int, int, int]:
        return (self.request_capacity, self.request_capacity, self.request_capacity)

    def _drain_lane(
        self, name: str, receive: MemoryObjectReceiveStream[object]
    ) -> tuple[bool, object | None]:
        if name in self._closed:
            return False, None
        try:
            return True, receive.receive_nowait()
        except anyio.WouldBlock:
            return False, None
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            self._closed.add(name)
            return True, None

    def drain_nowait(self) -> list[tuple[str, object]]:
        lanes = (
            ("ordinary", self._ordinary_receive),
            ("channel_a", self._control_receives["channel_a"]),
            ("channel_b", self._control_receives["channel_b"]),
            ("process_exit", self._control_receives["process_exit"]),
        )
        drained: list[tuple[str, object]] = []
        while True:
            progressed = False
            for name, receive in lanes:
                readable, item = self._drain_lane(name, receive)
                progressed = progressed or readable
                if item is not None:
                    drained.append((name, item))
            if not progressed:
                return drained

    @property
    def eof(self) -> bool:
        return self._closed == {"ordinary", "channel_a", "channel_b", "process_exit"}

    async def wait(self, timeout: float | None) -> None:
        if timeout is None:
            try:
                await self._wake_receive.receive()
            except anyio.EndOfStream:
                pass
            return
        with anyio.move_on_after(max(0.0, timeout)):
            try:
                await self._wake_receive.receive()
            except anyio.EndOfStream:
                pass

    async def aclose_receivers(self) -> None:
        await self._ordinary_receive.aclose()
        for receive in self._control_receives.values():
            await receive.aclose()
        await self._wake_receive.aclose()


async def receive_reply_or_stop(
    reply_receive: MemoryObjectReceiveStream[Any], producer_stop: anyio.Event
) -> Any | None:
    """Race a one-shot actor reply against cooperative producer stop."""
    try:
        return reply_receive.receive_nowait()
    except anyio.WouldBlock:
        pass
    except anyio.EndOfStream:
        return None
    if producer_stop.is_set():
        return None
    replies: list[Any] = []
    done = anyio.Event()

    async def receive_reply() -> None:
        try:
            replies.append(await reply_receive.receive())
        except anyio.EndOfStream:
            pass
        finally:
            done.set()

    async def receive_stop() -> None:
        await producer_stop.wait()
        done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(receive_reply)
        tg.start_soon(receive_stop)
        await done.wait()
        tg.cancel_scope.cancel()
    return replies[0] if replies else None


def monitor_result_from_reply(
    reply: Any,
    *,
    status: Any,
    session_id: str,
    orphaned_tool_result: bool,
) -> Any:
    """Carry an actor reply's exact frozen objects into SessionMonitorResult."""
    from autoskillit.execution.process._process_monitor import SessionMonitorResult

    return SessionMonitorResult(
        status=status,
        session_id=session_id,
        orphaned_tool_result=orphaned_tool_result,
        snapshot=reply.snapshot,
        decision=reply.decision,
        eligible_candidate=reply.eligible_candidate,
        eligible_source=reply.eligible_source,
        sightings=reply.sightings,
    )


@dataclass(frozen=True, slots=True)
class ChannelABatch:
    """One ordered batch of complete newline-terminated Channel A records.

    The pump emits one ``ChannelABatch`` per binary read/drain. ``records``
    is the ordered tuple of reduced events, ``byte_offset`` is the
    exclusive end of the last fully reduced newline-terminated record
    (so the next batch resumes from ``byte_offset``), and ``observations``
    carries every typed child-lifecycle contribution the parser yielded
    on this batch. ``parent_markers`` carries the parent-assistant
    markers that arrived on this batch so the actor can register them
    without re-scanning. ``lifecycle_issues`` carries the typed blocking
    evidence emitted by the normalizer for malformed/alias-conflict
    records; the coordinator replays them into its pending blocking
    store and surfaces them through the snapshot.

    ``processed_channel_a_byte_offset`` is the same value as ``byte_offset``
    so the actor's per-request reply payload can use a single field name.
    """

    records: tuple[SessionEvent, ...]
    observations: tuple[ChildLifecycleObservation, ...]
    parent_markers: tuple[ParentAssistantMarker, ...]
    byte_offset: int
    trailing_carry: bytes = b""
    lifecycle_issues: tuple[LifecycleEvidenceIssue, ...] = ()
    """Typed blocking-evidence issues emitted by the parser/normalizer on this batch.

    Each issue carries the canonical fingerprint required for later resolution.
    The actor relays unresolved issues into the coordinator's pending
    blocking-evidence store; resolved issues (matched against later valid
    evidence) are carried through the snapshot so downstream consumers can
    audit what blocked and what cleared.
    """
    processed_channel_a_byte_offset: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "processed_channel_a_byte_offset",
            self.byte_offset,
        )


@dataclass(frozen=True, slots=True)
class ChannelACatchUpCommand:
    """Actor-to-pump command: drain until ``required_byte_offset`` is reached.

    The actor sends one such command per producer that requests catch-up
    acknowledgement. The pump drains the next ordered batch that meets or
    exceeds the required offset, then emits a single ``ChannelABatch`` on
    the main fact stream. The actor replies to the producer with the
    resulting ``processed_channel_a_byte_offset``.

    A transient carry (incomplete UTF-8 tail) keeps the pump pending until
    a complete newline-terminated record arrives.
    """

    request_id: str
    required_byte_offset: int


@dataclass(frozen=True, slots=True)
class ChannelARemoveCommand:
    """Actor-to-pump retirement for one correlated catch-up request."""

    request_id: str


@dataclass(frozen=True, slots=True)
class ChannelARemovalAck:
    """Pump proof that a request token can no longer populate pump state."""

    request_id: str


@dataclass(frozen=True, slots=True)
class ChannelACommandRejected:
    """Pump rejection of a duplicate request token."""

    request_id: str


def _split_complete_lines(
    carry: bytes,
    raw: bytes,
) -> tuple[bytes, bytes]:
    """Split ``carry + raw`` into (complete_lines, new_carry).

    Lines are newline-terminated UTF-8 records. The trailing bytes after
    the last newline become the new carry; if a split multibyte sequence
    is mid-character at the cut, the decoder will replace silently and
    the carry remains pending until a complete character arrives.
    """
    merged = carry + raw
    if not merged:
        return b"", b""
    last_nl = merged.rfind(b"\n")
    if last_nl == -1:
        # No newline yet — keep everything as carry
        return b"", merged
    complete = merged[: last_nl + 1]
    new_carry = merged[last_nl + 1 :]
    return complete, new_carry


def read_channel_a_batch(
    stdout_path: Path,
    *,
    parser: StreamParser | None = None,
    completion_marker: str = "",
    initial_carry: bytes = b"",
    initial_byte_offset: int = 0,
) -> ChannelABatch:
    """Read one ordered batch of complete newline-terminated records.

    The pump reads every available byte from ``stdout_path``, splits on
    the last newline, reduces each complete line through ``parser``, and
    collects every typed observation and parent marker. The trailing
    carry is returned via the next call's ``initial_carry`` (production
    code passes the carry through ``ChannelAPumpState``).

    Returns an empty batch when no new bytes are available.
    """
    try:
        raw = stdout_path.read_bytes()
    except OSError:
        return ChannelABatch(
            records=(),
            observations=(),
            parent_markers=(),
            byte_offset=initial_byte_offset,
            trailing_carry=initial_carry,
        )
    new_raw = raw[initial_byte_offset + len(initial_carry) :]
    if not new_raw and not initial_carry:
        return ChannelABatch(
            records=(),
            observations=(),
            parent_markers=(),
            byte_offset=initial_byte_offset,
            trailing_carry=initial_carry,
        )
    complete, new_carry = _split_complete_lines(initial_carry, new_raw)
    if not complete:
        return ChannelABatch(
            records=(),
            observations=(),
            parent_markers=(),
            byte_offset=initial_byte_offset,
            trailing_carry=new_carry,
        )
    raw_lines = complete.split(b"\n")
    records: list[SessionEvent] = []
    observations: list[ChildLifecycleObservation] = []
    parent_markers: list[ParentAssistantMarker] = []
    lifecycle_issues: list[LifecycleEvidenceIssue] = []
    line_byte_cursor = initial_byte_offset
    for raw_line in raw_lines:
        if raw_line == b"" and line_byte_cursor > initial_byte_offset:
            continue
        line_byte_cursor += len(raw_line) + 1
        line = raw_line.decode("utf-8", errors="replace")
        if not line:
            continue
        event: SessionEvent | None = None
        if parser is not None:
            try:
                event = parser.parse_line(line)
            except Exception:  # noqa: BLE001
                logger.warning("parser_parse_line_failed", exc_info=True)
                event = None
        if event is not None:
            for obs in event.observations:
                observations.append(replace(obs, byte_offset=line_byte_cursor))
            if event.parent_marker is not None:
                parent_markers.append(replace(event.parent_marker, byte_offset=line_byte_cursor))
            for issue in event.lifecycle_issues:
                lifecycle_issues.append(
                    replace(issue, channel_relative_byte_offset=line_byte_cursor)
                )
            records.append(event)
    return ChannelABatch(
        records=tuple(records),
        observations=tuple(observations),
        parent_markers=tuple(parent_markers),
        byte_offset=line_byte_cursor,
        trailing_carry=new_carry,
        lifecycle_issues=tuple(lifecycle_issues),
    )


@dataclass
class ChannelAPumpState:
    """Mutable per-invocation state shared between pump and actor.

    Holds the carry bytes (split UTF-8 in-flight), the processed offset
    (exclusive end of last fully reduced newline-terminated record), and
    the bound parser instance. The actor never mutates the parser's
    internal state — only the pump owns the parser lifecycle.
    """

    carry: bytes = b""
    byte_offset: int = 0
    parser: StreamParser | None = None
    completion_marker: str = ""
    stdout_path: Path | None = None
    on_session_id_resolved: Callable[[str], None] | None = None


def bind_parser(state: ChannelAPumpState, parser: StreamParser) -> None:
    """Bind a fresh parser instance to the pump.

    A lifecycle-aware completion without a parser factory is a programming
    error — the pump cannot synthesize identity. ``bind_parser`` enforces
    that contract by raising if ``state.parser`` is already bound.
    """
    if state.parser is not None:
        raise RuntimeError("channel_a_pump_parser_already_bound")
    state.parser = parser


async def run_channel_a_pump(
    state: ChannelAPumpState,
    command_receive: Any,
    producer_stop: Any,
    ingress_endpoint: Any,
    *,
    remove_receive: Any | None = None,
    poll_interval: float = 0.05,
) -> None:
    """Drain Channel A for every admitted watermark until cooperative stop.

    Commands are retained by ID in admission order and never overwrite one
    another. One emitted batch can satisfy every retained watermark at or
    below its exclusive offset; explicit removals bound the map when a request
    retires for timeout or cancellation.
    """
    if state.stdout_path is None:
        raise RuntimeError("channel_a_pump_stdout_path_unset")
    pending: dict[str, ChannelACatchUpCommand] = {}

    async def _send_control(control: object) -> bool:
        try:
            await ingress_endpoint.send_control(control)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            return False
        return True

    try:
        while True:
            command_stream_closed = False
            control_closed = False
            while True:
                try:
                    command = command_receive.receive_nowait()
                except anyio.WouldBlock:
                    break
                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    command_stream_closed = True
                    break
                if not isinstance(command, ChannelACatchUpCommand):
                    continue
                if command.request_id in pending:
                    if not await _send_control(ChannelACommandRejected(command.request_id)):
                        control_closed = True
                        break
                    continue
                pending[command.request_id] = command

            if control_closed:
                break
            removal_processed = False
            if remove_receive is not None:
                try:
                    removal = remove_receive.receive_nowait()
                except (
                    anyio.WouldBlock,
                    anyio.EndOfStream,
                    anyio.ClosedResourceError,
                ):
                    removal = None
                if isinstance(removal, ChannelARemoveCommand):
                    pending.pop(removal.request_id, None)
                    if not await _send_control(ChannelARemovalAck(removal.request_id)):
                        control_closed = True
                    removal_processed = True
            if control_closed:
                break
            if removal_processed:
                continue

            batch = read_channel_a_batch(
                state.stdout_path,
                parser=state.parser,
                completion_marker=state.completion_marker,
                initial_carry=state.carry,
                initial_byte_offset=state.byte_offset,
            )
            state.carry = batch.trailing_carry
            state.byte_offset = batch.byte_offset
            if state.on_session_id_resolved is not None:
                for event in batch.records:
                    if event.session_id:
                        state.on_session_id_resolved(event.session_id)

            satisfied = tuple(
                request_id
                for request_id, command in pending.items()
                if state.byte_offset >= command.required_byte_offset
            )
            should_emit = bool(
                batch.records
                or batch.observations
                or batch.parent_markers
                or batch.lifecycle_issues
                or satisfied
            )
            if should_emit:
                await ingress_endpoint.send_ordinary(batch)
                for request_id in satisfied:
                    pending.pop(request_id, None)

            if (producer_stop.is_set() or command_stream_closed) and not should_emit:
                break
            if not should_emit:
                with anyio.move_on_after(poll_interval):
                    await producer_stop.wait()
    finally:
        pending.clear()
        await command_receive.aclose()
        if remove_receive is not None:
            await remove_receive.aclose()
        await ingress_endpoint.aclose()
