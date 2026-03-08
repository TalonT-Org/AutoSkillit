"""Pipeline token usage tracking for autoskillit.

Accumulates token counts keyed by YAML step name. The get_token_summary MCP
tool retrieves the accumulated data grouped by step.

This module is intentionally simple: a dataclass entry and a dict-backed store
with a defensive copy getter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger

logger = get_logger(__name__)


@dataclass
class TokenEntry:
    """Accumulated token usage for a single YAML step name."""

    step_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    invocation_count: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["TokenEntry", "DefaultTokenLog"]


class DefaultTokenLog:
    """In-memory store for per-step token usage.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[str, TokenEntry] = {}

    def record(
        self,
        step_name: str,
        token_usage: dict[str, Any] | None,
        *,
        start_ts: str = "",
        end_ts: str = "",
        elapsed_seconds: float | None = None,
    ) -> None:
        """Accumulate token usage for a step.

        No-op if step_name is empty or token_usage is None.
        """
        if not step_name or not token_usage:
            return
        if step_name not in self._entries:
            self._entries[step_name] = TokenEntry(step_name=step_name)
        e = self._entries[step_name]
        e.input_tokens += token_usage.get("input_tokens", 0)
        e.output_tokens += token_usage.get("output_tokens", 0)
        e.cache_creation_input_tokens += token_usage.get("cache_creation_input_tokens", 0)
        e.cache_read_input_tokens += token_usage.get("cache_read_input_tokens", 0)
        e.invocation_count += 1
        if elapsed_seconds is not None:
            e.elapsed_seconds += elapsed_seconds
        elif start_ts and end_ts:
            try:
                delta = (
                    datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
                ).total_seconds()
                e.elapsed_seconds += max(0.0, delta)
            except ValueError:
                pass
        logger.debug(
            "token_usage_recorded",
            step_name=step_name,
            invocation_count=e.invocation_count,
        )

    def get_report(self) -> list[dict[str, Any]]:
        """Return a defensive copy of all entries as dicts, in insertion order."""
        return [e.to_dict() for e in self._entries.values()]

    def compute_total(self) -> dict[str, Any]:
        """Compute aggregate token counts and elapsed time across all steps."""
        total: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_elapsed_seconds": 0.0,
        }
        for entry in self._entries.values():
            total["input_tokens"] += entry.input_tokens
            total["output_tokens"] += entry.output_tokens
            total["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
            total["cache_read_input_tokens"] += entry.cache_read_input_tokens
            total["total_elapsed_seconds"] += entry.elapsed_seconds
        return total

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int:
        """Reconstruct token entries from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads token_usage.json from each matching session
        directory, and accumulates into self._entries (merging by step_name
        with the existing in-memory state).

        Returns the count of session directories successfully loaded.
        """
        import json

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

            tu_path = Path(log_root) / "sessions" / dir_name / "token_usage.json"
            if not tu_path.exists():
                continue

            try:
                data = json.loads(tu_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            step_name = data.get("step_name", "")
            if not step_name:
                continue

            if step_name not in self._entries:
                self._entries[step_name] = TokenEntry(step_name=step_name)
            e = self._entries[step_name]
            e.input_tokens += data.get("input_tokens", 0)
            e.output_tokens += data.get("output_tokens", 0)
            e.cache_creation_input_tokens += data.get("cache_creation_input_tokens", 0)
            e.cache_read_input_tokens += data.get("cache_read_input_tokens", 0)
            e.elapsed_seconds += float(data.get("timing_seconds", 0.0))
            e.invocation_count += 1
            count += 1

        return count
