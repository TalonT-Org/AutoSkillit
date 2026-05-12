"""Fleet dispatch orchestration API."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import psutil
import regex as re

from autoskillit.core import (
    CaptureEntrySpec,
    CaptureValueTypeError,
    FleetErrorCode,
    InfraExitCategory,
    RetryReason,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillResult,
    claude_code_log_path,
    claude_code_project_dir,
    get_logger,
    resolve_payload_field,
    truncate_text,
    write_versioned_json,
)
from autoskillit.fleet.result_parser import L3ParseResult, parse_l3_result_block
from autoskillit.fleet.state import DispatchStatus
from autoskillit.fleet.state_types import (
    _ABANDON_REASONS,
    DispatchCompleted,
    DispatchOutcome,
    DispatchRejected,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)

ENVELOPE_STDERR_MAX = 2000


class CaptureCompletenessError(RuntimeError):
    """Raised when a capture spec extracts zero fields from the payload."""


_CAMPAIGN_REF_RE = re.compile(r"\$\{\{\s*campaign\.(\w+)\s*\}\}")


def _validate_capture_value(key: str, value: str, declared_type: str) -> None:
    """Validate a captured value against its declared type.

    Raises CaptureValueTypeError if validation fails.
    """
    if declared_type == "path":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="path value must be non-empty",
            )
        if not Path(value).exists():
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason=f"path does not exist: {value}",
            )
    elif declared_type == "string":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="string value must be non-empty",
            )
    elif declared_type == "url":
        if not value:
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason="url value must be non-empty",
            )
        if not (
            value.startswith("http://")
            or value.startswith("https://")
            or value.startswith("file://")
        ):
            raise CaptureValueTypeError(
                key=key,
                value=value,
                declared_type=declared_type,
                reason=f"url value must start with http://, https://, or file://: {value!r}",
            )
    # optional_string: any value including empty string — no validation


def _extract_captures(
    capture_spec: dict[str, CaptureEntrySpec],
    payload: dict[str, object],
) -> dict[str, str]:
    """Extract captured values from an L3 result payload.

    For each entry in `capture_spec`, reads `payload[field_name]` from the
    ``from_`` template and validates it against the declared `value_type`.
    Missing payload keys are logged as warnings. If the capture spec has
    entries but all fields are absent from the payload, raises
    CaptureCompletenessError. If a value fails type validation,
    raises CaptureValueTypeError.
    """
    result: dict[str, str] = {}
    expected_fields: list[str] = []
    for key, entry in capture_spec.items():
        field_name = resolve_payload_field(entry)
        if field_name is None:
            continue
        expected_fields.append(field_name)
        if field_name in payload:
            value: object = payload[field_name]
            if not isinstance(value, str) and entry.value_type == "path":
                raise CaptureValueTypeError(
                    key=key,
                    value=repr(value),
                    declared_type=entry.value_type,
                    reason=f"expected a string path, got {type(value).__name__}",
                )
            str_value = value if isinstance(value, str) else json.dumps(value, default=str)
            _validate_capture_value(key, str_value, entry.value_type)
            result[key] = str_value
        else:
            logger.warning(
                "capture_field_missing_from_payload",
                capture_name=key,
                expected_field=field_name,
                available_fields=sorted(str(k) for k in payload.keys()),
            )
    if expected_fields and not result:
        raise CaptureCompletenessError(
            f"Capture spec expected fields {expected_fields} but none were "
            f"present in payload. Available: {sorted(str(k) for k in payload.keys())}. "
            f"This indicates a sentinel/capture misalignment."
        )
    return result


def _interpolate_campaign_refs(
    ingredients: dict[str, str],
    captured: dict[str, str],
) -> dict[str, str]:
    """Resolve ``${{ campaign.key }}`` references in ingredient values.

    Raises ValueError if a campaign reference cannot be resolved or resolves to
    an empty string (which may indicate an invalid capture from a prior dispatch).
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
            resolved = captured[ref]
            if resolved == "":
                raise ValueError(
                    f"Ingredient '{_k}' campaign ref '{ref}' resolved to empty string — "
                    f"the capturing dispatch may have emitted an empty value"
                )
            return resolved

        out[k] = _CAMPAIGN_REF_RE.sub(_replace, v)
    return out


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
    sidecar_path: str | None = None,
    dispatched_create_time: float = 0.0,
) -> None:
    """on_spawn callback: atomically mark dispatch as running with dispatched_pid."""
    from autoskillit.core import read_boot_id
    from autoskillit.fleet import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            dispatched_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=read_boot_id() or "",
            dispatched_create_time=dispatched_create_time,
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


def _write_campaign_refusal(
    campaign_state_path: Path | None,
    effective_name: str,
    error_code: FleetErrorCode,
) -> None:
    """Write a REFUSED dispatch record to the campaign state file."""
    if campaign_state_path is None:
        return
    from autoskillit.fleet.state import (  # noqa: PLC0415
        DispatchRecord,
        DispatchStatus,
        upsert_dispatch_record_by_name,
    )

    try:
        upsert_dispatch_record_by_name(
            campaign_state_path,
            DispatchRecord(name=effective_name, status=DispatchStatus.REFUSED, reason=error_code),
        )
    except Exception:
        logger.warning("failed to record refusal to campaign state", exc_info=True)


def _normalize_capture_spec(
    capture: Mapping[str, str | CaptureEntrySpec] | None,
) -> dict[str, CaptureEntrySpec] | None:
    """Convert YAML-format ``dict[str, str]`` capture spec to ``dict[str, CaptureEntrySpec]``.

    The recipe YAML uses shorthand capture entries: ``{key: "${{ result.field }}"}``.
    This converts them to the typed ``CaptureEntrySpec`` format used internally.
    Already-typed ``CaptureEntrySpec`` values are passed through unchanged.
    """
    if capture is None:
        return None
    return {
        key: val if isinstance(val, CaptureEntrySpec) else CaptureEntrySpec(from_=val)
        for key, val in capture.items()
    }


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
    campaign_state_path: Path | None = None,
    prior_dispatch_id: str | None = None,
) -> DispatchOutcome:
    """Execute a single food truck dispatch.

    Orchestrates: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns DispatchOutcome (DispatchCompleted | DispatchRejected).
    """
    effective_name = dispatch_name or recipe

    def _reject(error_code: FleetErrorCode, message: str, **kwargs: Any) -> DispatchRejected:
        """Pre-lock, pre-dispatch-id rejection path — no per-dispatch state file exists yet."""
        rejection = DispatchRejected(error_code=error_code, message=message, **kwargs)
        _write_campaign_refusal(campaign_state_path, effective_name, error_code)
        return rejection

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
            campaign_state_path=campaign_state_path,
            prior_dispatch_id=prior_dispatch_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("execute_dispatch failed", exc_info=True)
        return _reject(
            error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        lock.release()


def _is_abandon_reason(skill_result: SkillResult) -> bool:
    """Return True when the kill reason indicates resume would be futile."""
    if skill_result.retry_reason in _ABANDON_REASONS:
        return True
    if (
        skill_result.retry_reason == RetryReason.RESUME
        and skill_result.infra.exit_category == InfraExitCategory.CONTEXT_EXHAUSTED
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
    capture: dict[str, CaptureEntrySpec] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    idle_output_timeout: int | None = None,
    caller_session_id: str = "",
    campaign_state_path: Path | None = None,
    prior_dispatch_id: str | None = None,
) -> DispatchOutcome:
    """Inner dispatch body — called after lock acquisition."""
    from autoskillit.fleet.state import (
        DispatchRecord,
        DispatchStateHandle,
        DispatchStatus,
        append_dispatch_record,
        normalize_dispatch_token_usage,
        read_state,
        upsert_dispatch_record_by_name,
        write_captured_values,
    )

    if tool_ctx.recipes is None:
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_MANIFEST_MISSING,
            message="Recipe repository not configured.",
        )

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
            message=f"Recipe '{recipe}' not found.",
        )

    try:
        validation_result = tool_ctx.recipes.load_and_validate(
            recipe,
            tool_ctx.project_dir,
            suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
            temp_dir=tool_ctx.temp_dir,
        )
    except Exception as exc:
        logger.warning("load_and_validate failed for '%s'", recipe, exc_info=True)
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
            message=f"Recipe '{recipe}' could not be loaded: {exc}",
        )

    if not validation_result.get("valid", False):
        error_findings = [
            s for s in validation_result.get("suggestions", []) if s.get("severity") == "error"
        ]
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
            message=f"Recipe '{recipe}' has validation errors: "
            + "; ".join(f"[{f['rule']}] {f['message']}" for f in error_findings[:3]),
        )

    try:
        full_recipe = tool_ctx.recipes.load(recipe_obj.path)
    except Exception as exc:
        logger.warning("load_recipe failed for '%s'", recipe, exc_info=True)
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
            message=f"Recipe '{recipe}' could not be loaded: {exc}",
        )

    _DISPATCHABLE_KINDS = frozenset({"standard", "food-truck"})

    if full_recipe.kind not in _DISPATCHABLE_KINDS:
        return DispatchRejected(
            error_code=FleetErrorCode.FLEET_INVALID_RECIPE_KIND,
            message=f"Recipe '{recipe}' has kind '{full_recipe.kind}'. "
            "Only standard and food-truck recipes can be dispatched.",
        )

    effective_ingredients = ingredients or {}
    if "task" in full_recipe.ingredients and "task" not in effective_ingredients:
        effective_ingredients = {"task": task, **effective_ingredients}

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
        except Exception:
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

    def _reject_with_state(error_code: FleetErrorCode, message: str) -> DispatchRejected:
        """Post-dispatch-id rejection path — writes both per-dispatch and campaign state."""
        try:
            append_dispatch_record(
                state_path,
                DispatchRecord(
                    name=effective_name,
                    status=DispatchStatus.REFUSED,
                    reason=error_code,
                ),
            )
        except Exception:
            logger.warning("_reject_with_state: per-dispatch state write failed", exc_info=True)
        _write_campaign_refusal(campaign_state_path, effective_name, error_code)
        return DispatchRejected(error_code=error_code, message=message, dispatch_id=dispatch_id)

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

    completion_marker = identity.completion_marker
    sentinel_contract = identity.sentinel_contract
    from autoskillit.fleet.sidecar import sidecar_path as compute_sidecar_path  # noqa: PLC0415

    dispatch_sidecar_path = str(compute_sidecar_path(dispatch_id, tool_ctx.project_dir))

    prompt = prompt_builder(
        recipe=recipe,
        task=task,
        ingredients=effective_ingredients,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        l3_timeout_sec=timeout_sec or 1800,
        capture=capture,
    )

    if tool_ctx.executor is None:
        return _reject_with_state(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Executor not configured.",
        )

    started_at = time.time()
    _dispatched_pid: list[int] = []

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
        _dispatched_pid.append(pid)
        try:
            create_time = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            create_time = 0.0
        _write_pid(
            state_path, effective_name, dispatch_id, pid, ticks, dispatch_sidecar_path, create_time
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
                    timeout=float(timeout_sec) if timeout_sec else None,
                    idle_output_timeout=float(idle_output_timeout)
                    if idle_output_timeout is not None
                    else None,
                    env_extras={
                        "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
                        "AUTOSKILLIT_CAMPAIGN_ID": campaign_id,
                        "AUTOSKILLIT_DISPATCH_ID": dispatch_id,
                        "AUTOSKILLIT_SESSION_DEADLINE": str(
                            started_at
                            + (
                                float(timeout_sec)
                                if timeout_sec is not None
                                else float(tool_ctx.config.fleet.default_timeout_sec)
                            )
                        ),
                    },
                    requires_packs=list(full_recipe.requires_packs) or ["kitchen-core"],
                    on_spawn=_on_spawn,
                    sentinel_contract=sentinel_contract,
                )
            finally:
                _hb_trigger.set()
                tg.cancel_scope.cancel()

        ended_at = time.time()
    finally:
        if marker_path is not None:
            try:
                marker_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "dispatch_marker_unlink_failed", marker=str(marker_path), exc_info=True
                )

    # --- Timeout pre-check: short-circuit before result-block parsing ---
    if skill_result.subtype == "timeout":
        record = DispatchRecord(
            name=effective_name,
            status=DispatchStatus.FAILURE,
            dispatch_id=dispatch_id,
            campaign_id=campaign_id,
            caller_session_id=caller_session_id,
            dispatched_session_id=skill_result.session_id,
            dispatched_pid=_dispatched_pid[0] if _dispatched_pid else 0,
            reason=FleetErrorCode.FLEET_L3_TIMEOUT,
            kill_reason=skill_result.retry_reason or "",
            infra_exit_category=skill_result.infra.exit_category or "",
            token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
            started_at=started_at,
            ended_at=ended_at,
        )
        upsert_dispatch_record_by_name(state_path, record)
        _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)
        return DispatchCompleted(
            success=False,
            dispatch_status=DispatchStatus.FAILURE,
            dispatch_id=dispatch_id,
            dispatched_session_id=skill_result.session_id or "",
            reason=FleetErrorCode.FLEET_L3_TIMEOUT,
            token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
            lifespan_started=skill_result.lifespan_started,
            stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
        )

    jsonl_path = claude_code_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")

    additional_jsonl_paths: list[Path] = []
    session_ids_for_jsonl = prior_session_chain[:]
    if prior_dispatched_session_id and prior_dispatched_session_id not in session_ids_for_jsonl:
        session_ids_for_jsonl.append(prior_dispatched_session_id)
    for sid in session_ids_for_jsonl:
        path = claude_code_log_path(str(tool_ctx.project_dir), sid)
        if path is not None:
            additional_jsonl_paths.append(path)

    parsed = parse_l3_result_block(
        stdout=skill_result.result or "",
        expected_dispatch_id=dispatch_id,
        assistant_messages_path=jsonl_path,
        prior_dispatch_ids=prior_ids or None,
        additional_jsonl_paths=additional_jsonl_paths or None,
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

    accumulated_session_chain = prior_session_chain[:]
    if (
        prior_dispatched_session_id
        and prior_dispatched_session_id not in accumulated_session_chain
    ):
        accumulated_session_chain.append(prior_dispatched_session_id)

    try:
        project_log_dir = str(claude_code_project_dir(str(tool_ctx.project_dir)))
    except OSError:
        project_log_dir = ""

    record = DispatchRecord(
        name=effective_name,
        status=final_status,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        caller_session_id=caller_session_id,
        dispatched_session_id=skill_result.session_id,
        session_chain=accumulated_session_chain,
        dispatched_session_log_dir=project_log_dir,
        dispatched_pid=_dispatched_pid[0] if _dispatched_pid else 0,
        reason=reason,
        kill_reason=skill_result.retry_reason or "",
        infra_exit_category=skill_result.infra.exit_category or "",
        token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
        started_at=started_at,
        ended_at=ended_at,
    )

    extracted: dict[str, str] = {}
    if final_status == DispatchStatus.SUCCESS and capture and parsed.payload:
        extracted = _extract_captures(capture, parsed.payload)

    upsert_dispatch_record_by_name(state_path, record)
    if not state_path.exists():
        raise FileNotFoundError(f"State file missing after upsert: {state_path}")
    if extracted:
        write_captured_values(state_path, extracted)
    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    if parsed.outcome == "completed_clean":
        envelope_success = bool(parsed.payload and parsed.payload.get("success", False))
        return DispatchCompleted(
            success=envelope_success,
            dispatch_status=final_status,
            dispatch_id=dispatch_id,
            dispatched_session_id=skill_result.session_id or "",
            reason=reason,
            token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
            l3_payload=parsed.payload,
            l3_parse_source=parsed.source,
            lifespan_started=skill_result.lifespan_started,
            stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
        )
    elif parsed.outcome == "completed_dirty":
        return DispatchCompleted(
            success=False,
            dispatch_status=final_status,
            dispatch_id=dispatch_id,
            dispatched_session_id=skill_result.session_id or "",
            reason=FleetErrorCode.FLEET_L3_PARSE_FAILED,
            token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
            l3_payload=None,
            l3_raw_body=parsed.raw_body,
            l3_parse_error=parsed.parse_error,
            l3_parse_source=parsed.source,
            lifespan_started=skill_result.lifespan_started,
            stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
        )
    else:
        return DispatchCompleted(
            success=False,
            dispatch_status=final_status,
            dispatch_id=dispatch_id,
            dispatched_session_id=skill_result.session_id or "",
            reason=FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK,
            token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
            l3_payload=None,
            l3_parse_source=parsed.source,
            lifespan_started=skill_result.lifespan_started,
            resume_checkpoint=dispatch_checkpoint.to_dict() if dispatch_checkpoint else None,
            stderr=truncate_text(skill_result.stderr or "", ENVELOPE_STDERR_MAX),
        )
