"""Pipeline token usage tracking for autoskillit.

Accumulates token counts keyed by YAML step name. The get_token_summary MCP
tool retrieves the accumulated data grouped by step.

This module is intentionally simple: a dataclass entry, a dict-backed store
with a defensive copy getter, and a module-level singleton.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TokenLog:
    """In-memory store for per-step token usage.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[str, TokenEntry] = {}

    def record(self, step_name: str, token_usage: dict[str, Any] | None) -> None:
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
        logger.debug(
            "token_usage_recorded",
            step_name=step_name,
            invocation_count=e.invocation_count,
        )

    def get_report(self) -> list[dict[str, Any]]:
        """Return a defensive copy of all entries as dicts, in insertion order."""
        return [e.to_dict() for e in self._entries.values()]

    def compute_total(self) -> dict[str, int]:
        """Compute aggregate token counts across all steps."""
        total: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        for entry in self._entries.values():
            total["input_tokens"] += entry.input_tokens
            total["output_tokens"] += entry.output_tokens
            total["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
            total["cache_read_input_tokens"] += entry.cache_read_input_tokens
        return total

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}


# Module-level singleton used by server.py
_token_log = TokenLog()
