"""Franchise dispatch orchestration API."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import FranchiseErrorCode, get_logger
from autoskillit.pipeline.gate import franchise_error, gate_error_result

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


def _write_pid(state_path: Path, dispatch_name: str, dispatch_id: str, pid: int) -> None:
    """on_spawn callback: atomically mark dispatch as running with l2_pid."""
    from autoskillit.franchise.state import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            l2_pid=pid,
        )
    except Exception:
        logger.warning("_write_pid: failed to mark dispatch running", exc_info=True)


async def execute_dispatch(
    tool_ctx: ToolContext,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    dispatch_name: str | None,
    timeout_sec: int | None,
    prompt_builder: Callable[..., str],
) -> str:
    """Execute a single food truck dispatch.

    Orchestrates: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns JSON envelope string.
    """
    if ingredients is not None:
        bad_vals = [k for k, v in ingredients.items() if not isinstance(v, str)]
        if bad_vals:
            return franchise_error(
                FranchiseErrorCode.FRANCHISE_UNKNOWN_INGREDIENT,
                f"Ingredient values must be strings. Non-string keys: {bad_vals}",
            )

    lock = tool_ctx.franchise_lock
    if lock is None:
        return gate_error_result(
            "Franchise lock not initialized — open_kitchen with franchise mode."
        )
    if lock.locked():
        return franchise_error(
            FranchiseErrorCode.FRANCHISE_PARALLEL_REFUSED,
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
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("execute_dispatch failed", exc_info=True)
        return franchise_error(
            FranchiseErrorCode.L2_STARTUP_OR_CRASH,
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
) -> str:
    """Inner dispatch body — called after lock acquisition."""
    from autoskillit.franchise.state import (
        DispatchRecord,
        DispatchStatus,
        append_dispatch_record,
        write_initial_state,
    )

    _execution = sys.modules["autoskillit.execution"]
    check_and_sleep_if_needed = _execution.check_and_sleep_if_needed
    _refresh_quota_cache = _execution._refresh_quota_cache

    if tool_ctx.recipes is None:
        return gate_error_result("Recipe repository not configured.")

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return franchise_error(
            FranchiseErrorCode.FRANCHISE_RECIPE_NOT_FOUND,
            f"Recipe '{recipe}' not found.",
        )
    if recipe_obj.kind != "standard":
        return franchise_error(
            FranchiseErrorCode.FRANCHISE_RECIPE_NOT_FOUND,
            f"Recipe '{recipe}' has kind '{recipe_obj.kind}'. "
            "Only standard recipes can be dispatched as food trucks.",
        )

    effective_ingredients = ingredients or {}
    if effective_ingredients:
        unknown = set(effective_ingredients.keys()) - set(recipe_obj.ingredients.keys())
        if unknown:
            return franchise_error(
                FranchiseErrorCode.FRANCHISE_UNKNOWN_INGREDIENT,
                f"Unknown ingredient keys: {sorted(unknown)}. "
                f"Valid keys: {sorted(recipe_obj.ingredients.keys())}",
            )

    quota_result = await check_and_sleep_if_needed(tool_ctx.config.quota_guard)
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
        return gate_error_result("Executor not configured.")

    started_at = time.time()
    skill_result = await tool_ctx.executor.dispatch_food_truck(
        orchestrator_prompt=prompt,
        cwd=str(tool_ctx.project_dir),
        completion_marker=completion_marker,
        kitchen_id=tool_ctx.kitchen_id,
        order_id=dispatch_id,
        timeout=float(timeout_sec) if timeout_sec else None,
        env_extras={
            "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
            "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
        },
        on_spawn=lambda pid: _write_pid(state_path, effective_name, dispatch_id, pid),
    )
    ended_at = time.time()

    final_status = DispatchStatus.SUCCESS if skill_result.success else DispatchStatus.FAILURE
    append_dispatch_record(
        state_path,
        DispatchRecord(
            name=effective_name,
            status=final_status,
            dispatch_id=dispatch_id,
            l2_session_id=skill_result.session_id,
            token_usage=skill_result.token_usage or {},
            started_at=started_at,
            ended_at=ended_at,
        ),
    )

    if tool_ctx.background is not None:
        tool_ctx.background.submit(
            _refresh_quota_cache(tool_ctx.config.quota_guard),
            label="quota_post_dispatch_refresh",
        )

    if tool_ctx.session_skill_manager is not None and skill_result.session_id:
        tool_ctx.session_skill_manager.cleanup_session(skill_result.session_id)

    return json.dumps(
        {
            "success": skill_result.success,
            "dispatch_id": dispatch_id,
            "l2_session_id": skill_result.session_id,
            "l2_payload": skill_result.result,
            "token_usage": skill_result.token_usage,
        }
    )
