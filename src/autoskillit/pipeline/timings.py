"""Pipeline step timing for autoskillit.

Accumulates wall-clock duration keyed by YAML step name. The get_timing_summary
MCP tool retrieves the accumulated data grouped by step.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger
from autoskillit.pipeline.audit import _iter_session_log_entries
from autoskillit.pipeline.tokens import canonical_step_name

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

    Entries are keyed by (order_id, canonical_step_name). An empty string
    order_id represents an unscoped (legacy) entry. This allows per-issue
    isolation when order_id is supplied, while preserving backward-compatible
    aggregation across all orders when order_id is omitted.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], TimingEntry] = {}

    def record(self, step_name: str, duration_seconds: float, *, order_id: str = "") -> None:
        if not step_name:
            return
        canonical = canonical_step_name(step_name)
        key = (order_id, canonical)
        if key not in self._entries:
            self._entries[key] = TimingEntry(step_name=canonical)
        e = self._entries[key]
        e.total_seconds += max(0.0, duration_seconds)
        e.invocation_count += 1
        logger.debug(
            "timing_recorded",
            step_name=canonical,
            order_id=order_id,
            invocation_count=e.invocation_count,
        )

    def get_report(self, *, order_id: str = "") -> list[dict[str, Any]]:
        """Return entries as dicts, optionally filtered by order_id.

        If order_id is empty, aggregate ALL entries by step_name across all
        order buckets (backward-compatible behavior). If order_id is non-empty,
        return only entries for that order.
        """
        if order_id:
            return [e.to_dict() for (oid, _step), e in self._entries.items() if oid == order_id]
        # Aggregate all orders by step_name
        aggregated: dict[str, TimingEntry] = {}
        for (_oid, step), e in self._entries.items():
            if step not in aggregated:
                aggregated[step] = TimingEntry(step_name=step)
            agg = aggregated[step]
            agg.total_seconds += e.total_seconds
            agg.invocation_count += e.invocation_count
        return [e.to_dict() for e in aggregated.values()]

    def compute_total(self, *, order_id: str = "") -> dict[str, Any]:
        """Compute aggregate elapsed time across all steps.

        If order_id is empty, sum all entries. If order_id is non-empty, sum
        only entries for that order.
        """
        total = 0.0
        for (oid, _step), entry in self._entries.items():
            if order_id and oid != order_id:
                continue
            total += entry.total_seconds
        return {"total_seconds": total}

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
        order_id_filter: str = "",
    ) -> int:
        """Reconstruct timing entries from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads step_timing.json from each matching session
        directory, and accumulates into self._entries.

        cwd_filter: if non-empty, only sessions whose cwd matches are loaded.
        kitchen_id_filter: if non-empty, only sessions whose kitchen_id matches are loaded.
            Falls back to pipeline_id for sessions written before the rename.
        campaign_id_filter: if non-empty, only sessions whose campaign_id matches are loaded.
        order_id_filter: if non-empty, only sessions whose order_id matches are loaded.

        Returns the count of session directories successfully loaded.
        """
        count = 0
        for st_path in _iter_session_log_entries(
            log_root,
            since,
            "step_timing.json",
            cwd_filter,
            kitchen_id_filter,
            campaign_id_filter,
            order_id_filter,
        ):
            try:
                data = json.loads(st_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            raw_step = data.get("step_name", "")
            if not raw_step:
                continue

            step_name = canonical_step_name(raw_step)
            entry_order_id = data.get("order_id", "")
            key = (entry_order_id, step_name)
            if key not in self._entries:
                self._entries[key] = TimingEntry(step_name=step_name)
            e = self._entries[key]
            _raw_total = data.get("total_seconds")
            e.total_seconds += max(0.0, float(_raw_total) if _raw_total is not None else 0.0)
            e.invocation_count += 1
            count += 1

        return count
