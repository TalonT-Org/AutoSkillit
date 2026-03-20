"""Pipeline MCP tool response size tracking for autoskillit.

Accumulates byte and estimated token counts for each MCP tool handler response.
The get_token_summary MCP tool retrieves the accumulated data alongside token usage.

This module follows the exact same pattern as tokens.py.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["McpResponseEntry", "DefaultMcpResponseLog"]


@dataclass
class McpResponseEntry:
    """Accumulated response size metrics for a single MCP tool name."""

    tool_name: str
    response_bytes: int = 0
    estimated_response_tokens: int = 0
    invocation_count: int = 0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "response_bytes": self.response_bytes,
            "estimated_response_tokens": self.estimated_response_tokens,
            "invocation_count": self.invocation_count,
        }


class DefaultMcpResponseLog:
    """In-memory accumulator for MCP tool response size metrics.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[str, McpResponseEntry] = {}

    def record(
        self,
        tool_name: str,
        response: str,
        *,
        alert_threshold_tokens: int = 0,
    ) -> bool:
        """Accumulate bytes/tokens for tool_name. Returns True if threshold exceeded."""
        byte_len = len(response.encode("utf-8"))
        estimated_tokens = byte_len // 4
        if tool_name not in self._entries:
            self._entries[tool_name] = McpResponseEntry(tool_name=tool_name)
        entry = self._entries[tool_name]
        entry.response_bytes += byte_len
        entry.estimated_response_tokens += estimated_tokens
        entry.invocation_count += 1
        return alert_threshold_tokens > 0 and estimated_tokens > alert_threshold_tokens

    def get_report(self) -> list[dict]:
        """Return a defensive copy of all entries as dicts, in insertion order."""
        return [e.to_dict() for e in self._entries.values()]

    def compute_total(self) -> dict:
        """Compute aggregate byte, token, and invocation counts across all tools."""
        total_bytes = sum(e.response_bytes for e in self._entries.values())
        total_tokens = sum(e.estimated_response_tokens for e in self._entries.values())
        total_invocations = sum(e.invocation_count for e in self._entries.values())
        return {
            "total_response_bytes": total_bytes,
            "total_estimated_response_tokens": total_tokens,
            "total_invocations": total_invocations,
        }

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}
