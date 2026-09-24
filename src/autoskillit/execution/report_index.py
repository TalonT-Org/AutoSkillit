"""Durable, append-only storage and tolerant reads for the derived report index.

The index lives in ``report-index/`` under a log root. Fact rows are fsynced to
``rows.jsonl`` before ``state.json`` atomically records the committed walk watermark
and byte boundary. The reader keeps the last row for each kind/key, joins event rows
to session attempts by session id and event time, and resolves token measures using
the attributed harness/provider pair.

For sources changed only in ways tracked by ``iter_report_walk``, an incremental
build and a rebuild have the same rows. This assumes transcripts are not modified
after their session row is walked; ``rebuild_report_index`` refreshes transcript
counts when that assumption no longer holds.
"""

from __future__ import annotations

import json
import os
from bisect import bisect_right
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, TypeGuard

from autoskillit.core import (
    CANONICAL_ACCOUNTING_FIELDS,
    ArtifactLease,
    get_logger,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.execution._report_index_rows import (
    REPORT_INDEX_SCHEMA_VERSION as REPORT_INDEX_SCHEMA_VERSION,
)
from autoskillit.execution._report_index_rows import (
    REQUEST_KIND,
    SESSION_KIND,
    SUBAGENT_KIND,
    TOOL_KIND,
    UNKNOWN_SOURCE,
    normalize_report_row,
    resolve_token_measure,
    rows_for_walk_item,
)
from autoskillit.execution.evidence.report_walk import (
    SourceGapError,
    WalkItem,
    iter_report_walk,
)
from autoskillit.execution.session_log.session_index import (
    iter_tolerant_session_index_lines,
)

_ROWS_FILE = "rows.jsonl"
_STATE_FILE = "state.json"
_LOCK_FILE = "index.lock"
_STATE_SCHEMA_VERSION = 1
_LEASE_TIMEOUT_SECONDS = 2.0
_COMMIT_BYTES = 1 << 20
_RESET_KEYS = {
    "otlp": ("otlp",),
    "archive": ("archive", "projection", "projection_identity"),
}

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReportIndexUpdate:
    """Counts and source gaps from one index update."""

    items_walked: int
    rows_written: int
    source_gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportIndex:
    """Latest report facts, grouped by their persisted row kind."""

    sessions: dict[str, dict[str, Any]]
    requests: dict[str, dict[str, Any]]
    tools: dict[str, dict[str, Any]]
    subagents: dict[str, dict[str, Any]]


def report_index_dir(log_root: Path) -> Path:
    """Return the report-index directory under a session log root."""
    return log_root / "report-index"


def update_report_index(log_root: Path, index_dir: Path) -> ReportIndexUpdate:
    """Append new facts derived from retained report-walk sources."""
    return _update(log_root, index_dir, rebuild=False)


def rebuild_report_index(log_root: Path, index_dir: Path) -> ReportIndexUpdate:
    """Delete and recreate the index from the currently retained sources."""
    return _update(log_root, index_dir, rebuild=True)


def _update(log_root: Path, index_dir: Path, *, rebuild: bool) -> ReportIndexUpdate:
    index_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ArtifactLease.acquire_exclusive(index_dir / _LOCK_FILE, timeout=_LEASE_TIMEOUT_SECONDS):
        if rebuild:
            (index_dir / _ROWS_FILE).unlink(missing_ok=True)
            (index_dir / _STATE_FILE).unlink(missing_ok=True)
        with _RowAppender.open(index_dir) as appender:
            return _walk_into(log_root, appender)


def _committed_state(index_dir: Path, rows_path: Path) -> tuple[dict[str, Any] | None, int]:
    try:
        size = rows_path.stat().st_size
    except FileNotFoundError:
        size = 0
    state = read_versioned_json(index_dir / _STATE_FILE, _STATE_SCHEMA_VERSION)
    if _state_is_valid(state, rows_path, size):
        assert state is not None
        walk = state["walk"]
        return walk, state["rows_bytes"]
    return None, _complete_line_prefix(rows_path, size)


def _state_is_valid(state: dict[str, Any] | None, rows_path: Path, size: int) -> bool:
    if state is None:
        return False
    rows_bytes = state.get("rows_bytes")
    walk = state.get("walk")
    if (
        isinstance(rows_bytes, bool)
        or not isinstance(rows_bytes, int)
        or rows_bytes < 0
        or rows_bytes > size
        or (walk is not None and not _valid_watermark(walk))
    ):
        return False
    return _is_line_boundary(rows_path, rows_bytes)


def _is_line_boundary(rows_path: Path, rows_bytes: int) -> bool:
    if rows_bytes == 0:
        return True
    try:
        with rows_path.open("rb") as handle:
            handle.seek(rows_bytes - 1)
            return handle.read(1) == b"\n"
    except FileNotFoundError:
        return False


def _valid_watermark(walk: object) -> bool:
    if not isinstance(walk, dict):
        return False
    return (
        _valid_otlp_cursor(walk.get("otlp"))
        and _valid_archive_cursor(walk.get("archive", {}))
        and _valid_projection(walk.get("projection", {}))
        and _valid_projection_identity(walk.get("projection_identity"))
    )


def _valid_otlp_cursor(cursor: object) -> bool:
    if cursor is None:
        return True
    if not isinstance(cursor, dict):
        return False
    return _valid_otlp_offsets(cursor) and _valid_otlp_fingerprint(cursor)


def _valid_otlp_offsets(cursor: dict[str, Any]) -> bool:
    start, end = cursor.get("start"), cursor.get("end")
    return _nonnegative_int(start) and _nonnegative_int(end) and end > start


def _valid_otlp_fingerprint(cursor: dict[str, Any]) -> bool:
    record_id, fingerprint = cursor.get("record_id"), cursor.get("fingerprint")
    return (
        isinstance(cursor.get("generation"), str)
        and (record_id is None or isinstance(record_id, str))
        and isinstance(fingerprint, str)
    )


def _nonnegative_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_archive_cursor(cursor: object) -> bool:
    if cursor is None:
        return False
    if not isinstance(cursor, dict):
        return False
    offset = cursor.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return False
    if offset == 0:
        return True
    identity, boundary = cursor.get("identity"), cursor.get("boundary")
    return _nonnegative_int_list(identity, 2) and isinstance(boundary, str)


def _valid_projection(projection: object) -> bool:
    return isinstance(projection, dict) and all(
        isinstance(key, str) and isinstance(value, str) for key, value in projection.items()
    )


def _valid_projection_identity(identity: object) -> bool:
    if identity is None:
        return True
    return (
        isinstance(identity, list)
        and len(identity) == 4
        and all(isinstance(value, int) and not isinstance(value, bool) for value in identity)
    )


def _nonnegative_int_list(value: object, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in value
        )
    )


def _complete_line_prefix(rows_path: Path, size: int) -> int:
    if size == 0:
        return 0
    try:
        with rows_path.open("rb") as handle:
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return size
            end = size
            while end > 0:
                start = max(0, end - 64 * 1024)
                handle.seek(start)
                chunk = handle.read(end - start)
                newline = chunk.rfind(b"\n")
                if newline >= 0:
                    return start + newline + 1
                end = start
    except FileNotFoundError:
        return 0
    return 0


class _RowAppender:
    def __init__(
        self,
        index_dir: Path,
        handle: BinaryIO,
        watermark: dict[str, Any] | None,
    ) -> None:
        self.index_dir = index_dir
        self.handle = handle
        self.watermark = watermark
        self._pending = bytearray()
        self._dirty = False
        self.items_walked = 0
        self.rows_written = 0

    @classmethod
    @contextmanager
    def open(cls, index_dir: Path) -> Iterator[_RowAppender]:
        rows_path = index_dir / _ROWS_FILE
        watermark, committed = _committed_state(index_dir, rows_path)
        fd = os.open(
            rows_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
            0o600,
        )
        with os.fdopen(fd, "r+b") as handle:
            handle.truncate(committed)
            handle.seek(committed)
            yield cls(index_dir, handle, watermark)

    def add(self, item: WalkItem) -> None:
        for row in rows_for_walk_item(item):
            encoded = json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
            self._pending.extend(encoded + b"\n")
            self.rows_written += 1
        self.watermark = item.watermark
        self.items_walked += 1
        self._dirty = True
        if item.kind == "checkpoint" or len(self._pending) >= _COMMIT_BYTES:
            self.commit()

    def commit(self) -> None:
        if not self._dirty:
            return
        self.handle.write(bytes(self._pending))
        self.handle.flush()
        os.fsync(self.handle.fileno())
        write_versioned_json(
            self.index_dir / _STATE_FILE,
            {"walk": self.watermark, "rows_bytes": self.handle.tell()},
            schema_version=_STATE_SCHEMA_VERSION,
        )
        self._pending.clear()
        self._dirty = False

    def reset_source(self, source: str) -> None:
        self.commit()
        for key in _RESET_KEYS[source]:
            if self.watermark is not None:
                self.watermark.pop(key, None)
        self._dirty = True
        self.commit()


def _walk_into(log_root: Path, appender: _RowAppender) -> ReportIndexUpdate:
    gaps: list[str] = []
    while True:
        try:
            for item in iter_report_walk(log_root, appender.watermark):
                appender.add(item)
        except SourceGapError as gap:
            if gap.source in gaps:
                raise
            gaps.append(gap.source)
            logger.warning("report_index_source_gap", source=gap.source, detail=str(gap))
            appender.reset_source(gap.source)
            continue
        appender.commit()
        return ReportIndexUpdate(appender.items_walked, appender.rows_written, tuple(gaps))


def _read_rows(rows_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    by_kind: dict[str, dict[str, dict[str, Any]]] = {
        SESSION_KIND: {},
        REQUEST_KIND: {},
        TOOL_KIND: {},
        SUBAGENT_KIND: {},
    }
    for _, raw in iter_tolerant_session_index_lines(rows_path, complete_only=True):
        row = normalize_report_row(raw)
        if row is None:
            continue
        kind, key = row.get("kind"), row.get("key")
        if isinstance(kind, str) and isinstance(key, str):
            by_kind[kind][key] = row
    return by_kind


class _SessionAttribution:
    """Maps event times to attempts, with equal starts ordered by attempt key."""

    def __init__(self, sessions: dict[str, dict[str, Any]]) -> None:
        self._entries: dict[str, list[tuple[int, str]]] = {}
        for key, row in sessions.items():
            session_id, time_ms = row.get("session_id"), row.get("time_ms")
            if not isinstance(session_id, str) or not session_id:
                continue
            start = time_ms if isinstance(time_ms, int) and not isinstance(time_ms, bool) else -1
            self._entries.setdefault(session_id, []).append((start, key))
        self._starts: dict[str, list[int]] = {}
        for session_id, entries in self._entries.items():
            entries.sort()
            self._starts[session_id] = [start for start, _ in entries]

    def session_for(self, session_id: str | None, time_ms: int | None) -> str | None:
        if session_id is None:
            return None
        entries = self._entries.get(session_id)
        if not entries:
            return None
        if time_ms is None:
            return entries[0][1] if len(entries) == 1 else None
        index = bisect_right(self._starts[session_id], time_ms) - 1
        return entries[0][1] if index < 0 else entries[index][1]


def _resolve_session_measures(sessions: dict[str, dict[str, Any]]) -> None:
    for session in sessions.values():
        for field in CANONICAL_ACCOUNTING_FIELDS:
            session[field] = resolve_token_measure(
                session["harness"], session["provider"], field, session[field]
            )


def _join_rows(rows: dict[str, dict[str, Any]], attribution: _SessionAttribution) -> None:
    for row in rows.values():
        row["session_key"] = attribution.session_for(row.get("session_id"), row.get("time_ms"))


def _join_request_rows(
    requests: dict[str, dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    attribution: _SessionAttribution,
) -> None:
    for row in requests.values():
        session_key = attribution.session_for(row.get("session_id"), row.get("time_ms"))
        row["session_key"] = session_key
        session = sessions.get(session_key) if session_key is not None else None
        _resolve_request_measures(row, session)


def _resolve_request_measures(row: dict[str, Any], session: dict[str, Any] | None) -> None:
    if session is None:
        harness, provider = row["harness"], UNKNOWN_SOURCE
    else:
        harness, provider = session["harness"], session["provider"]
    for field in CANONICAL_ACCOUNTING_FIELDS:
        row[field] = resolve_token_measure(harness, provider, field, row[field])


def _sorted_rows(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return dict(sorted(rows.items()))


def read_report_index(index_dir: Path) -> ReportIndex:
    """Read the latest valid fact for each key and resolve its session join."""
    by_kind = _read_rows(index_dir / _ROWS_FILE)
    sessions = by_kind[SESSION_KIND]
    requests = by_kind[REQUEST_KIND]
    tools = by_kind[TOOL_KIND]
    subagents = by_kind[SUBAGENT_KIND]
    _resolve_session_measures(sessions)
    attribution = _SessionAttribution(sessions)
    _join_request_rows(requests, sessions, attribution)
    _join_rows(tools, attribution)
    _join_rows(subagents, attribution)
    return ReportIndex(
        sessions=_sorted_rows(sessions),
        requests=_sorted_rows(requests),
        tools=_sorted_rows(tools),
        subagents=_sorted_rows(subagents),
    )
