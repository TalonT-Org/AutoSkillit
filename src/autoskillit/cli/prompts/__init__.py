"""Prompt-builder package — hub-and-spoke re-export facade, mirrors cli/doctor's pattern."""

from autoskillit.cli.prompts._prompts import (
    _COOK_GREETINGS,
    _MCP_RETRY_INSTRUCTION,
    _OPEN_KITCHEN_GREETINGS,
    _backend_supplement,
    _build_dynamic_dispatch_section,
    _build_fleet_campaign_prompt,
    _build_fleet_dispatch_prompt,
    _build_open_kitchen_prompt,
    _build_orchestrator_prompt,
    _get_ingredients_table,
    _has_dynamic_dispatch,
    _ingredient_table_display_instruction,
    _read_full_sous_chef,
    _resume_reason_guidance,
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
