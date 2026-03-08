"""MCP prompts and resource handlers: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp.prompts import Message, PromptResult

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, pkg_root
from autoskillit.server import mcp


async def _prime_quota_cache() -> None:
    """Fetch quota from the Anthropic API and write the local cache.

    Called at open_kitchen so the cache is primed before any run_skill hook fires.
    Fails open: a quota fetch failure must not abort kitchen open.
    """
    from autoskillit.execution import check_and_sleep_if_needed
    from autoskillit.server import _get_ctx, logger

    try:
        await check_and_sleep_if_needed(_get_ctx().config.quota_guard)
    except Exception:
        logger.warning("quota_prime_failed", exc_info=True)


def _write_hook_config() -> None:
    """Write user-configured quota values to temp/.autoskillit_hook_config.json.

    The hook subprocess (quota_check.py) reads this file to apply user settings
    without importing the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger

    cfg = _get_ctx().config.quota_guard
    payload = {
        "quota_guard": {
            "threshold": cfg.threshold,
            "cache_max_age": cfg.cache_max_age,
            "cache_path": cfg.cache_path,
        }
    }
    hook_cfg_path = Path.cwd() / "temp" / ".autoskillit_hook_config.json"
    try:
        hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg_path.write_text(json.dumps(payload))
    except OSError:
        logger.warning("hook_config_write_failed", path=str(hook_cfg_path))


async def _open_kitchen_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate.enable()
    logger.info("open_kitchen", gate_state="open")
    gate_path = Path.cwd() / "temp" / ".kitchen_gate"
    try:
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.touch()
    except OSError:
        logger.warning("gate_file_write_failed", path=str(gate_path))
    await _prime_quota_cache()
    _write_hook_config()


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate.disable()
    logger.info("close_kitchen", gate_state="closed")
    gate_path = Path.cwd() / "temp" / ".kitchen_gate"
    try:
        gate_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("gate_file_remove_failed", path=str(gate_path))
    hook_cfg_path = Path.cwd() / "temp" / ".autoskillit_hook_config.json"
    try:
        hook_cfg_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return recipe YAML for the orchestrating agent to follow."""
    from autoskillit.recipe import find_recipe_by_name

    match = find_recipe_by_name(name, Path.cwd())
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})
    return match.path.read_text()


@mcp.prompt()
async def open_kitchen() -> PromptResult:
    """Open the AutoSkillit kitchen for service."""
    await _open_kitchen_handler()

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)

    text = (
        "Kitchen is open. AutoSkillit tools are ready for service. "
        "Call the kitchen_status tool now to display version "
        "and health information to the user.\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill, which launches "
        "headless sessions with full tool access. Do NOT use native tools to "
        "investigate failures — route to on_failure and let the downstream skill handle diagnosis."
    )

    # Inject sous-chef global orchestration rules (graceful degradation if absent)
    _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if _sous_chef_path.exists():
        text += "\n\n" + _sous_chef_path.read_text()

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
