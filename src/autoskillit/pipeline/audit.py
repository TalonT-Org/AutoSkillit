"""Pipeline failure tracking for autoskillit.

Captures non-success results from _build_skill_result() into an in-memory
store. The get_pipeline_report MCP tool retrieves the accumulated failures.

This module is intentionally simple: a dataclass record and a list-backed
store with a defensive copy getter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from autoskillit.core import FailureRecord, RetryReason, get_logger

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
