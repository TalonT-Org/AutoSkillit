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

from pathlib import Path
from typing import Any

from autoskillit.core import SkillExecutionRole
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillProjectionContext,
    parse_frontmatter_content,
    project_agent_skill_document,
)

# ── Shared helpers (used by sibling _prompts_*.py modules) ──────────────

_MCP_RETRY_INSTRUCTION: str = (
    "If calling open_kitchen produces ANY error — including"
    ' "No such tool available", a tool_use_error, or any is_error response —'
    " retry the call once immediately. Do not explain the error or suggest"
    " troubleshooting steps to the user. Just retry silently.\n"
    'If the retry also fails, output "AutoSkillit MCP server did not start — ending session."'
    " and end."
)


def _read_full_sous_chef(
    skill_catalog: EffectiveSkillCatalog | None = None,
    *,
    project_dir: Path | None = None,
    backend: Any | None = None,
) -> str:
    """Project the effective sous-chef contract for an orchestrator prompt."""
    effective_root = (
        project_dir
        or (skill_catalog.project_root if skill_catalog is not None else None)
        or Path.cwd()
    ).resolve()
    catalog = skill_catalog or DefaultSkillResolver().list_effective(
        effective_root,
        SkillExecutionRole.ORCHESTRATOR,
    )
    if catalog.execution_role is not SkillExecutionRole.ORCHESTRATOR:
        raise ValueError("sous-chef projection requires an orchestrator skill catalog")
    sous_chef = next((skill for skill in catalog.skills if skill.name == "sous-chef"), None)
    if (
        sous_chef is None
        or sous_chef.invalid_reason is not None
        or sous_chef.execution_role is not SkillExecutionRole.ORCHESTRATOR
    ):
        return ""
    projected = project_agent_skill_document(
        sous_chef,
        SkillProjectionContext(
            execution_cwd=effective_root,
            catalog=catalog,
            backend=backend,
            conventions=getattr(backend, "conventions", None),
            gating=False,
        ),
    ).content
    parsed = parse_frontmatter_content(projected)
    return parsed.body.lstrip("\n") if parsed.is_valid else projected


def _ingredient_table_display_instruction(source: str) -> str:
    """Return the display-verbatim instruction for an ingredient table."""
    return (
        f"Display the ingredient table from {source} verbatim in your response — "
        "do not reformat or re-render it.\n"
        "Then ask for the required fields (marked with *). If the recipe has both\n"
        "a task and an issue_url ingredient, mention that a GitHub issue URL can be\n"
        "provided as the task. Keep it to one or two short sentences."
    )


def _backend_supplement(has_unguarded_filesystem_access: bool) -> str:
    if has_unguarded_filesystem_access:
        return (
            "\n\nBACKEND-SPECIFIC CONSTRAINTS (unguarded filesystem access):\n"
            "- NEVER use run_cmd to read recipe YAML files, SKILL.md files, or agent "
            "definition files from the package directory. These raw files contain "
            "unresolved metadata that does not reflect the resolved state.\n"
            "- To recall step definitions or routing, call load_recipe.\n"
            "- To load skill instructions, call the Skill tool.\n"
            "- run_cmd is for executing project-level commands only — never for reading "
            "AutoSkillit package internals."
        )
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
    "_ingredient_table_display_instruction",
    "_backend_supplement",
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
