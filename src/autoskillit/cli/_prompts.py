"""Orchestrator system prompt builder — shared helpers and re-export hub.

Domain-specific prompt builders live in sibling modules:
- _prompts_campaign.py   — L3 campaign dispatcher prompt
- _prompts_orchestrator.py — L1/L2 cook session prompt
- _prompts_kitchen.py    — open-kitchen and fleet-dispatch prompts

This module owns shared helpers used by multiple siblings and re-exports
all public symbols so that existing ``from autoskillit.cli._prompts import X``
statements continue to work unchanged.
"""

from __future__ import annotations

from autoskillit.core import pkg_root

# ── Shared helpers (used by sibling _prompts_*.py modules) ──────────────

_MCP_RETRY_INSTRUCTION: str = (
    "If calling open_kitchen produces ANY error — including"
    ' "No such tool available", a tool_use_error, or any is_error response —'
    " retry the call once immediately. Do not explain the error or suggest"
    " troubleshooting steps to the user. Just retry silently.\n"
    'If the retry also fails, output "AutoSkillit MCP server did not start — ending session."'
    " and end."
)


def _read_full_sous_chef() -> str:
    """Read the full sous-chef SKILL.md for injection into L1/L2 orchestration sessions."""
    path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    try:
        return path.read_text()
    except OSError:
        return ""


# ── Re-exports from domain submodules ───────────────────────────────────

from autoskillit.cli._prompts_campaign import (  # noqa: E402
    _build_dynamic_dispatch_section,
    _build_fleet_campaign_prompt,
    _has_dynamic_dispatch,
    _resume_reason_guidance,
)
from autoskillit.cli._prompts_kitchen import (  # noqa: E402
    _build_fleet_dispatch_prompt,
    _build_open_kitchen_prompt,
)
from autoskillit.cli._prompts_orchestrator import (  # noqa: E402
    _COOK_GREETINGS,
    _OPEN_KITCHEN_GREETINGS,
    _build_orchestrator_prompt,
    _get_ingredients_table,
)

__all__ = [
    "_MCP_RETRY_INSTRUCTION",
    "_read_full_sous_chef",
    "_build_fleet_campaign_prompt",
    "_has_dynamic_dispatch",
    "_build_dynamic_dispatch_section",
    "_resume_reason_guidance",
    "_build_orchestrator_prompt",
    "_get_ingredients_table",
    "_COOK_GREETINGS",
    "_OPEN_KITCHEN_GREETINGS",
    "_build_open_kitchen_prompt",
    "_build_fleet_dispatch_prompt",
]
