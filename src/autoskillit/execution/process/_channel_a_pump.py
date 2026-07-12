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
from typing import TYPE_CHECKING, Any

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


def _decode_lines(payload: bytes) -> list[str]:
    """Decode one binary payload into a list of newline-stripped strings."""
    text = payload.decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if line]


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
    lines = _decode_lines(complete)
    records: list[SessionEvent] = []
    observations: list[ChildLifecycleObservation] = []
    parent_markers: list[ParentAssistantMarker] = []
    lifecycle_issues: list[LifecycleEvidenceIssue] = []
    line_byte_cursor = initial_byte_offset
    for line in lines:
        line_byte_cursor += len(line.encode("utf-8", errors="replace")) + 1
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
    closed: bool = False


def bind_parser(state: ChannelAPumpState, parser: StreamParser) -> None:
    """Bind a fresh parser instance to the pump.

    A lifecycle-aware completion without a parser factory is a programming
    error — the pump cannot synthesize identity. ``bind_parser`` enforces
    that contract by raising if ``state.parser`` is already bound.
    """
    if state.parser is not None:
        raise RuntimeError("channel_a_pump_parser_already_bound")
    if state.closed:
        raise RuntimeError("channel_a_pump_already_closed")
    state.parser = parser


async def run_channel_a_pump(
    state: ChannelAPumpState,
    fact_send: Any,
    command_receive: Any,
    *,
    poll_interval: float = 0.05,
) -> None:
    """Async pump loop — emit batches on ``fact_send`` until closed.

    The loop polls ``state.stdout_path`` every ``poll_interval`` seconds,
    reads the next batch via :func:`read_channel_a_batch`, and pushes each
    batch onto ``fact_send``. When a catch-up command arrives, the pump
    reads ahead until ``required_byte_offset`` is reached, then emits the
    resulting batch and continues normal polling.

    On exit the pump closes ``fact_send`` exactly once.
    """
    if state.stdout_path is None:
        raise RuntimeError("channel_a_pump_stdout_path_unset")
    import anyio

    await fact_send.send(_PUMP_READY)
    pending_request: ChannelACatchUpCommand | None = None
    while not state.closed:
        try:
            cmd = command_receive.receive_nowait()
        except (anyio.WouldBlock, AttributeError):
            cmd = None
        if cmd is not None:
            pending_request = cmd
        batch = read_channel_a_batch(
            state.stdout_path,
            parser=state.parser,
            completion_marker=state.completion_marker,
            initial_carry=state.carry,
            initial_byte_offset=state.byte_offset,
        )
        # ``batch.byte_offset`` is the new exclusive end; update the cursor.
        state.carry = batch.trailing_carry
        state.byte_offset = batch.byte_offset
        if state.on_session_id_resolved is not None:
            for event in batch.records:
                if event.session_id:
                    state.on_session_id_resolved(event.session_id)
        if (
            pending_request is not None
            and batch.byte_offset >= pending_request.required_byte_offset
        ):
            await fact_send.send(batch)
            pending_request = None
            continue
        if batch.records or batch.observations or batch.parent_markers:
            await fact_send.send(batch)
        else:
            await anyio.sleep(poll_interval)
    # Final close — pump emits sentinel to mark end-of-stream.
    await fact_send.send(_PUMP_CLOSED)
    await fact_send.aclose()


@dataclass(frozen=True, slots=True)
class _PumpSentinel:
    """Internal sentinel used to coordinate actor startup/closure."""

    kind: str


_PUMP_READY = _PumpSentinel(kind="ready")
_PUMP_CLOSED = _PumpSentinel(kind="closed")


def is_pump_sentinel(value: Any, kind: str) -> bool:
    """Return True when ``value`` is a pump sentinel of the given kind."""
    return isinstance(value, _PumpSentinel) and value.kind == kind
