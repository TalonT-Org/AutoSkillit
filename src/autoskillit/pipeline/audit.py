"""Pipeline failure tracking for autoskillit.

Captures non-success results from _build_skill_result() into an in-memory
store. The get_pipeline_report MCP tool retrieves the accumulated failures.

This module is intentionally simple: a dataclass record and a list-backed
store with a defensive copy getter.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_type_hints

from autoskillit.core import FailureRecord, RetryReason, get_logger

logger = get_logger(__name__)

STDERR_MAX_LEN = 500
COMMAND_MAX_LEN = 200

__all__ = ["FailureRecord", "DefaultAuditLog", "STDERR_MAX_LEN", "COMMAND_MAX_LEN"]

_CONCRETE_TYPES = (str, int, bool, float)


def _validate_failure_record_dict(record_dict: dict[str, Any]) -> bool:
    """Return True if record_dict is structurally valid for FailureRecord construction.

    Checks:
    1. All dataclass fields are present as keys.
    2. Values for concrete non-generic fields (str, int, bool, float) match their
       declared type.

    Uses typing.get_type_hints() to resolve string annotations produced by
    ``from __future__ import annotations`` in core/_type_results.py.
    Wrong-type or missing-field records are rejected (return False) so that
    load_from_log_dir skips them rather than silently accepting mistyped data.
    """
    try:
        hints = get_type_hints(FailureRecord)
    except Exception:
        logger.warning(
            "get_type_hints(FailureRecord) failed; falling back to key-presence check",
            exc_info=True,
        )
        return all(f.name in record_dict for f in dataclasses.fields(FailureRecord))

    for f in dataclasses.fields(FailureRecord):
        if f.name not in record_dict:
            return False
        resolved = hints.get(f.name)
        if resolved in _CONCRETE_TYPES:
            val = record_dict[f.name]
            if resolved is int and isinstance(val, bool):
                return False
            if not isinstance(val, resolved):
                return False
    return True


def _iter_session_log_entries(
    log_root: Path,
    since: str,
    filename: str,
    cwd_filter: str = "",
    kitchen_id_filter: str = "",
    campaign_id_filter: str = "",
) -> Iterator[Path]:
    """Yield per-session file paths from sessions.jsonl that pass the filters.

    Handles: JSONL parsing, since_dt filtering (ISO timestamp), cwd filtering,
    kitchen_id filtering, dir_name extraction, and per-session file existence
    check. Each caller is responsible only for reading and accumulating the
    yielded file's JSON content.

    Args:
        log_root:           Root of the session log directory (contains sessions.jsonl).
        since:              ISO timestamp string; sessions before this are skipped.
                            Empty string disables time filtering.
        filename:           Name of the per-session file to look for
                            (e.g. "audit_log.json", "token_usage.json", "step_timing.json").
        cwd_filter:         If non-empty, only yield sessions whose ``cwd`` field matches
                            this string exactly. Empty string disables cwd filtering and
                            yields all sessions regardless of cwd (backward-compatible default).
        kitchen_id_filter:  If non-empty, only yield sessions whose ``kitchen_id`` field
                            matches this string exactly. Falls back to ``pipeline_id`` for
                            sessions written before the rename. Empty string disables
                            kitchen_id filtering. When both cwd_filter and kitchen_id_filter
                            are provided, both must match (AND logic).
        campaign_id_filter: If non-empty, only yield sessions whose ``campaign_id`` field
                            matches this string exactly. Uses ``.get()`` for backward
                            compatibility with pre-existing index entries lacking the field.
                            All active filters apply as AND logic.

    Yields:
        Path to each matching per-session file that exists.
    """
    index_path = Path(log_root) / "sessions.jsonl"
    if not index_path.exists():
        return

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
        except ValueError:
            pass

    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            idx = json.loads(line)
        except json.JSONDecodeError:
            continue

        if since_dt:
            try:
                entry_ts = datetime.fromisoformat(idx.get("timestamp", ""))
                if entry_ts.tzinfo is None:
                    entry_ts = entry_ts.replace(tzinfo=UTC)
                if entry_ts < since_dt:
                    continue
            except (ValueError, TypeError):
                continue

        if cwd_filter and idx.get("cwd") != cwd_filter:
            continue

        if kitchen_id_filter:
            entry_kitchen_id = idx.get("kitchen_id") or idx.get("pipeline_id", "")
            if entry_kitchen_id != kitchen_id_filter:
                continue

        if campaign_id_filter and idx.get("campaign_id") != campaign_id_filter:
            continue

        dir_name = idx.get("dir_name", "")
        if not dir_name:
            continue

        session_file = Path(log_root) / "sessions" / dir_name / filename
        if not session_file.exists():
            continue

        yield session_file


class DefaultAuditLog:
    """In-memory store for pipeline failure records.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so list.append() and list.copy() are safe without locks.
    """

    def __init__(self) -> None:
        self._records: list[FailureRecord] = []

    def record_failure(self, record: FailureRecord) -> None:
        """Append a failure record, applying field truncations."""
        truncated = FailureRecord(
            timestamp=record.timestamp,
            skill_command=record.skill_command[:COMMAND_MAX_LEN],
            exit_code=record.exit_code,
            subtype=record.subtype,
            needs_retry=record.needs_retry,
            retry_reason=record.retry_reason,
            stderr=record.stderr[:STDERR_MAX_LEN],
        )
        self._records.append(truncated)
        logger.warning(
            "skill_failure_recorded",
            skill_command=truncated.skill_command,
            exit_code=truncated.exit_code,
            subtype=truncated.subtype,
            needs_retry=truncated.needs_retry,
        )

    def get_report(self) -> list[FailureRecord]:
        """Return a defensive copy of the current failure list."""
        return list(self._records)

    def get_report_as_dicts(self) -> list[dict[str, Any]]:
        """Return all failure records serialized as dicts."""
        return [r.to_dict() for r in self._records]

    def clear(self) -> None:
        """Reset the store. Call at the start of each new pipeline run."""
        # Reassign rather than mutate in place: creates a new list object,
        # making the "store is now empty" intent unambiguous.
        self._records = []

    def consecutive_failures(self, skill_command: str) -> int:
        """Count consecutive needs_retry=True records for skill_command from the end.

        Iterates the log in reverse, skipping records for other commands.
        Stops (resets count to 0) when a needs_retry=False record is found for
        this command (terminal failure or success sentinel).
        """
        count = 0
        for record in reversed(self._records):
            if record.skill_command != skill_command:
                continue
            if record.needs_retry:
                count += 1
            else:
                break
        return count

    def record_success(self, skill_command: str) -> None:
        """Append a success sentinel to reset the consecutive-failure streak.

        The sentinel is a FailureRecord with needs_retry=False and subtype='success'.
        This is visible in get_report() but does not represent a real failure.
        """
        if not skill_command:
            return
        self._records.append(
            FailureRecord(
                timestamp=datetime.now(UTC).isoformat(),
                skill_command=skill_command[:COMMAND_MAX_LEN],
                exit_code=0,
                subtype="success",
                needs_retry=False,
                retry_reason=RetryReason.NONE.value,
                stderr="",
            )
        )

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
    ) -> int:
        """Reconstruct failure records from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads audit_log.json from each matching session
        directory, and appends FailureRecord instances to self._records.

        cwd_filter: if non-empty, only sessions whose cwd matches are loaded.
        kitchen_id_filter: if non-empty, only sessions whose kitchen_id matches are loaded.
            Falls back to pipeline_id for sessions written before the rename.
        campaign_id_filter: if non-empty, only sessions whose campaign_id matches are loaded.

        Returns the count of session directories successfully loaded.
        """
        count = 0
        for al_path in _iter_session_log_entries(
            log_root, since, "audit_log.json", cwd_filter, kitchen_id_filter, campaign_id_filter
        ):
            try:
                data = json.loads(al_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if not isinstance(data, list):
                continue

            for record_dict in data:
                if not isinstance(record_dict, dict):
                    continue
                if not _validate_failure_record_dict(record_dict):
                    continue
                try:
                    self._records.append(FailureRecord(**record_dict))
                    count += 1
                except (TypeError, KeyError):
                    continue

        return count
