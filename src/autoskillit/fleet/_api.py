"""Fleet dispatch orchestration API."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from autoskillit.core import (
    FleetErrorCode,
    InfraExitCategory,
    RetryReason,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillResult,
    claude_code_log_path,
    fleet_error,
    get_logger,
)
from autoskillit.fleet.result_parser import L3ParseResult, parse_l3_result_block
from autoskillit.fleet.state import DispatchStatus

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)

_CAMPAIGN_REF_RE = re.compile(r"\$\{\{\s*campaign\.(\w+)\s*\}\}")
_RESULT_REF_RE = re.compile(r"^\$\{\{\s*result\.([\w-]+)\s*\}\}$")


def _extract_captures(
    capture_spec: dict[str, str],
    payload: dict[str, object],
) -> dict[str, str]:
    """Extract captured values from an L3 result payload.

    For each entry in `capture_spec` whose value matches ``${{ result.field }}``,
    reads `payload[field]` and converts it to str. Missing payload keys are skipped.
    """
    result: dict[str, str] = {}
    for key, template in capture_spec.items():
        m = _RESULT_REF_RE.match(template.strip())
        if m is None:
            continue
        field_name = m.group(1)
        if field_name in payload:
            value = payload[field_name]
            result[key] = value if isinstance(value, str) else json.dumps(value, default=str)
    return result


def _interpolate_campaign_refs(
    ingredients: dict[str, str],
    captured: dict[str, str],
) -> dict[str, str]:
    """Resolve ``${{ campaign.key }}`` references in ingredient values.

    Raises ValueError if a campaign reference cannot be resolved.
    Non-campaign values are returned unchanged.
    """
    out: dict[str, str] = {}
    for k, v in ingredients.items():

        def _replace(m: re.Match, _k: str = k) -> str:
            ref = m.group(1)
            if ref not in captured:
                raise ValueError(
                    f"Ingredient '{_k}' references ${{{{ campaign.{ref} }}}} "
                    f"but '{ref}' has not been captured by any prior dispatch. "
                    f"Available: {sorted(captured)}"
                )
            return captured[ref]

        out[k] = _CAMPAIGN_REF_RE.sub(_replace, v)
    return out


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
    sidecar_path: str | None = None,
) -> None:
    """on_spawn callback: atomically mark dispatch as running with l3_pid and identity fields."""
    from autoskillit.core import read_boot_id
    from autoskillit.fleet import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            l3_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=read_boot_id() or "",
            sidecar_path=sidecar_path,
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
    capture: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    idle_output_timeout: int | None = None,
    caller_session_id: str = "",
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
    if lock.at_capacity():
        return fleet_error(
            FleetErrorCode.FLEET_PARALLEL_REFUSED,
            f"Fleet at capacity ({lock.active_count}/{lock.max_concurrent} dispatches running).",
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
            capture=capture,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            idle_output_timeout=idle_output_timeout,
            caller_session_id=caller_session_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("execute_dispatch failed", exc_info=True)
        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        lock.release()


_ABANDON_REASONS: frozenset[str] = frozenset(
    {
        RetryReason.STALE,
        RetryReason.THINKING_STALL,
        RetryReason.PATH_CONTAMINATION,
        RetryReason.CLONE_CONTAMINATION,
    }
)


def _is_abandon_reason(skill_result: SkillResult) -> bool:
    """Return True when the kill reason indicates resume would be futile."""
    if skill_result.retry_reason in _ABANDON_REASONS:
        return True
    if (
        skill_result.retry_reason == RetryReason.RESUME
        and skill_result.infra_exit_category == InfraExitCategory.CONTEXT_EXHAUSTED
    ):
        return True
    return False


def classify_dispatch_outcome(
    parsed: L3ParseResult,
    skill_result: SkillResult,
    *,
    sidecar_exists: bool = False,
    checkpoint: SessionCheckpoint | None = None,
) -> tuple[DispatchStatus, str]:
    """Map L2 food truck subprocess signals to a (DispatchStatus, reason) pair.

    Pure function — no filesystem access, no side effects.
    Rules applied in order:
      1. completed_clean + success flag → SUCCESS
      2. completed_clean + no success → FAILURE
      3. completed_dirty → FAILURE (fleet_l3_parse_failed)
      4. no_sentinel + session_id + lifespan_started + (checkpoint or sidecar) → RESUMABLE
      5. no_sentinel (any other case) → FAILURE (fleet_l3_no_result_block)
    """
    if parsed.outcome == "completed_clean" and parsed.payload and parsed.payload.get("success"):
        return DispatchStatus.SUCCESS, ""
    if parsed.outcome == "completed_clean":
        reason = parsed.payload.get("reason", "") if parsed.payload else ""
        return DispatchStatus.FAILURE, reason
    if parsed.outcome == "completed_dirty":
        return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_PARSE_FAILED
    has_progress = checkpoint is not None or sidecar_exists
    if skill_result.session_id and skill_result.lifespan_started and has_progress:
        if _is_abandon_reason(skill_result):
            return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
        return DispatchStatus.RESUMABLE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK
    return DispatchStatus.FAILURE, FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK


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
    capture: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    idle_output_timeout: int | None = None,
    caller_session_id: str = "",
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

    missing_required = [
        key
        for key, ing in full_recipe.ingredients.items()
        if getattr(ing, "required", False)
        and getattr(ing, "default", None) is None
        and key not in effective_ingredients
    ]
    if missing_required:
        return fleet_error(
            FleetErrorCode.FLEET_MISSING_INGREDIENT,
            f"Missing required ingredients: {sorted(missing_required)}. "
            f"These have no default and must be supplied.",
        )

    from autoskillit.fleet.state import read_all_campaign_captures  # noqa: PLC0415

    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    accumulated_captures = read_all_campaign_captures(dispatches_dir, tool_ctx.kitchen_id)

    _has_campaign_refs = any(_CAMPAIGN_REF_RE.search(v) for v in effective_ingredients.values())
    if _has_campaign_refs:
        try:
            effective_ingredients = _interpolate_campaign_refs(
                effective_ingredients, accumulated_captures
            )
        except ValueError as exc:
            return fleet_error(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                str(exc),
            )

    quota_result = await quota_checker(tool_ctx.config.quota_guard)
    if quota_result.get("should_sleep"):
        await asyncio.sleep(quota_result.get("sleep_seconds", 0))

    dispatch_id = str(uuid4())
    completion_marker = f"%%L3_DONE::{dispatch_id[:8]}%%"
    from autoskillit.fleet.sidecar import sidecar_path as compute_sidecar_path  # noqa: PLC0415

    dispatch_sidecar_path = str(compute_sidecar_path(dispatch_id, tool_ctx.project_dir))
    effective_name = dispatch_name or recipe

    campaign_id = tool_ctx.kitchen_id
    prompt = prompt_builder(
        recipe=recipe,
        task=task,
        ingredients=effective_ingredients,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        l3_timeout_sec=timeout_sec or 1800,
    )

    state_path = tool_ctx.temp_dir / "dispatches" / f"{dispatch_id}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_initial_state(
        state_path,
        campaign_id=campaign_id,
        campaign_name=effective_name,
        manifest_path="",
        dispatches=[DispatchRecord(name=effective_name, caller_session_id=caller_session_id)],
    )

    if tool_ctx.executor is None:
        return fleet_error(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Executor not configured.",
        )

    started_at = time.time()
    _l3_pid: list[int] = []

    def _on_spawn(pid: int, ticks: int) -> None:
        _l3_pid.append(pid)
        _write_pid(state_path, effective_name, dispatch_id, pid, ticks, dispatch_sidecar_path)

    skill_result = await tool_ctx.executor.dispatch_food_truck(
        orchestrator_prompt=prompt,
        cwd=str(tool_ctx.project_dir),
        completion_marker=completion_marker,
        resume_session_id=resume_session_id,
        resume_checkpoint=resume_checkpoint,
        kitchen_id=tool_ctx.kitchen_id,
        order_id=dispatch_id,
        campaign_id=campaign_id,
        dispatch_id=dispatch_id,
        caller_session_id=caller_session_id,
        project_dir=str(tool_ctx.project_dir),
        timeout=float(timeout_sec) if timeout_sec else None,
        idle_output_timeout=float(idle_output_timeout)
        if idle_output_timeout is not None
        else None,
        env_extras={
            "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
            "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
            "AUTOSKILLIT_DISPATCH_ID": dispatch_id,
        },
        requires_packs=list(full_recipe.requires_packs) or ["kitchen-core"],
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
                caller_session_id=caller_session_id,
                l3_session_id=skill_result.session_id,
                l3_pid=_l3_pid[0] if _l3_pid else 0,
                reason=FleetErrorCode.FLEET_L3_TIMEOUT,
                kill_reason=skill_result.retry_reason or "",
                infra_exit_category=skill_result.infra_exit_category or "",
                token_usage=skill_result.token_usage or {},
                started_at=started_at,
                ended_at=ended_at,
            ),
        )
        _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)
        return fleet_error(
            FleetErrorCode.FLEET_L3_TIMEOUT,
            f"L3 dispatch '{effective_name}' timed out",
            details={
                "dispatch_id": dispatch_id,
                "l3_session_id": skill_result.session_id,
                "lifespan_started": skill_result.lifespan_started,
                "token_usage": skill_result.token_usage,
            },
        )

    jsonl_path = claude_code_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")
    parsed = parse_l3_result_block(
        stdout=skill_result.result or "",
        expected_dispatch_id=dispatch_id,
        assistant_messages_path=jsonl_path,
    )

    sidecar_file = Path(dispatch_sidecar_path)
    dispatch_checkpoint: SessionCheckpoint | None = None
    if sidecar_file.exists():
        from autoskillit.fleet._checkpoint_bridge import checkpoint_from_sidecar  # noqa: PLC0415
        from autoskillit.fleet.sidecar import read_sidecar_from_path  # noqa: PLC0415

        sidecar_entries = read_sidecar_from_path(sidecar_file)
        if sidecar_entries:
            dispatch_checkpoint = checkpoint_from_sidecar(sidecar_entries)

    final_status, reason = classify_dispatch_outcome(
        parsed,
        skill_result,
        sidecar_exists=sidecar_file.exists(),
        checkpoint=dispatch_checkpoint,
    )

    append_dispatch_record(
        state_path,
        DispatchRecord(
            name=effective_name,
            status=final_status,
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
            l3_session_id=skill_result.session_id,
            l3_pid=_l3_pid[0] if _l3_pid else 0,
            reason=reason,
            kill_reason=skill_result.retry_reason or "",
            infra_exit_category=skill_result.infra_exit_category or "",
            token_usage=skill_result.token_usage or {},
            started_at=started_at,
            ended_at=ended_at,
        ),
    )

    if final_status == DispatchStatus.SUCCESS and capture and parsed.payload:
        from autoskillit.fleet.state import write_captured_values  # noqa: PLC0415

        extracted = _extract_captures(capture, parsed.payload)
        if extracted:
            write_captured_values(state_path, extracted)

    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    if parsed.outcome == "completed_clean":
        envelope_success = bool(parsed.payload and parsed.payload.get("success", False))
        return json.dumps(
            {
                "success": envelope_success,
                "dispatch_status": final_status.value,
                "dispatch_id": dispatch_id,
                "l3_session_id": skill_result.session_id,
                "l3_payload": parsed.payload,
                "reason": reason,
                "token_usage": skill_result.token_usage,
                "l3_parse_source": parsed.source,
                "lifespan_started": skill_result.lifespan_started,
            }
        )
    elif parsed.outcome == "completed_dirty":
        return json.dumps(
            {
                "success": False,
                "dispatch_status": final_status.value,
                "dispatch_id": dispatch_id,
                "l3_session_id": skill_result.session_id,
                "l3_payload": None,
                "reason": FleetErrorCode.FLEET_L3_PARSE_FAILED,
                "l3_raw_body": parsed.raw_body,
                "l3_parse_error": parsed.parse_error,
                "token_usage": skill_result.token_usage,
                "l3_parse_source": parsed.source,
                "lifespan_started": skill_result.lifespan_started,
            }
        )
    else:
        return json.dumps(
            {
                "success": False,
                "dispatch_status": final_status.value,
                "dispatch_id": dispatch_id,
                "l3_session_id": skill_result.session_id,
                "l3_payload": None,
                "reason": FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
                "l3_parse_source": parsed.source,
                "token_usage": skill_result.token_usage,
                "lifespan_started": skill_result.lifespan_started,
                "resume_checkpoint": dispatch_checkpoint.to_dict()
                if dispatch_checkpoint
                else None,
            }
        )
