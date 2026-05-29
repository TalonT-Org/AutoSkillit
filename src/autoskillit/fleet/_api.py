"""Fleet dispatch orchestration API."""

from __future__ import annotations

import asyncio
import functools
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import psutil

from autoskillit.core import (
    CaptureEntrySpec,
    FleetErrorCode,
    ProcessStaleError,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillResult,
    claude_code_log_path,
    claude_code_project_dir,
    get_logger,
    truncate_text,
    write_versioned_json,
)
from autoskillit.fleet._capture import _extract_captures, _normalize_capture_spec
from autoskillit.fleet._expressions import _CAMPAIGN_REF_RE, _interpolate_campaign_refs
from autoskillit.fleet._outcome import _checkpoint_to_dict, classify_dispatch_outcome
from autoskillit.fleet.result_parser import parse_l3_result_block
from autoskillit.fleet.state import DispatchStatus
from autoskillit.fleet.state_types import (
    DispatchCompleted,
    DispatchRejected,
    DispatchResult,
)

if TYPE_CHECKING:
    from autoskillit.fleet.sidecar import IssueSidecarEntry
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)

ENVELOPE_STDERR_MAX = 2000


def resolve_dispatch_timeout(
    timeout_sec: int | None,
    default_timeout_sec: int,
) -> float:
    """Resolve dispatch timeout to a concrete float.

    Single source of truth for all three timeout surfaces (prompt, process kill,
    session deadline). Uses ``is not None`` to correctly handle ``timeout_sec=0``.
    """
    if timeout_sec is not None:
        return float(timeout_sec)
    return float(default_timeout_sec)


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
    sidecar_path: str | None = None,
    dispatched_create_time: float = 0.0,
    identity_degraded: bool = False,
    boot_id: str = "",
) -> None:
    """on_spawn callback: atomically mark dispatch as running with dispatched_pid."""
    from autoskillit.fleet import mark_dispatch_running  # noqa: PLC0415

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            dispatched_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=boot_id,
            dispatched_create_time=dispatched_create_time,
            sidecar_path=sidecar_path,
            identity_degraded=identity_degraded,
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


async def _touch_dispatch_marker(
    marker_path: Path, interval: float = 30.0, trigger: anyio.Event | None = None
) -> None:
    """Periodically touch marker_path to refresh mtime; runs until trigger is set."""
    try:
        marker_path.touch()
    except OSError:
        logger.warning("_touch_dispatch_marker: failed to touch %s", marker_path, exc_info=True)
    while trigger is None or not trigger.is_set():
        await anyio.sleep(interval)
        try:
            marker_path.touch()
        except OSError:
            logger.warning(
                "_touch_dispatch_marker: failed to touch %s", marker_path, exc_info=True
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
    prior_dispatch_id: str | None = None,
    resume_message: str | None = None,
    caller_instructions: str | None = None,
) -> DispatchResult:
    """Execute a single food truck dispatch.

    Orchestrates: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns DispatchResult wrapping the outcome plus the per-dispatch state path.
    """
    effective_name = dispatch_name or recipe

    def _reject(error_code: FleetErrorCode, message: str, **kwargs: Any) -> DispatchResult:
        """Pre-lock, pre-dispatch-id rejection path — no per-dispatch state file exists yet."""
        rejection = DispatchRejected(error_code=error_code, message=message, **kwargs)
        return DispatchResult(rejection, per_dispatch_state_path=None)

    if ingredients is not None:
        bad_vals = [k for k, v in ingredients.items() if not isinstance(v, str)]
        if bad_vals:
            return _reject(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                f"Ingredient values must be strings. Non-string keys: {bad_vals}",
            )

    lock = tool_ctx.fleet_lock
    if lock is None:
        return _reject(
            error_code=FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED,
            message="Fleet lock not initialized — open_kitchen with fleet mode.",
        )
    if lock.at_capacity():
        return _reject(
            error_code=FleetErrorCode.FLEET_PARALLEL_REFUSED,
            message=(
                f"Fleet at capacity ({lock.active_count}/{lock.max_concurrent}"
                " dispatches running)."
            ),
        )

    try:
        await lock.acquire()
    except TimeoutError:
        return _reject(
            error_code=FleetErrorCode.FLEET_ACQUIRE_TIMEOUT,
            message=(
                f"Timed out waiting for fleet semaphore after {lock.timeout}s "
                f"({lock.active_count}/{lock.max_concurrent} dispatches running)."
            ),
        )
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
            capture=_normalize_capture_spec(capture),
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            idle_output_timeout=idle_output_timeout,
            caller_session_id=caller_session_id,
            prior_dispatch_id=prior_dispatch_id,
            resume_message=resume_message,
            caller_instructions=caller_instructions,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Unwrap ExceptionGroup from anyio task-group wrapping
        if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
            underlying = exc.exceptions[0]
        else:
            underlying = exc
        logger.warning(
            "execute_dispatch crashed before dispatch completion",
            exc_type=type(underlying).__name__,
            dispatch_name=effective_name,
            exc_info=True,
        )
        return _reject(
            error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            message=f"{type(underlying).__name__}: {underlying}",
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
    capture: dict[str, CaptureEntrySpec] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    idle_output_timeout: int | None = None,
    caller_session_id: str = "",
    prior_dispatch_id: str | None = None,
    resume_message: str | None = None,
    caller_instructions: str | None = None,
) -> DispatchResult:
    """Inner dispatch body — called after lock acquisition."""
    from autoskillit.fleet.state import (
        DispatchRecord,
        DispatchStateHandle,
        append_dispatch_record,
        normalize_dispatch_token_usage,
        read_state,
        upsert_dispatch_record_by_name,
        write_captured_values,
    )

    if tool_ctx.recipes is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_MANIFEST_MISSING,
                message="Recipe repository not configured.",
            ),
            per_dispatch_state_path=None,
        )

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
                message=f"Recipe '{recipe}' not found.",
            ),
            per_dispatch_state_path=None,
        )

    try:
        validation_result = tool_ctx.recipes.load_and_validate(
            recipe,
            tool_ctx.project_dir,
            suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
            temp_dir=tool_ctx.temp_dir,
        )
    except ProcessStaleError as exc:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_PROCESS_STALE,
                message=str(exc),
            ),
            per_dispatch_state_path=None,
        )
    except Exception as exc:
        logger.warning("load_and_validate failed for '%s'", recipe, exc_info=True)
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' could not be loaded: {exc}",
            ),
            per_dispatch_state_path=None,
        )

    if not validation_result.get("valid", False):
        error_findings = [
            s for s in validation_result.get("suggestions", []) if s.get("severity") == "error"
        ]
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' has validation errors: "
                + "; ".join(f"[{f['rule']}] {f['message']}" for f in error_findings[:3]),
            ),
            per_dispatch_state_path=None,
        )

    try:
        full_recipe = tool_ctx.recipes.load(recipe_obj.path)
    except Exception as exc:
        logger.warning("load_recipe failed for '%s'", recipe, exc_info=True)
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
                message=f"Recipe '{recipe}' could not be loaded: {exc}",
            ),
            per_dispatch_state_path=None,
        )

    _DISPATCHABLE_KINDS = frozenset({"standard", "food-truck"})

    if full_recipe.kind not in _DISPATCHABLE_KINDS:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_INVALID_RECIPE_KIND,
                message=f"Recipe '{recipe}' has kind '{full_recipe.kind}'. "
                "Only standard and food-truck recipes can be dispatched.",
            ),
            per_dispatch_state_path=None,
        )

    effective_ingredients = ingredients or {}
    if "task" in full_recipe.ingredients and "task" not in effective_ingredients:
        effective_ingredients = {"task": task, **effective_ingredients}

    from autoskillit.config import (  # noqa: PLC0415
        apply_config_authoritative_overrides,
    )

    effective_ingredients = apply_config_authoritative_overrides(
        effective_ingredients, full_recipe.ingredients, tool_ctx.project_dir
    )

    effective_name = dispatch_name or recipe
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    campaign_id = tool_ctx.kitchen_id

    prior_session_chain: list[str] = []
    prior_dispatched_session_id = ""
    if resume_session_id and prior_dispatch_id:
        handle = DispatchStateHandle.open_continued(dispatches_dir, prior_dispatch_id)
        try:
            prior_state = read_state(handle.state_path)
            if prior_state:
                for d in prior_state.dispatches:
                    if d.name == effective_name:
                        prior_session_chain = list(d.session_chain)
                        prior_dispatched_session_id = d.dispatched_session_id
                        break
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("failed to read prior session chain from state", exc_info=True)
    else:
        recipe_snapshot = {
            "recipe_name": recipe_obj.name,
            "recipe_path": str(recipe_obj.path),
            "recipe_version": recipe_obj.recipe_version or "",
            "content_hash": recipe_obj.content_hash or "",
            "effective_ingredients": dict(effective_ingredients),
        }
        handle = DispatchStateHandle.create_fresh(
            dispatches_dir,
            campaign_id,
            effective_name,
            "",
            [DispatchRecord(name=effective_name, caller_session_id=caller_session_id)],
            recipe_snapshot,
        )

    identity = handle.identity
    dispatch_id = identity.dispatch_id
    state_path = handle.state_path

    def _reject_with_state(error_code: FleetErrorCode, message: str) -> DispatchResult:
        """Post-dispatch-id rejection path — writes per-dispatch state only.

        Campaign state is written by the caller via _write_dispatch_to_campaign_state.
        """
        rejection = DispatchRejected(
            error_code=error_code, message=message, dispatch_id=dispatch_id
        )
        try:
            append_dispatch_record(
                state_path,
                DispatchRecord.refused(
                    name=effective_name,
                    error_code=error_code,
                    diagnostic_message=message,
                    dispatch_id=dispatch_id,
                ),
            )
        except Exception:
            logger.warning("_reject_with_state: per-dispatch state write failed", exc_info=True)
        return DispatchResult(rejection, per_dispatch_state_path=state_path)

    if effective_ingredients:
        unknown = set(effective_ingredients.keys()) - set(full_recipe.ingredients.keys())
        if unknown:
            return _reject_with_state(
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
        return _reject_with_state(
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
            logger.warning("ingredient interpolation failed", exc_info=True)
            return _reject_with_state(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                str(exc),
            )

    quota_result = await quota_checker(tool_ctx.config.quota_guard)
    if quota_result.get("should_sleep"):
        await asyncio.sleep(quota_result.get("sleep_seconds", 0))

    if resume_session_id:
        _primary_jsonl = claude_code_log_path(str(tool_ctx.project_dir), resume_session_id)
        if _primary_jsonl is None or not _primary_jsonl.exists():
            logger.warning(
                "resume_jsonl_missing",
                resume_session_id=resume_session_id,
                expected_path=str(_primary_jsonl) if _primary_jsonl else "none",
            )
            _fallback_session_id = prior_session_chain[-1] if prior_session_chain else ""
            if _fallback_session_id:
                _fallback_jsonl = claude_code_log_path(
                    str(tool_ctx.project_dir), _fallback_session_id
                )
                if _fallback_jsonl is not None and _fallback_jsonl.exists():
                    logger.info(
                        "resume_session_fallback",
                        original_session_id=resume_session_id,
                        fallback_session_id=_fallback_session_id,
                    )
                    resume_session_id = _fallback_session_id
                else:
                    return _reject_with_state(
                        FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                        f"JSONL log for session {resume_session_id} not found",
                    )
            else:
                return _reject_with_state(
                    FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                    f"JSONL log for session {resume_session_id} not found",
                )

    completion_marker = identity.completion_marker
    sentinel_contract = identity.sentinel_contract
    from autoskillit.fleet.sidecar import sidecar_path as compute_sidecar_path  # noqa: PLC0415

    dispatch_sidecar_path = str(compute_sidecar_path(dispatch_id, tool_ctx.project_dir))

    resolved_timeout = resolve_dispatch_timeout(
        timeout_sec, tool_ctx.config.fleet.default_timeout_sec
    )
    prompt = prompt_builder(
        recipe=recipe,
        task=task,
        ingredients=effective_ingredients,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        l3_timeout_sec=int(resolved_timeout),
        capture=capture,
        caller_instructions=caller_instructions,
    )

    if tool_ctx.executor is None:
        return _reject_with_state(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Executor not configured.",
        )

    started_at = time.time()
    _dispatched_pid: list[int] = []
    _dispatched_ticks: list[int] = []
    _dispatched_create_time: list[float] = []
    _dispatched_boot_id: list[str] = []

    # Collect prior dispatch_ids from attempt_history for defense-in-depth parsing
    prior_ids: list[str] = []
    try:
        state = read_state(state_path)
        if state:
            for d in state.dispatches:
                if d.name == effective_name:
                    for attempt in d.attempt_history:
                        aid = attempt.get("dispatch_id", "")
                        if aid and aid != dispatch_id:
                            prior_ids.append(aid)
    except Exception:
        logger.warning(
            "failed to collect prior dispatch_ids from state",
            state_path=str(state_path),
            exc_info=True,
        )

    prior_completion_markers = (
        [f"%%L3_DONE::{pid[:8]}%%" for pid in prior_ids] if prior_ids else None
    )

    def _on_spawn(pid: int, ticks: int) -> None:
        from autoskillit.core import read_boot_id  # noqa: PLC0415

        _dispatched_pid.append(pid)
        try:
            create_time = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            create_time = 0.0
        boot_id = read_boot_id() or ""
        _dispatched_ticks.append(ticks)
        _dispatched_create_time.append(create_time)
        _dispatched_boot_id.append(boot_id)
        _write_pid(
            state_path,
            effective_name,
            dispatch_id,
            pid,
            ticks,
            dispatch_sidecar_path,
            create_time,
            identity_degraded=(ticks == 0 or not boot_id),
            boot_id=boot_id,
        )

    marker_dir: Path | None = None
    marker_path: Path | None = None
    try:
        marker_dir = claude_code_project_dir(str(tool_ctx.project_dir))
    except OSError:
        pass

    if marker_dir is not None:
        marker_path = marker_dir / f"dispatch-in-progress-{caller_session_id}-{dispatch_id}.marker"
        try:
            write_versioned_json(
                marker_path,
                {
                    "dispatch_id": dispatch_id,
                    "orchestrator_pid": os.getpid(),
                    "session_id": caller_session_id,
                },
                schema_version=1,
            )
        except OSError:
            logger.warning("dispatch_marker_write_failed", marker=str(marker_path), exc_info=True)
            marker_dir = None
            marker_path = None

    _dispatch_completed_normally = False
    _hb_trigger = anyio.Event()
    try:
        async with anyio.create_task_group() as tg:
            if marker_path is not None:
                tg.start_soon(
                    functools.partial(_touch_dispatch_marker, marker_path, trigger=_hb_trigger)
                )
            try:
                skill_result = await tool_ctx.executor.dispatch_food_truck(
                    orchestrator_prompt=prompt,
                    cwd=str(tool_ctx.project_dir),
                    completion_marker=completion_marker,
                    prior_completion_markers=prior_completion_markers,
                    resume_session_id=resume_session_id,
                    resume_checkpoint=resume_checkpoint,
                    kitchen_id=tool_ctx.kitchen_id,
                    order_id=dispatch_id,
                    campaign_id=campaign_id,
                    dispatch_id=dispatch_id,
                    caller_session_id=caller_session_id,
                    project_dir=str(tool_ctx.project_dir),
                    marker_dir=marker_dir,
                    session_id=caller_session_id,
                    timeout=resolved_timeout,
                    idle_output_timeout=float(idle_output_timeout)
                    if idle_output_timeout is not None
                    else None,
                    env_extras={
                        "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
                        "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
                        "AUTOSKILLIT_DISPATCH_ID": dispatch_id,
                        "AUTOSKILLIT_SESSION_DEADLINE": str(started_at + resolved_timeout),
                    },
                    requires_packs=list(full_recipe.requires_packs) or ["kitchen-core"],
                    on_spawn=_on_spawn,
                    sentinel_contract=sentinel_contract,
                    resume_message=resume_message,
                )
            finally:
                _hb_trigger.set()
                tg.cancel_scope.cancel()

        ended_at = time.time()
        _dispatch_completed_normally = True
    except asyncio.CancelledError:
        if _dispatched_pid:
            try:
                from autoskillit.fleet.state import mark_dispatch_interrupted  # noqa: PLC0415

                with anyio.CancelScope(shield=True):
                    mark_dispatch_interrupted(
                        state_path,
                        effective_name,
                        reason="signal_induced_cancellation",
                    )
            except Exception:
                logger.warning(
                    "failed to record interrupted state on cancel",
                    dispatch_name=effective_name,
                    exc_info=True,
                )
        raise
    finally:
        if marker_path is not None:
            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "dispatch_marker_unlink_failed", marker=str(marker_path), exc_info=True
                )
        if not _dispatch_completed_normally:
            from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

            with anyio.CancelScope(shield=True):
                await cleanup_orphaned_labels(dispatch_sidecar_path, tool_ctx.github_client)

    sidecar_file = Path(dispatch_sidecar_path)
    sidecar_entries: list[IssueSidecarEntry] = []
    dispatch_checkpoint: SessionCheckpoint | None = None
    if sidecar_file.exists():
        from autoskillit.fleet._checkpoint_bridge import checkpoint_from_sidecar  # noqa: PLC0415
        from autoskillit.fleet.sidecar import read_sidecar_from_path  # noqa: PLC0415

        sidecar_entries = read_sidecar_from_path(sidecar_file).entries
        if sidecar_entries:
            dispatch_checkpoint = checkpoint_from_sidecar(sidecar_entries)

    extended_chain = prior_session_chain[:]
    additional_jsonl_paths: list[Path] = []
    if skill_result.subtype == "timeout":
        parsed_result = None
    else:
        if prior_dispatched_session_id and prior_dispatched_session_id not in extended_chain:
            extended_chain.append(prior_dispatched_session_id)

        for sid in extended_chain:
            path = claude_code_log_path(str(tool_ctx.project_dir), sid)
            if path is not None:
                additional_jsonl_paths.append(path)

        jsonl_path = claude_code_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")
        parsed_result = parse_l3_result_block(
            stdout=skill_result.result or "",
            expected_dispatch_id=dispatch_id,
            assistant_messages_path=jsonl_path,
            prior_dispatch_ids=prior_ids or None,
            additional_jsonl_paths=additional_jsonl_paths or None,
        )

    _issue_urls_raw = effective_ingredients.get("issue_urls", "") if effective_ingredients else ""
    _dispatched_issue_list = [u.strip() for u in _issue_urls_raw.split(",") if u.strip()]
    dispatched_issue_count = len(_dispatched_issue_list)
    if parsed_result is not None and parsed_result.outcome == "no_sentinel" and sidecar_entries:
        from autoskillit.fleet._sidecar_synthesis import synthesize_from_sidecar  # noqa: PLC0415

        parsed_result = synthesize_from_sidecar(
            parsed_result,
            sidecar_entries,
            dispatched_issue_count=dispatched_issue_count,
        )

    final_status, reason = classify_dispatch_outcome(
        parsed_result,
        skill_result,
        sidecar_exists=sidecar_file.exists(),
        checkpoint=dispatch_checkpoint,
        subtype=skill_result.subtype,
    )

    _labels_cleaned = False
    if final_status not in (DispatchStatus.SUCCESS, DispatchStatus.RESUMABLE):
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

        _labels_cleaned = await cleanup_orphaned_labels(
            dispatch_sidecar_path, tool_ctx.github_client
        )

    try:
        project_log_dir = str(claude_code_project_dir(str(tool_ctx.project_dir)))
    except OSError:
        logger.warning("failed to resolve project log dir", exc_info=True)
        project_log_dir = ""

    if (
        resume_session_id
        and skill_result.session_id
        and resume_session_id != skill_result.session_id
    ):
        logger.warning(
            "session_id_continuity_mismatch",
            resume_session_id=resume_session_id,
            returned_session_id=skill_result.session_id,
        )

    record = DispatchRecord(
        name=effective_name,
        status=final_status,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        caller_session_id=caller_session_id,
        dispatched_session_id=skill_result.session_id,
        session_chain=extended_chain,
        dispatched_session_log_dir=project_log_dir,
        dispatched_pid=_dispatched_pid[0] if _dispatched_pid else 0,
        dispatched_starttime_ticks=_dispatched_ticks[0] if _dispatched_ticks else 0,
        dispatched_boot_id=_dispatched_boot_id[0] if _dispatched_boot_id else "",
        dispatched_create_time=_dispatched_create_time[0] if _dispatched_create_time else 0.0,
        reason=reason,
        kill_reason=skill_result.retry_reason or "",
        infra_exit_category=skill_result.infra.exit_category or "",
        token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
        started_at=started_at,
        ended_at=ended_at,
        sidecar_path=dispatch_sidecar_path,
        labels_cleaned=_labels_cleaned,
        resume_checkpoint=_checkpoint_to_dict(dispatch_checkpoint),
    )

    extracted: dict[str, str] = {}
    if (
        final_status == DispatchStatus.SUCCESS
        and capture
        and parsed_result is not None
        and parsed_result.payload
        and parsed_result.source != "sidecar"
    ):
        extracted = _extract_captures(capture, parsed_result.payload)

    upsert_dispatch_record_by_name(state_path, record)
    if extracted:
        write_captured_values(state_path, extracted)
    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    if parsed_result is not None and parsed_result.outcome == "completed_clean":
        envelope_success = bool(
            parsed_result.payload and parsed_result.payload.get("success", False)
        )
        return DispatchResult(
            DispatchCompleted(
                success=envelope_success,
                dispatch_status=final_status,
                dispatch_id=dispatch_id,
                dispatched_session_id=skill_result.session_id or "",
                reason=reason,
                token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
                l3_payload=parsed_result.payload,
                l3_parse_source=parsed_result.source,
                lifespan_started=skill_result.lifespan_started,
                stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
                elapsed_seconds=ended_at - started_at,
            ),
            per_dispatch_state_path=state_path,
        )
    elif parsed_result is not None and parsed_result.outcome == "completed_dirty":
        return DispatchResult(
            DispatchCompleted(
                success=False,
                dispatch_status=final_status,
                dispatch_id=dispatch_id,
                dispatched_session_id=skill_result.session_id or "",
                reason=FleetErrorCode.FLEET_L3_PARSE_FAILED,
                token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
                l3_payload=None,
                l3_raw_body=parsed_result.raw_body,
                l3_parse_error=parsed_result.parse_error,
                l3_parse_source=parsed_result.source,
                lifespan_started=skill_result.lifespan_started,
                stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
                elapsed_seconds=ended_at - started_at,
            ),
            per_dispatch_state_path=state_path,
        )
    else:
        parse_source = parsed_result.source if parsed_result is not None else "stdout"
        return DispatchResult(
            DispatchCompleted(
                success=False,
                dispatch_status=final_status,
                dispatch_id=dispatch_id,
                dispatched_session_id=skill_result.session_id or "",
                reason=reason,
                token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
                l3_payload=None,
                l3_parse_source=parse_source,
                lifespan_started=skill_result.lifespan_started,
                resume_checkpoint=_checkpoint_to_dict(dispatch_checkpoint),
                stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
                elapsed_seconds=ended_at - started_at,
            ),
            per_dispatch_state_path=state_path,
        )
