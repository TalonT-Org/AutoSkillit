"""Pipeline step timing for autoskillit.

Accumulates wall-clock duration keyed by YAML step name. The get_timing_summary
MCP tool retrieves the accumulated data grouped by step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger

logger = get_logger(__name__)


@dataclass
class TimingEntry:
    """Accumulated wall-clock duration for a single YAML step name."""

    step_name: str
    total_seconds: float = 0.0
    invocation_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["TimingEntry", "DefaultTimingLog"]


class DefaultTimingLog:
    """In-memory store for per-step wall-clock timing.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[str, TimingEntry] = {}

    def record(self, step_name: str, duration_seconds: float) -> None:
        if not step_name:
            return
        if step_name not in self._entries:
            self._entries[step_name] = TimingEntry(step_name=step_name)
        e = self._entries[step_name]
        e.total_seconds += max(0.0, duration_seconds)
        e.invocation_count += 1
        logger.debug(
            "timing_recorded",
            step_name=step_name,
            invocation_count=e.invocation_count,
        )

    def get_report(self) -> list[dict[str, Any]]:
        """Return a defensive copy of all entries as dicts, in insertion order."""
        return [e.to_dict() for e in self._entries.values()]

    def compute_total(self) -> dict[str, Any]:
        """Compute aggregate elapsed time across all steps."""
        return {"total_seconds": sum(e.total_seconds for e in self._entries.values())}

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int:
        """Reconstruct timing entries from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads step_timing.json from each matching session
        directory, and accumulates into self._entries.

        Returns the count of session directories successfully loaded.
        """
        import json
        from datetime import datetime
        from pathlib import Path as _Path

        index_path = _Path(log_root) / "sessions.jsonl"
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
                    if entry_ts < since_dt:
                        continue
                except (ValueError, TypeError):
                    continue

            dir_name = idx.get("dir_name", "")
            if not dir_name:
                continue

            st_path = _Path(log_root) / "sessions" / dir_name / "step_timing.json"
            if not st_path.exists():
                continue

            try:
                data = json.loads(st_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            step_name = data.get("step_name", "")
            if not step_name:
                continue

            if step_name not in self._entries:
                self._entries[step_name] = TimingEntry(step_name=step_name)
            e = self._entries[step_name]
            e.total_seconds += max(0.0, float(data.get("total_seconds", 0.0)))
            e.invocation_count += 1
            count += 1

        return count
