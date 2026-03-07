"""Pipeline step timing for autoskillit.

Accumulates wall-clock duration keyed by YAML step name. The get_timing_summary
MCP tool retrieves the accumulated data grouped by step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
