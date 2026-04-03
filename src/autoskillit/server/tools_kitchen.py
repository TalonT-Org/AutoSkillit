"""MCP tool handlers and resource: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit import __version__
from autoskillit.config import resolve_ingredient_defaults
from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    TOOL_CATEGORIES,
    atomic_write,
    get_logger,
    pkg_root,
)
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _apply_triage_gate,
    _find_recipe,
    _hook_config_path,
    _prime_quota_cache,
    _require_not_headless,
    track_response_size,
)

logger = get_logger(__name__)


def _write_hook_config() -> None:
    """Write user-configured quota values to temp/.autoskillit_hook_config.json.

    The hook subprocess (quota_check.py) reads this file to apply user settings
    without importing the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger

    ctx = _get_ctx()
    cfg = ctx.config.quota_guard
    payload = {
        "quota_guard": {
            "threshold": cfg.threshold if cfg.threshold is not None else 90.0,
            "cache_max_age": cfg.cache_max_age if cfg.cache_max_age is not None else 300,
            "cache_path": cfg.cache_path
            if cfg.cache_path is not None
            else "~/.claude/autoskillit_quota_cache.json",
        },
        "kitchen_id": ctx.kitchen_id,
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

    ctx = _get_ctx()
    ctx.gate.enable()
    ctx.kitchen_id = str(uuid4())
    # Store recipe packs — populated from LoadRecipeResult.requires_packs in #524.
    # For now, store empty frozenset to establish the contract.
    ctx.active_recipe_packs = frozenset()
    logger.info("open_kitchen", gate_state="open", kitchen_id=ctx.kitchen_id)
    _write_hook_config()
    await _prime_quota_cache()


async def _redisable_subsets(ctx: Context, disabled: list[str]) -> None:
    """Re-disable subset-tagged tools after enabling kitchen.

    REQ-VIS-008: FastMCP session rules override server rules; enable_components(kitchen)
    would otherwise reveal dual-tagged tools (e.g. kitchen+github) that are server-disabled.
    Later session rules win, so these disables correctly override the kitchen enable.
    """
    for subset in disabled:
        await ctx.disable_components(tags={subset})


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    _get_ctx().gate.disable()
    _get_ctx().active_recipe_packs = None
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


def _build_tool_category_listing() -> str:
    """Return a formatted string listing all tool categories from TOOL_CATEGORIES."""
    lines = []
    for name, tools in TOOL_CATEGORIES:
        lines.append(f"  {name}: {', '.join(tools)}")
    return "\n".join(lines)


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": True})
@track_response_size("open_kitchen")
async def open_kitchen(
    name: str | None = None,
    overrides: dict[str, str] | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Open the AutoSkillit kitchen for service.

    When ``name`` is provided, the kitchen is opened AND the named recipe is
    loaded in a single call, reducing terminal noise from two tool calls to one.

    Args:
        name: Optional recipe name to load immediately after opening.
        overrides: Optional dict of ingredient name → value to override recipe defaults.
            Use to activate hidden features (e.g., ``{"sprint_mode": "true"}``).
    """
    if (h := _require_not_headless("open_kitchen")) is not None:
        return h

    from autoskillit.server import _get_ctx  # noqa: PLC0415

    disabled_subsets = _get_ctx().config.subsets.disabled
    await _open_kitchen_handler()
    await ctx.enable_components(tags={"kitchen"})
    await _redisable_subsets(ctx, disabled_subsets)

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
    _categories = _build_tool_category_listing()

    if name is not None:
        tool_ctx = _get_ctx()
        if tool_ctx.recipes is None:
            return json.dumps({"error": "Server not initialized", "kitchen": "open"})
        suppressed = tool_ctx.config.migration.suppressed
        _defaults = resolve_ingredient_defaults(Path.cwd())
        result = tool_ctx.recipes.load_and_validate(
            name,
            Path.cwd(),
            suppressed=suppressed,
            resolved_defaults=_defaults,
            ingredient_overrides=overrides,
        )
        tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
        recipe_info = tool_ctx.recipes.find(name, Path.cwd())
        result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
        result["kitchen"] = "open"
        result["version"] = __version__
        return json.dumps(result)

    text = (
        f"Kitchen is open. AutoSkillit {__version__}. Tools are ready for service.\n\n"
        f"Available Tools by Category:\n{_categories}\n\n"
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


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": True})
@track_response_size("close_kitchen")
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen."""
    if (h := _require_not_headless("close_kitchen")) is not None:
        return h
    _close_kitchen_handler()
    await ctx.reset_visibility()
    return "Kitchen is closed."
