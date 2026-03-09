"""Gate policy constants for AutoSkillit MCP tools.

L1 pipeline module. Declares which tools are gated vs. ungated and provides
the canonical error response for a closed gate.

GATED_TOOLS and UNGATED_TOOLS are sourced from autoskillit.core.types (L0)
so that L2 modules (recipe, migration) can also reference the tool registry
without violating layer ordering.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Final

from autoskillit.core import GATED_TOOLS, UNGATED_TOOLS  # noqa: F401

GATE_FILENAME: Final[str] = ".kitchen_gate"
HOOK_CONFIG_FILENAME: Final[str] = ".autoskillit_hook_config.json"


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it


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
    "User must type the open_kitchen prompt to activate. "
    "Check the MCP prompt list for the exact name."
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
            "is_error": True,
            "exit_code": -1,
            "needs_retry": False,
            "retry_reason": "none",
            "stderr": "",
            "token_usage": None,
        }
    )
