"""Codex CLI NDJSON test fixtures."""

from __future__ import annotations

from pathlib import Path

CODEX_SCHEMA_VERSION: int = 1

HAPPY_PATH_SINGLE_TURN: str = "happy_path_single_turn.ndjson"
MULTI_TURN_WITH_COMPACTION: str = "multi_turn_with_compaction.ndjson"
TURN_FAILED_ERROR: str = "turn_failed_error.ndjson"
SESSION_WITH_REASONING: str = "session_with_reasoning.ndjson"
SESSION_WITH_MCP_TOOL_CALL: str = "session_with_mcp_tool_call.ndjson"


def fixture_path(name: str) -> Path:
    """Return the absolute path to a fixture file in this directory."""
    return Path(__file__).parent / name


__all__ = [
    "CODEX_SCHEMA_VERSION",
    "HAPPY_PATH_SINGLE_TURN",
    "MULTI_TURN_WITH_COMPACTION",
    "SESSION_WITH_MCP_TOOL_CALL",
    "SESSION_WITH_REASONING",
    "TURN_FAILED_ERROR",
    "fixture_path",
]
