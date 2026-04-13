"""Gate policy constants for AutoSkillit MCP tools.

L1 pipeline module. Declares which tools are gated vs. ungated and provides
the canonical error response for a closed gate.

GATED_TOOLS and UNGATED_TOOLS are sourced from autoskillit.core.types (L0)
so that L2 modules (recipe, migration) can also reference the tool registry
without violating layer ordering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from autoskillit.core import GATED_TOOLS, UNGATED_TOOLS, KillReason  # noqa: F401


@dataclass(slots=True)
class DefaultGateState:
    """Gate enable/disable state consumed by ToolContext (_context.py)."""

    enabled: bool = False

    def enable(self) -> None:
        """Transition gate to enabled state in-place."""
        self.enabled = True

    def disable(self) -> None:
        """Transition gate to disabled state in-place."""
        self.enabled = False


_DEFAULT_GATE_MESSAGE = (
    "AutoSkillit tools are not enabled. "
    "Call the open_kitchen tool to activate (visible in your tool list). "
    "Kitchen tools are hidden at startup and revealed per-session when open_kitchen is called."
)


def gate_error_result(message: str | None = None) -> str:
    """Return the canonical JSON error string for a closed or blocked gate.

    message: Optional custom error text. When omitted, returns the default
    'tools not enabled' message for gate-closed errors.

    Hardcodes retry_reason as "none" (the StrEnum value of RetryReason.NONE)
    to preserve the L0 zero-internal-imports constraint.
    """
    return json.dumps(
        {
            "success": False,
            "result": message if message is not None else _DEFAULT_GATE_MESSAGE,
            "session_id": "",
            "subtype": "gate_error",
            "cli_subtype": "",
            "is_error": True,
            "exit_code": -1,
            "kill_reason": KillReason.NATURAL_EXIT,
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "",
            "token_usage": None,
            "write_path_warnings": [],
            "write_call_count": 0,
        }
    )


_DEFAULT_HEADLESS_MESSAGE = (
    "This tool cannot be called from headless sessions. "
    "Headless workers (Tier 2) may only use native Claude Code tools and "
    "HEADLESS_TOOLS. Orchestration tools are reserved for Tier 1 sessions."
)


def headless_error_result(message: str | None = None) -> str:
    """Return a canonical JSON error for tools blocked in headless sessions.

    Uses subtype='headless_error' to distinguish from gate_error
    (kitchen closed). All other fields match the standard 9-field response
    envelope so orchestrators can route failures without schema inspection.
    """
    return json.dumps(
        {
            "success": False,
            "result": message if message is not None else _DEFAULT_HEADLESS_MESSAGE,
            "session_id": "",
            "subtype": "headless_error",
            "cli_subtype": "",
            "is_error": True,
            "exit_code": -1,
            "kill_reason": KillReason.NATURAL_EXIT,
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "",
            "token_usage": None,
            "write_path_warnings": [],
            "write_call_count": 0,
        }
    )
