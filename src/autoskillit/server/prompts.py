"""MCP prompts and resource handlers: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp.prompts import Message, PromptResult

from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS
from autoskillit.pipeline.gate import GateState
from autoskillit.server import mcp


def _open_kitchen_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate = GateState(enabled=True)
    logger.info("open_kitchen", gate_state="open")


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate = GateState(enabled=False)
    logger.info("close_kitchen", gate_state="closed")


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return recipe YAML for the orchestrating agent to follow."""
    from autoskillit.recipe.io import find_recipe_by_name

    match = find_recipe_by_name(name, Path.cwd())
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})
    return match.path.read_text()


@mcp.prompt()
def open_kitchen() -> PromptResult:
    """Open the AutoSkillit kitchen for service."""
    _open_kitchen_handler()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)

    text = (
        "Kitchen is open. AutoSkillit tools are ready for service. "
        "Call the kitchen_status tool now to display version "
        "and health information to the user.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill or "
        "run_skill_retry, which launch headless sessions with full "
        "tool access. Do NOT use native tools to investigate failures — "
        "route to on_failure and let the downstream skill handle diagnosis."
    )

    # Check if the project needs an upgrade
    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    return PromptResult([Message(text, role="user")])


@mcp.prompt()
def close_kitchen() -> PromptResult:
    """Close the AutoSkillit kitchen."""
    _close_kitchen_handler()
    return PromptResult([Message("Kitchen is closed.", role="assistant")])
