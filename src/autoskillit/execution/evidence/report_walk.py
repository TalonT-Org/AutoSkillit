"""Incremental source walk for report consumers.

Consumers must commit each record and its successor watermark together, or upsert
the record by ``source_id`` before committing the watermark. Resume only from the
last committed watermark. A checkpoint item commits progress without a record.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from autoskillit._parent_assistant_turns import iter_merged_assistant_turns
from autoskillit.core import ArtifactLease
from autoskillit.execution.backends._codex_parse import _logical_rollout_reader
from autoskillit.execution.child_outcomes import enumerate_claude_subagent_transcripts
from autoskillit.execution.session_log.session_index import (
    iter_tolerant_session_index_lines,
    read_tolerant_session_index_rows,
)
from autoskillit.execution.session_log.session_log import session_index_lock_path

_LEASE_TIMEOUT_SECONDS = 2.0


class SourceGapError(RuntimeError):
    """A committed source boundary is no longer present in retained data."""


@dataclass(frozen=True, slots=True)
class WalkItem:
    kind: str
    source_id: str | None
    session_id: str | None
    record: dict[str, Any] | None
    watermark: dict[str, Any]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(handle: BinaryIO) -> list[int]:
    stat = os.fstat(handle.fileno())
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def _record_id_prefix(line: bytes) -> str | None:
    prefix = b'{"record_id":"'
    if not line.startswith(prefix):
        return None
    end = line.find(b'"', len(prefix), min(len(line), 160))
    if end < 0:
        return None
    try:
        return line[len(prefix) : end].decode("ascii")
    except UnicodeDecodeError:
        return None


def _open_otlp(root: Path, stack: ExitStack) -> list[tuple[str, BinaryIO]]:
    paths = (("archive", root / "otlp.jsonl.1"), ("active", root / "otlp.jsonl"))
    opened: list[tuple[str, BinaryIO]] = []
    lock = root / ".locks" / "otlp-sink.lock"
    with ArtifactLease.acquire_shared(lock, timeout=_LEASE_TIMEOUT_SECONDS):
        for name, path in paths:
            try:
                opened.append((name, stack.enter_context(path.open("rb"))))
            except FileNotFoundError:
                continue
    return opened


def _boundary(handle: BinaryIO, cursor: dict[str, Any]) -> bool:
    start, end = cursor["start"], cursor["end"]
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return False
    if end > _identity(handle)[2]:
        return False
    handle.seek(start)
    line = handle.read(min(end - start, 160))
    record_id = cursor.get("record_id")
    if record_id:
        return _record_id_prefix(line) == record_id
    return _identity(handle) == cursor.get("identity") and _digest(line) == cursor.get(
        "fingerprint"
    )


def _session_id(record: dict[str, Any]) -> str | None:
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
        first_lines = []
        for _, handle in handles:
            handle.seek(0)
            first_lines.append(_record_id_prefix(handle.read(160)))
        if first_lines[0] is not None and first_lines[0] == first_lines[1]:
            handles.pop()
    if not cursor:
        return 0, 0
    for index, (_, handle) in enumerate(handles):
        if _boundary(handle, cursor):
            return index, cursor["end"]
    raise SourceGapError("Committed OTLP record is outside retained generations")


def _walk_otlp(root: Path, state: dict[str, Any]) -> Iterator[WalkItem]:
    with ExitStack() as stack:
        handles = _open_otlp(root, stack)
        start_index, start_offset = _otlp_resume_position(handles, state.get("otlp"))
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
                except (UnicodeDecodeError, json.JSONDecodeError):
                    record = None
                record_id = _record_id_prefix(line)
                identity = _identity(handle)
                state["otlp"] = {
                    "generation": name,
                    "start": start,
                    "end": end,
                    "record_id": record_id,
                    "identity": identity,
                    "fingerprint": _digest(line[:160]),
                }
                if not isinstance(record, dict):
                    continue
                source_id = record_id or f"historical:{identity}:{start}:{_digest(line)}"
                yield WalkItem("otlp", source_id, _session_id(record), record, _copy(state))
            start_offset = 0


def _copy(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state))


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
    unavailable = False
    has_transcript = False
    for field, backend in (("claude_code_log", "claude"), ("codex_log", "codex")):
        raw = row.get(field)
        if raw is None:
            unavailable |= field in row
            continue
        if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
            unavailable = True
            continue
        has_transcript = True
        path = Path(raw)
        paths: tuple[Path, ...] = (path,)
        if backend == "claude":
            paths += enumerate_claude_subagent_transcripts(path)
        for transcript in paths:
            try:
                turns.extend(_transcript_turns(transcript, backend))
            except (OSError, UnicodeError, RuntimeError, ValueError):
                unavailable = True
    unavailable |= not has_transcript
    return {
        "row": row,
        "assistant_turn_count": None if unavailable else len(turns),
        "assistant_turns": turns,
        "transcripts_available": not unavailable,
    }


def _walk_archive(root: Path, state: dict[str, Any]) -> Iterator[WalkItem]:
    path = root / "sessions-archive.jsonl"
    cursor = state.get("archive", {})
    offset = cursor.get("offset", 0)
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        if offset:
            raise SourceGapError("Session archive disappeared") from None
        return
    with handle:
        identity = _identity(handle)[:2]
        if offset:
            if identity != cursor.get("identity") or offset > _identity(handle)[2]:
                raise SourceGapError("Session archive was replaced or truncated")
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                raise SourceGapError("Session archive boundary changed")
            handle.seek(max(0, offset - 160))
            if _digest(handle.read(offset - handle.tell())) != cursor.get("boundary"):
                raise SourceGapError("Session archive boundary changed")
        for end, row in iter_tolerant_session_index_lines(path, offset=offset, complete_only=True):
            handle.seek(max(0, end - 160))
            boundary = _digest(handle.read(end - handle.tell()))
            state["archive"] = {"identity": identity, "offset": end, "boundary": boundary}
            if not row or not isinstance(row.get("dir_name"), str) or not row["dir_name"]:
                continue
            yield WalkItem(
                "session",
                row["dir_name"],
                row.get("session_id"),
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
    previous = {key: value for key, value in state.get("projection", {}).items() if key in names}
    current: dict[str, str] = {}
    for row in rows:
        name = row.get("dir_name")
        if not isinstance(name, str) or not name:
            continue
        fingerprint = _digest(json.dumps(row, sort_keys=True, default=str).encode())
        current[name] = fingerprint
        if previous.get(name) == fingerprint:
            continue
        previous[name] = fingerprint
        state["projection"] = previous
        yield WalkItem("session", name, row.get("session_id"), _session_record(row), _copy(state))
    state["projection"] = current
    state["projection_identity"] = identity
    yield WalkItem("checkpoint", None, None, None, _copy(state))


def iter_report_walk(
    log_root: Path, watermark: dict[str, Any] | None = None
) -> Iterator[WalkItem]:
    """Walk retained sources from a committed, JSON-serializable watermark."""
    root = Path(log_root)
    state = _copy(watermark or {})
    last = _copy(state)
    for walk in (_walk_otlp, _walk_archive, _walk_projection):
        for item in walk(root, state):
            last = item.watermark
            yield item
    if state != last:
        yield WalkItem("checkpoint", None, None, None, _copy(state))
