"""Gate policy constants for AutoSkillit MCP tools.

Layer 0 module — zero internal (autoskillit) imports.
Declares which tools are gated vs. ungated and provides
the canonical error response for a closed gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GateState:
    """Gate enable/disable state consumed by ToolContext (_context.py)."""

    enabled: bool = False


GATED_TOOLS: frozenset[str] = frozenset(
    {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "run_skill_retry",
        "test_check",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
    }
)

UNGATED_TOOLS: frozenset[str] = frozenset(
    {
        "kitchen_status",
        "get_pipeline_report",
        "get_token_summary",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
    }
)


def gate_error_result() -> str:
    """Return the canonical JSON error string for a closed gate.

    Used by _require_enabled() in server.py when _tools_enabled is False.
    Hardcodes retry_reason as "none" (the StrEnum value of RetryReason.NONE)
    to preserve the L0 zero-internal-imports constraint.
    """
    return json.dumps(
        {
            "success": False,
            "result": (
                "AutoSkillit tools are not enabled. "
                "User must type the open_kitchen prompt to activate. "
                "Check the MCP prompt list for the exact name."
            ),
            "session_id": "",
            "subtype": "gate_error",
            "is_error": True,
            "exit_code": -1,
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "",
            "token_usage": None,
        }
    )
