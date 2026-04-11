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
    atomic_write,
    get_logger,
    pkg_root,
)
from autoskillit.pipeline import create_background_task
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _apply_triage_gate,
    _build_hook_diagnostic_warning,
    _find_recipe,
    _hook_config_path,
    _prime_quota_cache,
    _quota_refresh_loop,
    _require_not_headless,
    track_response_size,
)

logger = get_logger(__name__)


def _kitchen_failure_envelope(
    exc: BaseException,
    stage: str,
    *,
    user_hint: str | None = None,
) -> str:
    """Return a JSON failure envelope for open_kitchen errors.

    Tool implementations catch exceptions locally and emit domain-specific
    envelopes with helpful ``user_visible_message`` values; the
    ``@track_response_size`` decorator only catches what slips through.
    """
    msg = user_hint or (
        f"open_kitchen failed during {stage}: {type(exc).__name__}. "
        f"Run 'autoskillit doctor' to diagnose, or reinstall if the failure persists."
    )
    return json.dumps(
        {
            "success": False,
            "kitchen": "failed",
            "user_visible_message": msg,
            "error": f"{type(exc).__name__}: {exc}",
            "stage": stage,
        }
    )


_DISPLAY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Execution", ("run_cmd", "run_python", "run_skill")),
    ("Testing & Workspace", ("test_check", "reset_test_dir", "classify_fix", "reset_workspace")),
    (
        "Git Operations",
        ("merge_worktree", "create_unique_branch", "check_pr_mergeable", "set_commit_status"),
    ),
    ("Recipes", ("migrate_recipe", "list_recipes", "load_recipe", "validate_recipe")),
    (
        "Clone & Remote",
        (
            "clone_repo",
            "remove_clone",
            "push_to_remote",
            "register_clone_status",
            "batch_cleanup_clones",
        ),
    ),
    (
        "GitHub",
        (
            "fetch_github_issue",
            "get_issue_title",
            "report_bug",
            "prepare_issue",
            "enrich_issues",
            "claim_issue",
            "release_issue",
            "get_pr_reviews",
            "bulk_close_issues",
        ),
    ),
    (
        "CI & Automation",
        ("wait_for_ci", "wait_for_merge_queue", "toggle_auto_merge", "get_ci_status"),
    ),
    (
        "Telemetry & Diagnostics",
        (
            "read_db",
            "write_telemetry_files",
            "kitchen_status",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
        ),
    ),
    ("Kitchen", ("open_kitchen", "close_kitchen")),
)


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
            "threshold": cfg.threshold if cfg.threshold is not None else 85.0,
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


async def _open_kitchen_handler() -> str | None:
    """Set the tools-enabled flag. Extracted for testability.

    Returns ``None`` on success, or a JSON failure envelope string on error.
    """
    from autoskillit.server import _get_ctx, logger

    ctx = _get_ctx()
    ctx.gate.enable()
    ctx.kitchen_id = str(uuid4())
    ctx.active_recipe_packs = frozenset()
    logger.info("open_kitchen", gate_state="open", kitchen_id=ctx.kitchen_id)

    try:
        _write_hook_config()
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="write_hook_config")

    try:
        await _prime_quota_cache()
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="prime_quota_cache")

    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
    try:
        ctx.quota_refresh_task = create_background_task(
            _quota_refresh_loop(ctx.config.quota_guard),
            label="quota_refresh_loop",
        )
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="start_quota_refresh")

    return None


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

    ctx = _get_ctx()
    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
        ctx.quota_refresh_task = None
    ctx.gate.disable()
    ctx.active_recipe_packs = None
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
    """Return a formatted string listing all tool categories."""
    lines = []
    for name, tools in _DISPLAY_CATEGORIES:
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
    # Headless guard — wrap denial in envelope shape
    if (h := _require_not_headless("open_kitchen")) is not None:
        parsed_h = json.loads(h)
        return json.dumps(
            {
                "success": False,
                "kitchen": "failed",
                "user_visible_message": parsed_h.get(
                    "result",
                    "open_kitchen cannot be called from headless sessions.",
                ),
                "error": "HeadlessDenied",
                "stage": "headless_guard",
            }
        )

    from autoskillit.server import _get_ctx  # noqa: PLC0415

    disabled_subsets = _get_ctx().config.subsets.disabled

    handler_err = await _open_kitchen_handler()
    if handler_err is not None:
        return handler_err

    try:
        await ctx.enable_components(tags={"kitchen"})
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="enable_components")

    try:
        await _redisable_subsets(ctx, disabled_subsets)
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="redisable_subsets")

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
    _categories = _build_tool_category_listing()

    if name is not None:
        tool_ctx = _get_ctx()
        if tool_ctx.recipes is None:
            return _kitchen_failure_envelope(
                RuntimeError("Server not initialized"),
                stage="recipe_context",
                user_hint=(
                    "open_kitchen cannot load a recipe because the server is not "
                    "initialized. Run 'autoskillit doctor' to diagnose."
                ),
            )
        suppressed = tool_ctx.config.migration.suppressed
        _defaults = resolve_ingredient_defaults(Path.cwd())
        try:
            result = tool_ctx.recipes.load_and_validate(
                name,
                Path.cwd(),
                suppressed=suppressed,
                resolved_defaults=_defaults,
                ingredient_overrides=overrides,
            )
        except Exception as exc:
            return _kitchen_failure_envelope(exc, stage="load_and_validate")

        tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))

        try:
            recipe_info = tool_ctx.recipes.find(name, Path.cwd())
        except Exception as exc:
            return _kitchen_failure_envelope(exc, stage="recipe_find")

        try:
            result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
        except Exception as exc:
            return _kitchen_failure_envelope(exc, stage="apply_triage_gate")

        result["success"] = True
        result["kitchen"] = "open"
        result["version"] = __version__

        if "ingredients_table" not in result or not result["ingredients_table"]:
            result["ingredients_table"] = None

        try:
            warning = _build_hook_diagnostic_warning()
        except Exception as exc:
            return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
        if warning:
            result["hook_warning"] = warning.strip()
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
    try:
        if _sous_chef_path.exists():
            text += "\n\n" + _sous_chef_path.read_text()
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="read_sous_chef")

    # Check if the project needs an upgrade
    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    try:
        warning = _build_hook_diagnostic_warning()
    except Exception as exc:
        return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
    if warning:
        text += warning

    return json.dumps(
        {
            "success": True,
            "kitchen": "open",
            "content": text,
            "ingredients_table": None,
            "version": __version__,
        }
    )


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": True})
@track_response_size("close_kitchen")
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen."""
    if (h := _require_not_headless("close_kitchen")) is not None:
        return h
    _close_kitchen_handler()
    await ctx.reset_visibility()
    return "Kitchen is closed."
