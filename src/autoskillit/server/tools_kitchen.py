"""MCP tool handlers and resource: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import atexit
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, atomic_write, pkg_root
from autoskillit.execution import read_boot_id, read_starttime_ticks
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _find_recipe,
    _gate_file_path,
    _hook_config_path,
    _prime_quota_cache,
)


def _register_gate_cleanup() -> None:
    """Write the gate file and register an atexit handler to remove it on exit."""
    gate_file = _gate_file_path(pkg_root())
    try:
        gate_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "starttime_ticks": read_starttime_ticks(os.getpid()),
            "boot_id": read_boot_id(),
            "opened_at": datetime.now(UTC).isoformat(),
        }
        atomic_write(gate_file, json.dumps(payload))
    except OSError:
        return

    def _cleanup() -> None:
        try:
            gate_file.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)


def _write_hook_config() -> None:
    """Write user-configured quota values to temp/.autoskillit_hook_config.json.

    The hook subprocess (quota_check.py) reads this file to apply user settings
    without importing the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger

    cfg = _get_ctx().config.quota_guard
    payload = {
        "quota_guard": {
            "threshold": cfg.threshold if cfg.threshold is not None else 90.0,
            "cache_max_age": cfg.cache_max_age if cfg.cache_max_age is not None else 300,
            "cache_path": cfg.cache_path
            if cfg.cache_path is not None
            else "~/.claude/autoskillit_quota_cache.json",
        }
    }
    hook_cfg_path = _hook_config_path(Path.cwd())
    try:
        hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hook_cfg_path, json.dumps(payload))
    except OSError:
        logger.warning("hook_config_write_failed", path=str(hook_cfg_path))


async def _open_kitchen_handler() -> None:
    """Set the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate.enable()
    logger.info("open_kitchen", gate_state="open")
    _write_hook_config()
    _register_gate_cleanup()
    await _prime_quota_cache()


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate.disable()
    logger.info("close_kitchen", gate_state="closed")
    hook_cfg_path = _hook_config_path(Path.cwd())
    try:
        hook_cfg_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return recipe YAML for the orchestrating agent to follow."""
    match = _find_recipe(name, Path.cwd())
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})
    return match.path.read_text()


@mcp.tool(tags={"automation"})
async def open_kitchen(ctx: Context = CurrentContext()) -> str:
    """Open the AutoSkillit kitchen for service."""
    await _open_kitchen_handler()
    await ctx.enable_components(tags={"kitchen"})

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

    return text


@mcp.tool(tags={"automation"})
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen."""
    _close_kitchen_handler()
    await ctx.reset_visibility()
    return "Kitchen is closed."
