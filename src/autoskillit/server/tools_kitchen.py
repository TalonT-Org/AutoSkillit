"""MCP tool handlers and resource: open_kitchen, close_kitchen, recipe:// resource."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict
from uuid import uuid4

if TYPE_CHECKING:
    from autoskillit.config.settings import QuotaGuardConfig

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit import __version__
from autoskillit.config import iter_display_categories, resolve_ingredient_defaults
from autoskillit.core import (
    PIPELINE_FORBIDDEN_TOOLS,
    _collect_disabled_feature_tags,
    atomic_write,
    find_latest_session_id,
    get_logger,
    pkg_root,
)
from autoskillit.pipeline import create_background_task
from autoskillit.server import mcp
from autoskillit.server.helpers import (
    _apply_triage_gate,
    _build_hook_diagnostic_warning,
    _hook_config_path,
    _prime_quota_cache,
    _quota_refresh_loop,
    _require_orchestrator_exact,
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
        f"Run 'autoskillit doctor' to diagnose, "
        f"or run 'autoskillit install' if the failure persists."
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


class QuotaGuardHookPayload(TypedDict):
    cache_max_age: int
    cache_path: str
    buffer_seconds: int
    disabled: bool


def _quota_guard_hook_payload(cfg: QuotaGuardConfig) -> QuotaGuardHookPayload:
    """Return the quota_guard section of .hook_config.json for a given config.

    This is the single authoritative definition of which QuotaGuardConfig fields
    cross the stdlib-only boundary into hook subprocesses. When adding a field to
    QuotaHookSettings, add the corresponding source field here AND update
    QUOTA_GUARD_HOOK_PAYLOAD_KEYS in _hook_settings.py. The contract test
    test_hook_bridge_coverage.py enforces that both stay in sync.
    """
    return {
        "cache_max_age": cfg.cache_max_age,
        "cache_path": cfg.cache_path,
        "buffer_seconds": cfg.buffer_seconds,
        "disabled": not cfg.enabled,
    }


def _build_sous_chef_for_delivery(sc_text: str) -> str:
    """Return stripped sous-chef SKILL.md text for delivery via the named-recipe path."""
    return sc_text.strip()


def _write_hook_config() -> None:
    """Write user-configured quota values to .autoskillit/temp/.hook_config.json.

    The hook subprocess (quota_guard.py) reads this file to apply user settings
    without importing the autoskillit package.
    """
    from autoskillit.server import _get_ctx, logger

    ctx = _get_ctx()
    cfg = ctx.config.quota_guard
    payload = {
        "quota_guard": _quota_guard_hook_payload(cfg),
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
        ctx.gate.disable()
        logger.warning("open_kitchen_failure", stage="write_hook_config", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="write_hook_config")

    try:
        await _prime_quota_cache()
    except Exception as exc:
        ctx.gate.disable()
        logger.warning("open_kitchen_failure", stage="prime_quota_cache", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="prime_quota_cache")

    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
    try:
        ctx.quota_refresh_task = create_background_task(
            _quota_refresh_loop(ctx.config.quota_guard),
            label="quota_refresh_loop",
        )
    except Exception as exc:
        ctx.gate.disable()
        logger.warning("open_kitchen_failure", stage="start_quota_refresh", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="start_quota_refresh")

    try:
        from autoskillit.core import register_active_kitchen  # noqa: PLC0415

        register_active_kitchen(ctx.kitchen_id, os.getpid(), str(Path.cwd()))
    except Exception:
        logger.warning("open_kitchen_registry_failed", exc_info=True)

    return None


async def _redisable_subsets(
    ctx: Context,
    disabled: list[str],
    features: dict[str, bool] | None = None,
    *,
    experimental_enabled: bool = False,
) -> None:
    """Re-disable subset-tagged and feature-disabled tools after enabling kitchen.

    Pass 1 (existing): Re-disable config-disabled subset tags so dual-tagged tools
    (e.g. kitchen+github) that are server-disabled are not accidentally revealed.

    Pass 2: Suppress tool tags for disabled features via `_collect_disabled_feature_tags`.
    Shared tools with kitchen-core retain visibility via the kitchen-core tag
    (FastMCP union model).

    ``features`` defaults to ``None`` (treated as ``{}``, i.e. all features use
    ``FeatureDef.default_enabled``). Pass ``config.features`` from the call site.
    """
    # Pass 1: subset re-disable (existing)
    for subset in disabled:
        await ctx.disable_components(tags={subset})

    # Pass 2: feature gate — suppress tool tags for disabled features
    _features = features or {}
    for tag in _collect_disabled_feature_tags(
        _features, experimental_enabled=experimental_enabled
    ):
        await ctx.disable_components(tags={tag})


def _close_kitchen_handler() -> None:
    """Clear the tools-enabled flag. Extracted for testability."""
    from autoskillit.server import _get_ctx, logger

    ctx = _get_ctx()
    if ctx.quota_refresh_task is not None:
        ctx.quota_refresh_task.cancel()
        ctx.quota_refresh_task = None
    ctx.gate.disable()
    try:
        from autoskillit.core import unregister_active_kitchen  # noqa: PLC0415

        unregister_active_kitchen(ctx.kitchen_id)
    except Exception:
        logger.warning("close_kitchen_registry_failed", exc_info=True)
    ctx.active_recipe_packs = None
    ctx.recipe_name = ""
    ctx.recipe_content_hash = ""
    ctx.recipe_composite_hash = ""
    ctx.recipe_version = ""
    logger.info("close_kitchen", gate_state="closed")
    hook_cfg_path = _hook_config_path(Path.cwd())
    try:
        hook_cfg_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("hook_config_remove_failed", path=str(hook_cfg_path))
    review_gate_path = Path.cwd() / ".autoskillit" / "temp" / "review_gate_state.json"
    try:
        review_gate_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("review_gate_state_remove_failed", path=str(review_gate_path))


@mcp.resource("recipe://{name}")
def get_recipe(name: str) -> str:
    """Return recipe YAML for the orchestrating agent to follow."""
    from autoskillit.server._state import _get_ctx_or_none

    ctx = _get_ctx_or_none()
    match = ctx.recipes.find(name, Path.cwd()) if ctx and ctx.recipes else None
    if match is None:
        return json.dumps({"error": f"No recipe named '{name}'."})
    return match.path.read_text()


def _build_tool_category_listing(
    features: dict[str, bool], *, experimental_enabled: bool = False
) -> str:
    """Return a formatted string listing all tool categories."""
    lines = []
    for name, tools in iter_display_categories(
        features, experimental_enabled=experimental_enabled
    ):
        lines.append(f"  {name}: {', '.join(tools)}")
    return "\n".join(lines)


@mcp.tool(
    tags={"autoskillit"},
    annotations={"readOnlyHint": True},
    meta={"anthropic/maxResultSizeChars": 100_000},
)
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

    Never raises.
    """
    try:
        # Headless guard — wrap denial in envelope shape
        if (h := _require_orchestrator_exact("open_kitchen")) is not None:
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
            logger.warning("open_kitchen_failure", stage="enable_components", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="enable_components")

        try:
            _kctx = _get_ctx()
            await _redisable_subsets(
                ctx,
                disabled_subsets,
                _kctx.config.features,
                experimental_enabled=_kctx.config.experimental_enabled,
            )
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="redisable_subsets", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="redisable_subsets")

        _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        _ctx = _get_ctx()
        _categories = _build_tool_category_listing(
            _ctx.config.features, experimental_enabled=_ctx.config.experimental_enabled
        )

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
            # Runtime enum check: output_mode must be validated before recipe loading
            if name == "research":
                _om_value = (overrides or {}).get("output_mode")
                if _om_value is not None and _om_value not in {"pr", "local"}:
                    return json.dumps(
                        {
                            "error": (
                                f"output_mode must be 'pr' or 'local', got {_om_value!r}. "
                                "Only two modes are supported for the research recipe."
                            )
                        }
                    )
            try:
                result = tool_ctx.recipes.load_and_validate(
                    name,
                    Path.cwd(),
                    suppressed=suppressed,
                    resolved_defaults=_defaults,
                    ingredient_overrides=overrides,
                )
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="load_and_validate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="load_and_validate")

            tool_ctx.active_recipe_packs = frozenset(result.get("requires_packs", []))
            tool_ctx.recipe_name = name
            tool_ctx.recipe_content_hash = result.get("content_hash", "")
            tool_ctx.recipe_composite_hash = result.get("composite_hash", "")
            tool_ctx.recipe_version = result.get("recipe_version", "")

            composite = result.get("composite_hash", "")
            from autoskillit.server._state import _check_rerun  # noqa: PLC0415

            rerun_suggestion = _check_rerun(tool_ctx.config.linux_tracing.log_dir, composite)
            if rerun_suggestion:
                result.setdefault("suggestions", []).append(rerun_suggestion)

            try:
                recipe_info = tool_ctx.recipes.find(name, Path.cwd())
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="recipe_find", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="recipe_find")

            try:
                result = await _apply_triage_gate(result, name, recipe_info=recipe_info)
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="apply_triage_gate", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="apply_triage_gate")

            result["success"] = True
            result["kitchen"] = "open"
            result["version"] = __version__

            if "ingredients_table" not in result or not result["ingredients_table"]:
                result["ingredients_table"] = None

            try:
                warning = _build_hook_diagnostic_warning()
            except Exception as exc:
                logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
                return _kitchen_failure_envelope(exc, stage="hook_diagnostic")
            if warning:
                result["hook_warning"] = warning.strip()

            # Inject sous-chef discipline into the response so headless L2 sessions
            # receive mandatory step-execution rules. Headless sessions receive no
            # system prompt injection; this named-recipe path is their sole delivery
            # channel. Graceful degradation: missing SKILL.md is non-fatal.
            _sc_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
            try:
                if _sc_path.exists():
                    _sc_content = _build_sous_chef_for_delivery(_sc_path.read_text())
                    if _sc_content:
                        result["sous_chef_discipline"] = _sc_content
            except (OSError, UnicodeDecodeError):
                logger.warning(
                    "open_kitchen_failure", stage="read_sous_chef_discipline", exc_info=True
                )

            return json.dumps(result)

        text = (
            f"Kitchen is open. AutoSkillit {__version__}. Tools are ready for service.\n\n"
            f"Available Tools by Category:\n{_categories}\n\n"
            "IMPORTANT — Orchestrator Discipline:\n"
            f"NEVER use native Claude Code tools ({_forbidden_list}) "
            "in this session. All code reading, searching, editing, and "
            "investigation MUST be delegated through run_skill, which launches "
            "headless sessions with full tool access. Do NOT use native tools to "
            "investigate failures — route to on_failure "
            "and let the downstream skill handle diagnosis."
        )

        # Inject sous-chef global orchestration rules (graceful degradation if absent)
        _sous_chef_path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
        try:
            if _sous_chef_path.exists():
                text += "\n\n" + _sous_chef_path.read_text()
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="read_sous_chef", exc_info=True)
            return _kitchen_failure_envelope(exc, stage="read_sous_chef")

        # Check if the project needs an upgrade
        scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
        recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
        if scripts_dir.exists() and not recipes_dir.exists():
            text += (
                "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated"
                " to the new recipe format.\n"
                "`.autoskillit/scripts/` still exists."
                " Run `autoskillit upgrade` in this directory\n"
                "to migrate automatically, or ask me to do it for you."
            )

        try:
            warning = _build_hook_diagnostic_warning()
        except Exception as exc:
            logger.warning("open_kitchen_failure", stage="hook_diagnostic", exc_info=True)
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
    except Exception as exc:
        logger.error("open_kitchen unhandled exception", exc_info=True)
        return _kitchen_failure_envelope(exc, stage="unhandled")


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": True})
@track_response_size("close_kitchen")
async def close_kitchen(ctx: Context = CurrentContext()) -> str:
    """Close the AutoSkillit kitchen.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("close_kitchen")) is not None:
            return h
        _close_kitchen_handler()
        await ctx.reset_visibility()
        return "Kitchen is closed."
    except Exception as exc:
        logger.error("close_kitchen unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": False})
@track_response_size("disable_quota_guard")
async def disable_quota_guard() -> str:
    """Disable the quota guard for the remainder of this kitchen session.

    The quota guard blocks run_skill calls when API utilization exceeds a
    threshold. Invoke this tool when you decide the work is worth the quota
    spend and want to override the guard for the current session.

    Session-scoped only: the guard re-activates when the kitchen is closed
    and reopened. Does not modify persistent configuration.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("disable_quota_guard")) is not None:
            return h
        hook_cfg_path = _hook_config_path(Path.cwd())
        if not hook_cfg_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "error": "Kitchen is not open — hook config file absent.",
                }
            )
        try:
            payload = json.loads(hook_cfg_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Failed to read hook config: {type(exc).__name__}: {exc}",
                }
            )
        quota_section = payload.get("quota_guard", {})
        quota_section["disabled"] = True
        payload["quota_guard"] = quota_section
        atomic_write(hook_cfg_path, json.dumps(payload))
        return json.dumps(
            {
                "success": True,
                "content": (
                    "Quota guard disabled for this session. "
                    "run_skill calls will no longer be blocked by quota checks."
                ),
            }
        )
    except Exception as exc:
        logger.error("disable_quota_guard unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


def _find_session_id_for_reload(cwd: Path) -> str | None:
    """Return the session_id to use for reload; kitchen marker preferred, mtime fallback."""
    from autoskillit.core import get_state_dir, is_marker_fresh, read_marker

    state_dir = get_state_dir()
    if state_dir.is_dir():

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        candidates = sorted(state_dir.glob("*.json"), key=_safe_mtime, reverse=True)
        for p in candidates:
            marker = read_marker(p.stem)
            if marker is not None and is_marker_fresh(marker):
                return marker.session_id
    return find_latest_session_id(str(cwd))


def _write_reload_sentinel(cwd: Path, session_id: str) -> None:
    """Atomically write a reload sentinel file for session_id."""
    sentinel_path = cwd / ".autoskillit" / "temp" / "reload_sentinel" / f"{session_id}.json"
    payload = json.dumps({"session_id": session_id, "requested_at": datetime.now(UTC).isoformat()})
    atomic_write(sentinel_path, payload)


def _reload_session_handler() -> dict[str, str]:
    """Core logic for the reload_session tool — testable without FastMCP."""
    cwd = Path.cwd()
    session_id = _find_session_id_for_reload(cwd)
    if not session_id:
        raise ValueError(
            "Cannot determine session ID. Ensure open_kitchen was called, "
            "or that a Claude Code session JSONL exists for this project."
        )
    _write_reload_sentinel(cwd, session_id)
    return {
        "status": "reload_requested",
        "session_id": session_id,
        "next_action": (
            "Run /exit now. The parent autoskillit process will re-launch "
            "with --resume and full wrapper environment."
        ),
    }


@mcp.tool(tags={"autoskillit"}, annotations={"readOnlyHint": False})
@track_response_size("reload_session")
async def reload_session() -> dict[str, str]:
    """Signal the parent autoskillit process to reload this session with the full
    wrapper environment intact and resume the conversation.

    After calling this tool, run /exit to allow the parent process to detect the
    reload request and re-launch claude with --resume <session_id>.

    Never raises.
    """
    try:
        return _reload_session_handler()
    except Exception as exc:
        logger.error("reload_session unhandled exception", exc_info=True)
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
