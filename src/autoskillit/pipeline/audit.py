"""Pipeline failure tracking for autoskillit.

Captures non-success results from _build_skill_result() into an in-memory
store. The get_pipeline_report MCP tool retrieves the accumulated failures.

This module is intentionally simple: a dataclass record and a list-backed
store with a defensive copy getter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import FailureRecord, get_logger

logger = get_logger(__name__)

STDERR_MAX_LEN = 500
COMMAND_MAX_LEN = 200

__all__ = ["FailureRecord", "DefaultAuditLog", "STDERR_MAX_LEN", "COMMAND_MAX_LEN"]


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

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int:
        """Reconstruct failure records from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads audit_log.json from each matching session
        directory, and appends FailureRecord instances to self._records.

        Returns the count of session directories successfully loaded.
        """
        import json
        from datetime import UTC, datetime

        index_path = Path(log_root) / "sessions.jsonl"
        if not index_path.exists():
            return 0

        since_dt: datetime | None = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                pass

        count = 0
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

            dir_name = idx.get("dir_name", "")
            if not dir_name:
                continue

            al_path = Path(log_root) / "sessions" / dir_name / "audit_log.json"
            if not al_path.exists():
                continue

            try:
                data = json.loads(al_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if not isinstance(data, list):
                continue

            for record_dict in data:
                try:
                    self._records.append(FailureRecord(**record_dict))
                    count += 1
                except (TypeError, KeyError):
                    continue

        return count
