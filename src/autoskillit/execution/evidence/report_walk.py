"""Incremental source walk for report consumers.

Consumers must commit each record and its successor watermark together, or upsert
the record by ``source_id`` before committing the watermark. Resume only from the
last committed watermark. A checkpoint item commits progress without a record.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Final

from autoskillit.core import ArtifactLease, get_logger, iter_merged_assistant_turns
from autoskillit.execution.backends._codex_parse import _logical_rollout_reader
from autoskillit.execution.child_outcomes import enumerate_claude_subagent_transcripts
from autoskillit.execution.session_log.session_index import (
    iter_tolerant_session_index_lines,
    read_tolerant_session_index_rows,
)
from autoskillit.execution.session_log.session_log import session_index_lock_path

logger = get_logger(__name__)

_LEASE_TIMEOUT_SECONDS = 2.0

# Serialization contract shared with ``_OtlpHandler.do_PUT`` (see
# ``execution/evidence/otlp_sink.py``). The OTLP writer emits one JSON object
# per line with a leading ``record_id`` field; the walker's
# ``_record_id_prefix`` reads the first 160 bytes to recover the key.
_OTLP_RECORD_ID_PREFIX = b'{"record_id":"'
_OTLP_RECORD_ID_SCAN_BYTES = 160

_ARCHIVE_BOUNDARY_CHANGED = "Session archive boundary changed"
_PROJECTION_DELETED_NO_RECORDS = "Session archive disappeared"

# Watermark keys identifying the sources the report walker tracks. Shared with
# ``report_index.py`` so the gap source and the reset table agree on the same
# vocabulary.
SOURCE_OTLP: Final[str] = "otlp"
SOURCE_ARCHIVE: Final[str] = "archive"
VALID_SOURCE_KEYS: Final[frozenset[str]] = frozenset({SOURCE_OTLP, SOURCE_ARCHIVE})

# WalkItem ``kind`` values emitted by the report walker. Shared with consumers
# (e.g. ``_report_index_rows.rows_for_walk_item``) so dispatch is by constant
# instead of repeated string literals.
OTLP_WALK_KIND: Final[str] = "otlp"
SESSION_WALK_KIND: Final[str] = "session"
CHECKPOINT_WALK_KIND: Final[str] = "checkpoint"


class SourceGapError(RuntimeError):
    """A committed source boundary is no longer present in retained data.

    ``source`` is the watermark key whose cursor is stale.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(message)
        if source not in VALID_SOURCE_KEYS:
            raise ValueError(
                f"Unknown SourceGapError source: {source!r}; expected one of "
                f"{sorted(VALID_SOURCE_KEYS)}"
            )
        self.source = source


_VALID_WALK_KINDS = frozenset({OTLP_WALK_KIND, SESSION_WALK_KIND, CHECKPOINT_WALK_KIND})


@dataclass(frozen=True, slots=True)
class WalkItem:
    kind: str
    source_id: str | None
    session_id: str | None
    record: dict[str, Any] | None
    watermark: dict[str, Any]

    def __post_init__(self) -> None:
        if self.kind not in _VALID_WALK_KINDS:
            raise ValueError(f"Unknown WalkItem kind: {self.kind!r}")
        if self.source_id is not None and not isinstance(self.source_id, str):
            raise TypeError(
                f"WalkItem.source_id must be str or None, got {type(self.source_id).__name__}"
            )
        if self.session_id is not None and not isinstance(self.session_id, str):
            raise TypeError(
                f"WalkItem.session_id must be str or None, got {type(self.session_id).__name__}"
            )
        if self.record is not None and not isinstance(self.record, dict):
            raise TypeError(
                f"WalkItem.record must be dict or None, got {type(self.record).__name__}"
            )
        if not isinstance(self.watermark, dict):
            raise TypeError(
                f"WalkItem.watermark must be dict, got {type(self.watermark).__name__}"
            )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(handle: BinaryIO) -> list[int]:
    stat = os.fstat(handle.fileno())
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def _record_id_prefix(line: bytes) -> str | None:
    if not line.startswith(_OTLP_RECORD_ID_PREFIX):
        return None
    end = line.find(b'"', len(_OTLP_RECORD_ID_PREFIX), min(len(line), _OTLP_RECORD_ID_SCAN_BYTES))
    if end < 0:
        return None
    try:
        return line[len(_OTLP_RECORD_ID_PREFIX) : end].decode("ascii")
    except UnicodeDecodeError:
        return None


def _open_otlp_handles(root: Path, stack: ExitStack) -> list[tuple[str, BinaryIO]]:
    """Open OTLP source handles under the sink lease.

    The lease is held for the lifetime of the returned handles via the
    caller's ``ExitStack``; releasing it here would let writers append during
    iteration and produce partial reads.
    """
    paths = (("archive", root / "otlp.jsonl.1"), ("active", root / "otlp.jsonl"))
    lock = root / ".locks" / "otlp-sink.lock"
    stack.enter_context(ArtifactLease.acquire_shared(lock, timeout=_LEASE_TIMEOUT_SECONDS))
    opened: list[tuple[str, BinaryIO]] = []
    for name, path in paths:
        try:
            opened.append((name, stack.enter_context(path.open("rb"))))
        except FileNotFoundError:
            continue
    return opened


def _validate_otlp_boundary(handle: BinaryIO, cursor: dict[str, Any]) -> bool:
    start, end = cursor["start"], cursor["end"]
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return False
    if end > _identity(handle)[2]:
        return False
    handle.seek(start)
    line = handle.read(min(end - start, _OTLP_RECORD_ID_SCAN_BYTES))
    record_id = cursor.get("record_id")
    if record_id:
        return _record_id_prefix(line) == record_id
    # Without a record_id we rely on the line content fingerprint; concurrent
    # appends change st_size/st_mtime_ns but preserve the line at ``start``.
    return _digest(line) == cursor.get("fingerprint")


def _extract_otlp_session_id(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    for key in ("session_id", "sessionId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _otlp_resume_position(
    handles: list[tuple[str, BinaryIO]], cursor: dict[str, Any] | None
) -> tuple[int, int]:
    if len(handles) == 2:
        first_lines: list[str | None] = []
        for _, handle in handles:
            handle.seek(0)
            first_lines.append(_record_id_prefix(handle.read(_OTLP_RECORD_ID_SCAN_BYTES)))
        if first_lines[0] is not None and first_lines[0] == first_lines[1]:
            # Both files share their first record (rotation overlap). Drop the
            # file that does not hold the committed cursor so we never raise
            # SourceGapError for a record that is still on disk.
            archive, active = handles[0][1], handles[1][1]
            if cursor is None:
                handles.pop(0)  # no cursor → keep the newer active file
            elif _validate_otlp_boundary(active, cursor) and not _validate_otlp_boundary(
                archive, cursor
            ):
                handles.pop(0)  # cursor is in active → drop archive
            elif _validate_otlp_boundary(archive, cursor) and not _validate_otlp_boundary(
                active, cursor
            ):
                handles.pop(1)  # cursor is in archive → drop active
            # If the cursor matches both or neither, leave both files in
            # place; the boundary search below will resolve the location or
            # raise SourceGapError when the record is genuinely gone.
    if not cursor:
        return 0, 0
    for index, (_, handle) in enumerate(handles):
        if _validate_otlp_boundary(handle, cursor):
            return index, cursor["end"]
    raise SourceGapError(SOURCE_OTLP, "Committed OTLP record is outside retained generations")


def _walk_otlp(root: Path, state: dict[str, Any]) -> Iterator[WalkItem]:
    with ExitStack() as stack:
        handles = _open_otlp_handles(root, stack)
        start_index, start_offset = _otlp_resume_position(handles, state.get(SOURCE_OTLP))
        for index in range(start_index, len(handles)):
            name, handle = handles[index]
            handle.seek(start_offset if index == start_index else 0)
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b"\n"):
                    break
                end = handle.tell()
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    logger.debug(
                        "report_walk_otlp_payload_decode_failed",
                        extra={"generation": name, "offset": start, "error": str(exc)},
                    )
                    record = None
                record_id = _record_id_prefix(line)
                state[SOURCE_OTLP] = {
                    "generation": name,
                    "start": start,
                    "end": end,
                    "record_id": record_id,
                    "fingerprint": _digest(line[:_OTLP_RECORD_ID_SCAN_BYTES]),
                }
                if not isinstance(record, dict):
                    continue
                # Resume identity is content-based: the record_id prefix when
                # available, otherwise the line digest. Using identity (st_size
                # / st_mtime_ns) here would break resume when the file is
                # appended to between walks.
                source_id = record_id or f"historical:{_digest(line)}"
                yield WalkItem(
                    OTLP_WALK_KIND,
                    source_id,
                    _extract_otlp_session_id(record),
                    record,
                    _copy(state),
                )


def _copy(state: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(state)


def _transcript_turns(path: Path, backend: str) -> list[dict[str, Any]]:
    if backend == "codex":
        with _logical_rollout_reader(path) as handle:
            text = handle.read().decode("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return [
        {
            "turn_id": f"{path}:{turn.request_id}",
            "timestamp": turn.timestamp,
            "tool_names": list(turn.tool_names),
        }
        for turn in iter_merged_assistant_turns(text, backend=backend)
    ]


def _session_record(row: dict[str, Any]) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    unavailable_reasons: list[str] = []
    has_transcript = False
    for field, backend in (("claude_code_log", "claude"), ("codex_log", "codex")):
        raw = row.get(field)
        if raw is None:
            if field in row:
                unavailable_reasons.append(f"{field}:null")
            continue
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            unavailable_reasons.append(f"{field}:invalid-path")
            continue
        has_transcript = True
        path = Path(raw)
        paths: tuple[Path, ...]
        try:
            paths = (path,) + (
                enumerate_claude_subagent_transcripts(path) if backend == "claude" else ()
            )
        except OSError as exc:
            unavailable_reasons.append(f"{field}:enumerate:{exc}")
            continue
        for transcript in paths:
            try:
                turns.extend(_transcript_turns(transcript, backend))
            except (OSError, UnicodeError, ValueError) as exc:
                unavailable_reasons.append(f"{transcript}:{exc}")
            except RuntimeError as exc:
                # _logical_rollout_reader raises RuntimeError for non-regular
                # rollout files (symlinks, devices). Treat as unavailable
                # rather than masking the caller's intent.
                unavailable_reasons.append(f"{transcript}:runtime:{exc}")
    unavailable = not has_transcript or bool(unavailable_reasons)
    return {
        "row": row,
        "assistant_turn_count": None if unavailable else len(turns),
        "assistant_turns": turns,
        "transcripts_available": not unavailable,
        "transcript_unavailable_reasons": unavailable_reasons,
    }


def _walk_archive(root: Path, state: dict[str, Any]) -> Iterator[WalkItem]:
    # The session archive is an append-only retention file with no concurrent
    # writers in the live session workflow, so an ArtifactLease is not
    # required; the file handle is closed promptly and identity is captured
    # once for the lifetime of the walk.
    path = root / "sessions-archive.jsonl"
    cursor = state.get(SOURCE_ARCHIVE, {})
    offset = cursor.get("offset", 0)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        if offset:
            raise SourceGapError(SOURCE_ARCHIVE, _PROJECTION_DELETED_NO_RECORDS) from None
        return
    with handle:
        identity = _identity(handle)[:2]
        if offset:
            if identity != cursor.get("identity") or offset > _identity(handle)[2]:
                raise SourceGapError(SOURCE_ARCHIVE, "Session archive was replaced or truncated")
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                raise SourceGapError(SOURCE_ARCHIVE, _ARCHIVE_BOUNDARY_CHANGED)
            handle.seek(max(0, offset - 160))
            if _digest(handle.read(offset - handle.tell())) != cursor.get("boundary"):
                raise SourceGapError(SOURCE_ARCHIVE, _ARCHIVE_BOUNDARY_CHANGED)
        for end, row in iter_tolerant_session_index_lines(path, offset=offset, complete_only=True):
            handle.seek(max(0, end - 160))
            boundary = _digest(handle.read(end - handle.tell()))
            state[SOURCE_ARCHIVE] = {"identity": identity, "offset": end, "boundary": boundary}
            if not row or not isinstance(row.get("dir_name"), str) or not row["dir_name"]:
                continue
            session_id = row.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                session_id = None
            yield WalkItem(
                SESSION_WALK_KIND,
                row["dir_name"],
                session_id,
                _session_record(row),
                _copy(state),
            )


def _walk_projection(root: Path, state: dict[str, Any]) -> Iterator[WalkItem]:
    path = root / "sessions.jsonl"
    with ArtifactLease.acquire_shared(
        session_index_lock_path(root), timeout=_LEASE_TIMEOUT_SECONDS
    ):
        try:
            stat = path.stat()
            identity = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
            if identity == state.get("projection_identity"):
                return
            rows = read_tolerant_session_index_rows(path)
        except FileNotFoundError:
            identity = None
            rows = []
    names = {row.get("dir_name") for row in rows if isinstance(row.get("dir_name"), str)}
    previous: dict[str, str] = {}
    current: dict[str, str] = {}
    for key, value in state.get("projection", {}).items():
        if key in names:
            previous[key] = value
    for row in rows:
        name = row.get("dir_name")
        if not isinstance(name, str) or not name:
            continue
        fingerprint = _digest(json.dumps(row, sort_keys=True, default=str).encode())
        current[name] = fingerprint
        if previous.get(name) == fingerprint:
            continue
        previous = dict(previous)
        previous[name] = fingerprint
        state["projection"] = previous
        session_id = row.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            session_id = None
        yield WalkItem(SESSION_WALK_KIND, name, session_id, _session_record(row), _copy(state))
    state["projection"] = current
    state["projection_identity"] = identity
    yield WalkItem(CHECKPOINT_WALK_KIND, None, None, None, _copy(state))


def iter_report_walk(
    log_root: Path, watermark: dict[str, Any] | None = None
) -> Iterator[WalkItem]:
    """Walk retained sources from a committed, JSON-serializable watermark.

    The walk chains three sub-walkers (``_walk_otlp``, ``_walk_archive``,
    ``_walk_projection``); only ``_walk_projection`` emits a final
    ``kind="checkpoint"`` WalkItem to record that the live ``sessions.jsonl``
    snapshot was fully observed. Consumers relying on a per-source checkpoint
    should not assume one from ``_walk_otlp`` or ``_walk_archive``.
    """
    state = _copy(watermark or {})
    for walk in (_walk_otlp, _walk_archive, _walk_projection):
        yield from walk(log_root, state)
