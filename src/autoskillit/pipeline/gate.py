"""Gate policy constants for AutoSkillit MCP tools.

Layer 0 module — zero internal (autoskillit) imports.
Declares which tools are gated vs. ungated and provides
the canonical error response for a closed gate.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


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
        "migrate_recipe",
        # Clone lifecycle tools (promoted from python: recipe steps)
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "report_bug",
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
        "fetch_github_issue",
    }
)


_DEFAULT_GATE_MESSAGE = (
    "AutoSkillit tools are not enabled. "
    "User must type the open_kitchen prompt to activate. "
    "Check the MCP prompt list for the exact name."
)


GATE_STATE_FILENAME = ".kitchen_gate"


def write_gate_file(temp_dir: Path) -> None:
    """Write the gate state file to signal kitchen is open."""
    gate_path = temp_dir / GATE_STATE_FILENAME
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    content = str(os.getpid())
    fd, tmp = tempfile.mkstemp(dir=gate_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, gate_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_gate_file(temp_dir: Path) -> None:
    """Remove the gate state file. No-op if absent."""
    gate_path = temp_dir / GATE_STATE_FILENAME
    gate_path.unlink(missing_ok=True)


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
