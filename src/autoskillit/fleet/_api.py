"""Fleet dispatch orchestration API."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from autoskillit.core import (
    FleetErrorCode,
    SkillResult,
    claude_code_log_path,
    fleet_error,
    get_logger,
)
from autoskillit.fleet.result_parser import parse_l2_result_block

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
) -> None:
    """on_spawn callback: atomically mark dispatch as running with l2_pid and identity fields."""
    from autoskillit.core import read_boot_id
    from autoskillit.fleet.state import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            l2_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=read_boot_id() or "",
        )
    except Exception:
        logger.warning("_write_pid: failed to mark dispatch running", exc_info=True)


def _post_dispatch_cleanup(
    tool_ctx: ToolContext,
    skill_result: SkillResult,
    cache_invalidator: Callable[[str], None] | None,
    quota_refresher: Callable[..., Any],
) -> None:
    """Run quota cache invalidation, background quota refresh, and session skill cleanup."""
    if cache_invalidator is not None:
        cache_invalidator(tool_ctx.config.quota_guard.cache_path)

    if tool_ctx.background is not None:
        tool_ctx.background.submit(
            quota_refresher(tool_ctx.config.quota_guard),
            label="quota_post_dispatch_refresh",
        )

    if tool_ctx.session_skill_manager is not None and skill_result.session_id:
        try:
            tool_ctx.session_skill_manager.cleanup_session(skill_result.session_id)
        except Exception as exc:
            logger.warning(
                "session skills cleanup failed — dispatch not affected",
                session_id=skill_result.session_id,
                exc_class=type(exc).__name__,
                exc_info=True,
            )


async def execute_dispatch(
    tool_ctx: ToolContext,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    dispatch_name: str | None,
    timeout_sec: int | None,
    prompt_builder: Callable[..., str],
    quota_checker: Callable[..., Any],
    quota_refresher: Callable[..., Any],
    cache_invalidator: Callable[[str], None] | None = None,
) -> str:
    """Execute a single food truck dispatch.

    Orchestrates: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns JSON envelope string.
    """
    if ingredients is not None:
        bad_vals = [k for k, v in ingredients.items() if not isinstance(v, str)]
        if bad_vals:
            return fleet_error(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                f"Ingredient values must be strings. Non-string keys: {bad_vals}",
            )

    lock = tool_ctx.fleet_lock
    if lock is None:
        return fleet_error(
            FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED,
            "Fleet lock not initialized — open_kitchen with fleet mode.",
        )
    if lock.locked():
        return fleet_error(
            FleetErrorCode.FLEET_PARALLEL_REFUSED,
            "A dispatch is already in progress. Only one dispatch at a time.",
        )

    await lock.acquire()
    try:
        return await _run_dispatch(
            tool_ctx=tool_ctx,
            recipe=recipe,
            task=task,
            ingredients=ingredients,
            dispatch_name=dispatch_name,
            timeout_sec=timeout_sec,
            prompt_builder=prompt_builder,
            quota_checker=quota_checker,
            quota_refresher=quota_refresher,
            cache_invalidator=cache_invalidator,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("execute_dispatch failed", exc_info=True)
        return fleet_error(
            FleetErrorCode.FLEET_L2_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        lock.release()


async def _run_dispatch(
    tool_ctx: ToolContext,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    dispatch_name: str | None,
    timeout_sec: int | None,
    prompt_builder: Callable[..., str],
    quota_checker: Callable[..., Any],
    quota_refresher: Callable[..., Any],
    cache_invalidator: Callable[[str], None] | None = None,
) -> str:
    """Inner dispatch body — called after lock acquisition."""
    from autoskillit.fleet.state import (
        DispatchRecord,
        DispatchStatus,
        append_dispatch_record,
        write_initial_state,
    )

    if tool_ctx.recipes is None:
        return fleet_error(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Recipe repository not configured.",
        )

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return fleet_error(
            FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
            f"Recipe '{recipe}' not found.",
        )

    try:
        full_recipe = tool_ctx.recipes.load(recipe_obj.path)
    except Exception as exc:
        logger.warning("load_recipe failed for '%s'", recipe, exc_info=True)
        return fleet_error(
            FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
            f"Recipe '{recipe}' could not be loaded: {exc}",
        )

    if full_recipe.kind != "standard":
        return fleet_error(
            FleetErrorCode.FLEET_INVALID_RECIPE_KIND,
            f"Recipe '{recipe}' has kind '{full_recipe.kind}'. "
            "Only standard recipes can be dispatched as food trucks.",
        )

    effective_ingredients = ingredients or {}
    if effective_ingredients:
        unknown = set(effective_ingredients.keys()) - set(full_recipe.ingredients.keys())
        if unknown:
            return fleet_error(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                f"Unknown ingredient keys: {sorted(unknown)}. "
                f"Valid keys: {sorted(full_recipe.ingredients.keys())}",
            )

    quota_result = await quota_checker(tool_ctx.config.quota_guard)
    if quota_result.get("should_sleep"):
        await asyncio.sleep(quota_result.get("sleep_seconds", 0))

    dispatch_id = str(uuid4())
    completion_marker = f"%%L2_DONE::{dispatch_id[:8]}%%"
    effective_name = dispatch_name or recipe

    campaign_id = tool_ctx.kitchen_id
    prompt = prompt_builder(
        recipe=recipe,
        task=task,
        ingredients=effective_ingredients,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        l2_timeout_sec=timeout_sec or 1800,
    )

    state_path = tool_ctx.temp_dir / "dispatches" / f"{dispatch_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_initial_state(
        state_path,
        campaign_id=campaign_id,
        campaign_name=effective_name,
        manifest_path="",
        dispatches=[DispatchRecord(name=effective_name)],
    )

    if tool_ctx.executor is None:
        return fleet_error(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Executor not configured.",
        )

    started_at = time.time()
    _l2_pid: list[int] = []

    def _on_spawn(pid: int, ticks: int) -> None:
        _l2_pid.append(pid)
        _write_pid(state_path, effective_name, dispatch_id, pid, ticks)

    skill_result = await tool_ctx.executor.dispatch_food_truck(
        orchestrator_prompt=prompt,
        cwd=str(tool_ctx.project_dir),
        completion_marker=completion_marker,
        kitchen_id=tool_ctx.kitchen_id,
        order_id=dispatch_id,
        campaign_id=campaign_id,
        dispatch_id=dispatch_id,
        project_dir=str(tool_ctx.project_dir),
        timeout=float(timeout_sec) if timeout_sec else None,
        env_extras={
            "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
            "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
            "AUTOSKILLIT_DISPATCH_ID": dispatch_id,
        },
        on_spawn=_on_spawn,
    )
    ended_at = time.time()

    # --- Timeout pre-check: short-circuit before result-block parsing ---
    if skill_result.subtype == "timeout":
        append_dispatch_record(
            state_path,
            DispatchRecord(
                name=effective_name,
                status=DispatchStatus.FAILURE,
                dispatch_id=dispatch_id,
                l2_session_id=skill_result.session_id,
                l2_pid=_l2_pid[0] if _l2_pid else 0,
                reason=FleetErrorCode.FLEET_L2_TIMEOUT,
                token_usage=skill_result.token_usage or {},
                started_at=started_at,
                ended_at=ended_at,
            ),
        )
        _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)
        return fleet_error(
            FleetErrorCode.FLEET_L2_TIMEOUT,
            f"L2 dispatch '{effective_name}' timed out",
            details={
                "dispatch_id": dispatch_id,
                "l2_session_id": skill_result.session_id,
                "lifespan_started": skill_result.lifespan_started,
                "token_usage": skill_result.token_usage,
            },
        )

    jsonl_path = claude_code_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")
    parsed = parse_l2_result_block(
        stdout=skill_result.result or "",
        expected_dispatch_id=dispatch_id,
        assistant_messages_path=jsonl_path,
    )

    # Classify outcome → (final_status, reason)
    if parsed.outcome == "completed_clean" and parsed.payload and parsed.payload.get("success"):
        final_status = DispatchStatus.SUCCESS
        reason = ""
    elif parsed.outcome == "completed_clean":
        final_status = DispatchStatus.FAILURE
        reason = parsed.payload.get("reason", "") if parsed.payload else ""
    elif parsed.outcome == "completed_dirty":
        final_status = DispatchStatus.FAILURE
        reason = FleetErrorCode.FLEET_L2_PARSE_FAILED
    else:  # no_sentinel
        final_status = DispatchStatus.FAILURE
        reason = FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK

    append_dispatch_record(
        state_path,
        DispatchRecord(
            name=effective_name,
            status=final_status,
            dispatch_id=dispatch_id,
            l2_session_id=skill_result.session_id,
            l2_pid=_l2_pid[0] if _l2_pid else 0,
            reason=reason,
            token_usage=skill_result.token_usage or {},
            started_at=started_at,
            ended_at=ended_at,
        ),
    )

    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    if parsed.outcome == "completed_clean":
        envelope_success = bool(parsed.payload and parsed.payload.get("success", False))
        return json.dumps(
            {
                "success": envelope_success,
                "dispatch_id": dispatch_id,
                "l2_session_id": skill_result.session_id,
                "l2_payload": parsed.payload,
                "reason": reason,
                "token_usage": skill_result.token_usage,
                "l2_parse_source": parsed.source,
                "lifespan_started": skill_result.lifespan_started,
            }
        )
    elif parsed.outcome == "completed_dirty":
        return json.dumps(
            {
                "success": False,
                "dispatch_id": dispatch_id,
                "l2_session_id": skill_result.session_id,
                "l2_payload": None,
                "reason": FleetErrorCode.FLEET_L2_PARSE_FAILED,
                "l2_raw_body": parsed.raw_body,
                "l2_parse_error": parsed.parse_error,
                "token_usage": skill_result.token_usage,
                "l2_parse_source": parsed.source,
                "lifespan_started": skill_result.lifespan_started,
            }
        )
    else:
        return json.dumps(
            {
                "success": False,
                "dispatch_id": dispatch_id,
                "l2_session_id": skill_result.session_id,
                "l2_payload": None,
                "reason": FleetErrorCode.FLEET_L2_NO_RESULT_BLOCK,
                "l2_parse_source": parsed.source,
                "token_usage": skill_result.token_usage,
                "lifespan_started": skill_result.lifespan_started,
            }
        )
