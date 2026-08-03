"""Fleet dispatch orchestration API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import psutil

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CaptureEntrySpec,
    FleetErrorCode,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureMode,
    ProcessStaleError,
    SessionCheckpoint,  # noqa: F401, TC001
    SkillResult,
    atomic_write,
    get_logger,
)
from autoskillit.fleet._capture import _extract_captures, _normalize_capture_spec
from autoskillit.fleet._checkpoint_bridge import (
    bind_dispatch_launch_contract,
    load_dispatch_progress,
)
from autoskillit.fleet._expressions import _CAMPAIGN_REF_RE, _interpolate_campaign_refs
from autoskillit.fleet._issue_url_helpers import extract_issue_urls
from autoskillit.fleet._native_shell_capture import (
    FoodTruckLineageInitializationError,
    prepare_dispatch_identity,
    prepare_food_truck_lineage,
    resolve_dispatch_timeout,
    set_lineage_terminal_state,
)
from autoskillit.fleet._outcome import (
    _checkpoint_to_dict,
    _sanitize_managed_capture_diagnostics,
    build_dispatch_result,
    classify_dispatch_outcome,
)
from autoskillit.fleet._outcome import (
    build_success_short_circuit as _build_success_short_circuit,
)
from autoskillit.fleet.result_parser import parse_l3_result_block
from autoskillit.fleet.state import DispatchStatus
from autoskillit.fleet.state_recovery import prepare_resume
from autoskillit.fleet.state_types import (
    DispatchAggregatePhase,
    DispatchCompleted,
    DispatchEffectName,
    DispatchProvenanceTracker,
    DispatchRejected,
    DispatchResult,
)
from autoskillit.workspace import default_skill_resolver, prepare_skill_projection

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, ResolvedLaunchContract
    from autoskillit.fleet.state import DispatchStateHandle
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


class DispatchSpawnFailed(RuntimeError):
    """Spawn-time failure signal for the ``execute_dispatch`` callback path.

    Attributes:
        error_code: FleetErrorCode identifying the failure category.
        message: Human-readable description of the spawn failure.
    """

    def __init__(self, error_code: FleetErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@asynccontextmanager
async def _dispatch_heartbeat(
    dispatches_dir: Path,
    dispatch_id: str,
    heartbeat_interval: float = 30.0,
) -> AsyncGenerator[Path | None]:
    """Write, heartbeat, and clean up a dispatch heartbeat file.

    Co-locates the heartbeat file with the dispatch state file in ``dispatches_dir``
    so the cross-campaign reaper can discover it without any path threading.
    Yields the heartbeat ``Path`` on success, or ``None`` if the initial write fails.
    """
    hb_path = dispatches_dir / f"dispatch-{dispatch_id}.heartbeat"
    try:
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hb_path, "{}")
    except OSError:
        logger.warning("dispatch_heartbeat_write_failed", heartbeat=str(hb_path), exc_info=True)
        yield None
        return

    async def _touch_heartbeat() -> None:
        while True:
            await asyncio.sleep(heartbeat_interval)
            try:
                hb_path.touch()
            except OSError:
                logger.warning(
                    "dispatch_heartbeat_touch_failed", heartbeat=str(hb_path), exc_info=True
                )

    hb_task: asyncio.Task[None] | None = None
    try:
        hb_task = asyncio.get_running_loop().create_task(_touch_heartbeat())
        yield hb_path
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("dispatch_heartbeat_task_failed", exc_info=True)
        try:
            hb_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "dispatch_heartbeat_unlink_failed", heartbeat=str(hb_path), exc_info=True
            )


def _write_pid(
    state_path: Path,
    dispatch_name: str,
    dispatch_id: str,
    pid: int,
    starttime_ticks: int,
    sidecar_path: str | None = None,
    dispatched_create_time: float = 0.0,
    identity_degraded: bool = False,
    issue_url: str = "",
    dispatched_boot_id: str = "",
    provenance: DispatchProvenanceTracker | None = None,
    *,
    enforce_max_resume_attempts: bool = False,
) -> str | None:
    """on_spawn callback: atomically mark dispatch as running with dispatched_pid.

    L2 — Fail-closed: if ``mark_dispatch_running`` raises (e.g. illegal state
    transition), the spawned child is killed via ``kill_process_tree`` (the
    canonical sync kill primitive used by ``_dispatch_reaper``) and the
    exception's message string is returned to the caller via closure-scoped
    state. Raising the exception from ``_on_spawn`` is NOT safe because
    ``_execute_claude_headless`` catches runner exceptions and returns
    ``SkillResult.crashed`` — the propagated exception would never reach the
    outer ``execute_dispatch`` wrapper. The caller therefore inspects the
    returned error string (or the closure-scoped ``_spawn_error`` list) and
    translates it into a ``FLEET_L3_STARTUP_OR_CRASH`` envelope.

    Returns:
        None on success; the formatted error message string on failure (also
        recorded via the side-effect of having killed the child).
    """
    from autoskillit.execution import kill_process_tree
    from autoskillit.fleet import mark_dispatch_running

    try:
        mark_dispatch_running(
            state_path,
            dispatch_name,
            dispatch_id=dispatch_id,
            dispatched_pid=pid,
            starttime_ticks=starttime_ticks,
            boot_id=dispatched_boot_id,
            dispatched_create_time=dispatched_create_time,
            sidecar_path=sidecar_path,
            identity_degraded=identity_degraded,
            issue_url=issue_url,
            enforce_max_resume_attempts=enforce_max_resume_attempts,
        )
        return None
    except Exception as exc:
        # Fail-closed: kill the child before the state record can diverge.
        if pid:
            try:
                if provenance is not None:
                    provenance.start(
                        DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                        identities={"pid": pid},
                    )
                cleanup_result = kill_process_tree(pid, timeout=2.0)
                if provenance is not None:
                    provenance.record_local_cleanup(cleanup_result)
                    if cleanup_result.complete:
                        provenance.confirm(
                            DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                            receipt="bounded process-tree wait confirmed no survivors",
                            identities={"pid": pid},
                        )
                    else:
                        provenance.mark_ambiguous(
                            DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                            evidence="local process-tree cleanup left survivors",
                            identities={"pid": pid},
                        )
            except Exception:
                logger.warning(
                    "_write_pid: kill_process_tree failed for pid=%d",
                    pid,
                    exc_info=True,
                )
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        cause_str = f" caused by {type(cause).__name__}: {cause}" if cause is not None else ""
        return f"_on_spawn transition failed: {type(exc).__name__}: {exc}{cause_str}"


def _post_dispatch_cleanup(
    tool_ctx: ToolContext,
    skill_result: SkillResult,
    cache_invalidator: Callable[[str], None] | None,
    quota_refresher: Callable[..., Any],
) -> None:
    """Run quota cache invalidation and background quota refresh."""
    if cache_invalidator is not None:
        cache_invalidator(tool_ctx.config.quota_guard.cache_path)

    if tool_ctx.background is not None:
        tool_ctx.background.submit(
            quota_refresher(tool_ctx.config.quota_guard),
            label="quota_post_dispatch_refresh",
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
    capture: dict[str, str | dict[str, str]] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    idle_output_timeout: int | None = None,
    caller_session_id: str = "",
    prior_dispatch_id: str | None = None,
    resume_message: str | None = None,
    caller_instructions: str | None = None,
    dispatch_backend: CodingAgentBackend | None = None,
    effective_backend_map: dict[str, str] | None = None,
    provenance: DispatchProvenanceTracker | None = None,
    native_shell_capture_mode: NativeShellCaptureMode | None = None,
) -> DispatchResult:
    """Execute a single food truck dispatch.

    Orchestrates: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns DispatchResult wrapping the outcome plus the per-dispatch state path.
    """
    effective_name = dispatch_name or recipe
    provenance = provenance or DispatchProvenanceTracker()

    def _reject(error_code: FleetErrorCode, message: str, **kwargs: Any) -> DispatchResult:
        """Pre-lock, pre-dispatch-id rejection path — no per-dispatch state file exists yet."""
        rejection = DispatchRejected(
            error_code=error_code,
            message=message,
            effect_provenance=provenance.snapshot(),
            **kwargs,
        )
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
            dispatch_backend=dispatch_backend,
            effective_backend_map=effective_backend_map,
            provenance=provenance,
            native_shell_capture_mode=native_shell_capture_mode,
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
        failure_text = f"{type(underlying).__name__}: {underlying}"
        sanitized_failure_text = _sanitize_managed_capture_diagnostics(failure_text)
        snapshot = provenance.snapshot()
        if snapshot.aggregate_phase != DispatchAggregatePhase.NOT_STARTED:
            identities = {
                key: value
                for effect in snapshot.effects
                for key, value in effect.known_downstream_identities
            }
            diagnostic_message = (
                sanitized_failure_text
                if sanitized_failure_text
                else "Food-truck dispatch failed during startup."
            )
            failure_status = DispatchStatus.FAILURE
            state_path = Path(identities["state_path"]) if identities.get("state_path") else None
            if state_path is not None:
                try:
                    from autoskillit.fleet.state import (  # noqa: PLC0415
                        DispatchRecord,
                        append_dispatch_record,
                    )

                    append_dispatch_record(
                        state_path,
                        DispatchRecord(
                            name=effective_name,
                            status=failure_status,
                            dispatch_id=identities.get("dispatch_id", ""),
                            dispatched_session_id=identities.get("dispatched_session_id", ""),
                            reason=str(FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH),
                            diagnostic_message=diagnostic_message,
                            effect_provenance=snapshot.to_dict(),
                        ),
                    )
                except Exception:
                    state_path = None
                    logger.warning(
                        "execute_dispatch crash-state persistence failed",
                        exc_info=True,
                    )
            return DispatchResult(
                DispatchCompleted(
                    success=False,
                    dispatch_status=failure_status,
                    dispatch_id=identities.get("dispatch_id", ""),
                    dispatched_session_id=identities.get("dispatched_session_id", ""),
                    reason=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
                    diagnostic_message=diagnostic_message,
                    effect_provenance=snapshot,
                ),
                per_dispatch_state_path=state_path,
            )
        return _reject(
            error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            message=(
                sanitized_failure_text
                if sanitized_failure_text
                else "Food-truck dispatch failed during startup."
            ),
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
    dispatch_backend: CodingAgentBackend | None = None,
    effective_backend_map: dict[str, str] | None = None,
    provenance: DispatchProvenanceTracker | None = None,
    native_shell_capture_mode: NativeShellCaptureMode | None = None,
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

    provenance = provenance or DispatchProvenanceTracker()

    if tool_ctx.recipes is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_MANIFEST_MISSING,
                message="Recipe repository not configured.",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    recipe_obj = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
    if recipe_obj is None:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
                message=f"Recipe '{recipe}' not found.",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    _effective_backend = dispatch_backend if dispatch_backend is not None else tool_ctx.backend
    _caller_backend_name = tool_ctx.backend.name if tool_ctx.backend is not None else ""

    try:
        validation_result = tool_ctx.recipes.load_and_validate(
            recipe,
            tool_ctx.project_dir,
            suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
            ingredient_overrides=ingredients,
            temp_dir=tool_ctx.temp_dir,
            backend_name=_effective_backend.name if _effective_backend else None,
            effective_backend_map=effective_backend_map,
        )
    except ProcessStaleError as exc:
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_PROCESS_STALE,
                message=str(exc),
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )
    except Exception as exc:
        logger.warning("load_and_validate failed for '%s'", recipe, exc_info=True)
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' could not be loaded: {exc}",
                effect_provenance=provenance.snapshot(),
            ),
            per_dispatch_state_path=None,
        )

    if not validation_result.get("valid", False):
        structural_errors = validation_result.get("errors", [])
        error_findings = [
            s for s in validation_result.get("suggestions", []) if s.get("severity") == "error"
        ]
        total_errors = len(structural_errors) + len(error_findings)
        error_parts = structural_errors[:3] + [
            f"[{f['rule']}] {f['message']}" for f in error_findings[:3]
        ]
        shown = len(error_parts)
        if total_errors > shown:
            error_parts.append(f"+{total_errors - shown} more errors")
        return DispatchResult(
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
                message=f"Recipe '{recipe}' has validation errors: " + "; ".join(error_parts),
                effect_provenance=provenance.snapshot(),
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
                effect_provenance=provenance.snapshot(),
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
                effect_provenance=provenance.snapshot(),
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
        effective_ingredients,
        full_recipe.ingredients,
        tool_ctx.project_dir,
    )

    effective_name = dispatch_name or recipe
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    campaign_id = tool_ctx.kitchen_id

    recipe_snapshot = {
        "recipe_name": recipe_obj.name,
        "recipe_path": str(recipe_obj.path),
        "recipe_version": recipe_obj.recipe_version or "",
        "content_hash": recipe_obj.content_hash or "",
        "effective_ingredients": dict(effective_ingredients),
    }

    if resume_session_id:
        provenance.start(
            DispatchEffectName.REQUESTED_RESUME_BINDING,
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )
        provenance.confirm(
            DispatchEffectName.REQUESTED_RESUME_BINDING,
            receipt="request argument captured",
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )
    provenance.start(
        DispatchEffectName.DISPATCH_ALLOCATION,
        identities={"prior_dispatch_id": prior_dispatch_id or ""},
    )
    if resume_session_id and prior_dispatch_id:
        provenance.start(
            DispatchEffectName.PRIOR_DISPATCH_BINDING,
            retry_relevant=False,
            identities={"prior_dispatch_id": prior_dispatch_id},
        )

    def _create_fresh_handle() -> DispatchStateHandle:
        return DispatchStateHandle.create_fresh(
            dispatches_dir,
            campaign_id,
            effective_name,
            "",
            [
                DispatchRecord(
                    name=effective_name,
                    caller_session_id=caller_session_id,
                    caller_backend_name=_caller_backend_name,
                ),
            ],
            recipe_snapshot,
        )

    identity_preparation = prepare_dispatch_identity(
        create_fresh_handle=_create_fresh_handle,
        dispatches_dir=dispatches_dir,
        effective_name=effective_name,
        resume_session_id=resume_session_id,
        prior_dispatch_id=prior_dispatch_id,
    )
    handle = identity_preparation.handle
    if identity_preparation.prior_success_record is not None:
        logger.info(
            "resume_skipped_prior_success",
            dispatch_name=effective_name,
            prior_dispatch_id=prior_dispatch_id,
        )
        provenance.confirm(
            DispatchEffectName.DISPATCH_ALLOCATION,
            receipt="opened authoritative prior dispatch state",
            identities={
                "dispatch_id": identity_preparation.prior_success_record.dispatch_id,
                "state_path": handle.state_path,
            },
        )
        provenance.confirm(
            DispatchEffectName.PRIOR_DISPATCH_BINDING,
            receipt="authoritative prior dispatch state reported success",
            retry_relevant=False,
            identities={
                "dispatch_id": identity_preparation.prior_success_record.dispatch_id,
                "dispatched_session_id": (
                    identity_preparation.prior_success_record.dispatched_session_id
                ),
            },
        )
        provenance.start(
            DispatchEffectName.COMMIT,
            identities={
                "dispatch_id": identity_preparation.prior_success_record.dispatch_id,
                "dispatched_session_id": (
                    identity_preparation.prior_success_record.dispatched_session_id
                ),
            },
        )
        provenance.confirm(
            DispatchEffectName.COMMIT,
            receipt="reused committed prior dispatch",
            identities={
                "dispatch_id": identity_preparation.prior_success_record.dispatch_id,
                "dispatched_session_id": (
                    identity_preparation.prior_success_record.dispatched_session_id
                ),
            },
        )
        return _build_success_short_circuit(
            identity_preparation.prior_success_record,
            handle,
            provenance.snapshot(),
        )

    identity = handle.identity
    dispatch_id = identity.dispatch_id
    state_path = handle.state_path
    provenance.confirm(
        DispatchEffectName.DISPATCH_ALLOCATION,
        receipt="per-dispatch state identity persisted",
        identities={
            "dispatch_id": dispatch_id,
            "state_path": state_path,
        },
    )
    if resume_session_id and prior_dispatch_id:
        provenance.confirm(
            DispatchEffectName.PRIOR_DISPATCH_BINDING,
            receipt="prior dispatch state opened",
            retry_relevant=False,
            identities={
                "prior_dispatch_id": prior_dispatch_id,
                "dispatch_id": dispatch_id,
            },
        )
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None

    def _complete_failure_with_state(
        error_code: FleetErrorCode,
        message: str,
        *,
        dispatch_status: DispatchStatus = DispatchStatus.REFUSED,
        dispatched_session_id: str = "",
    ) -> DispatchResult:
        """Post-dispatch-id failure path — writes per-dispatch state only.

        Campaign state is written by the caller via _write_dispatch_to_campaign_state.
        """
        completed = DispatchCompleted(
            success=False,
            dispatch_status=dispatch_status,
            dispatch_id=dispatch_id,
            dispatched_session_id=dispatched_session_id,
            reason=error_code,
            diagnostic_message=message,
            effect_provenance=provenance.snapshot(),
        )
        if managed_lineage_ref is not None:
            try:
                set_lineage_terminal_state(
                    tool_ctx,
                    managed_lineage_ref,
                    ManagedHeadlessSessionTerminalState.FAILED,
                )
            except Exception:
                logger.warning(
                    "_reject_with_state: managed lineage close failed",
                    exc_info=True,
                )
        try:
            append_dispatch_record(
                state_path,
                DispatchRecord(
                    name=effective_name,
                    status=dispatch_status,
                    reason=str(error_code),
                    diagnostic_message=message,
                    dispatch_id=dispatch_id,
                    dispatched_session_id=dispatched_session_id,
                    effect_provenance=provenance.snapshot().to_dict(),
                    managed_lineage_ref=managed_lineage_ref,
                ),
            )
        except Exception:
            logger.warning(
                "_complete_failure_with_state: per-dispatch state write failed",
                exc_info=True,
            )
            return DispatchResult(completed, per_dispatch_state_path=None)
        return DispatchResult(completed, per_dispatch_state_path=state_path)

    if effective_ingredients:
        unknown = set(effective_ingredients.keys()) - set(full_recipe.ingredients.keys())
        if unknown:
            return _complete_failure_with_state(
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
        return _complete_failure_with_state(
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
            return _complete_failure_with_state(
                FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                str(exc),
            )

    quota_result = await quota_checker(tool_ctx.config.quota_guard)
    if quota_result.get("should_sleep"):
        await asyncio.sleep(quota_result.get("sleep_seconds", 0))

    resolved_timeout = resolve_dispatch_timeout(
        timeout_sec, tool_ctx.config.fleet.default_timeout_sec
    )
    if tool_ctx.executor is None:
        return _complete_failure_with_state(
            FleetErrorCode.FLEET_MANIFEST_MISSING,
            "Executor not configured.",
        )

    def _prepare_launch(for_dispatch_id: str) -> tuple[str, Any, Any, Path]:
        prepared_prompt = prompt_builder(
            recipe=recipe,
            task=task,
            ingredients=effective_ingredients,
            dispatch_id=for_dispatch_id,
            campaign_id=campaign_id,
            l3_timeout_sec=int(resolved_timeout),
            capture=capture,
            caller_instructions=caller_instructions,
        )
        plugin_authority = capability_preparation = None
        if _effective_backend is not None:
            plugin_authority, capability_preparation = prepare_skill_projection(
                project_root=tool_ctx.project_dir,
                cwd=tool_ctx.project_dir,
                resolver=tool_ctx.skill_resolver or default_skill_resolver(),
                visibility=tool_ctx.config.skill_visibility_spec(),
                default_base_branch=tool_ctx.config.branching.default_base_branch,
                recipe_packs=tool_ctx.active_recipe_packs,
                recipe_features=tool_ctx.active_recipe_features,
            )
        authoritative_cwd = (
            capability_preparation.cwd
            if capability_preparation is not None
            else tool_ctx.project_dir
        ).resolve()
        return (
            prepared_prompt,
            plugin_authority,
            capability_preparation,
            authoritative_cwd,
        )

    lineage_backend_name = (
        _effective_backend.name
        if _effective_backend is not None
        else (_caller_backend_name or "unknown")
    )
    try:
        lineage_preparation = prepare_food_truck_lineage(
            tool_ctx=tool_ctx,
            identity_preparation=identity_preparation,
            launch=_prepare_launch(dispatch_id),
            prepare_launch=_prepare_launch,
            create_fresh_handle=_create_fresh_handle,
            effective_name=effective_name,
            prior_dispatch_id=prior_dispatch_id,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            resume_preparer=lambda: prepare_resume(
                state_path,
                effective_name,
                continue_on_failure=True,
            ),
            native_shell_capture_mode=native_shell_capture_mode,
            lineage_backend_name=lineage_backend_name,
        )
    except FoodTruckLineageInitializationError:
        return _complete_failure_with_state(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            "Food-truck dispatch initialization failed.",
        )

    handle = lineage_preparation.handle
    identity = handle.identity
    dispatch_id = identity.dispatch_id
    state_path = handle.state_path
    (
        prompt,
        food_truck_plugin_authority,
        food_truck_capability_preparation,
        _lineage_anchor,
    ) = lineage_preparation.launch
    capture_decision = lineage_preparation.capture_decision
    managed_lineage_ref = lineage_preparation.managed_lineage_ref
    preflight = lineage_preparation.preflight
    resume_session_id = lineage_preparation.resume_session_id
    resume_checkpoint = lineage_preparation.resume_checkpoint
    resume_message = lineage_preparation.resume_message
    prior_session_chain = list(lineage_preparation.prior_session_chain)
    prior_dispatched_session_id = lineage_preparation.prior_dispatched_session_id
    if lineage_preparation.halted_reason is not None:
        return DispatchResult(
            outcome=DispatchRejected(
                error_code=FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                message=lineage_preparation.halted_reason,
                effect_provenance=provenance.snapshot(),
                dispatch_id=dispatch_id,
            ),
            per_dispatch_state_path=state_path,
        )

    try:
        current_state = read_state(state_path)
        current_record = (
            next(
                (d for d in current_state.dispatches if d.name == effective_name),
                None,
            )
            if current_state is not None
            else None
        )
        if current_record is None:
            current_record = DispatchRecord(name=effective_name)
        current_record.dispatch_id = dispatch_id
        current_record.campaign_id = campaign_id
        current_record.caller_session_id = caller_session_id
        current_record.caller_backend_name = _caller_backend_name
        current_record.backend_name = lineage_backend_name
        current_record.effect_provenance = provenance.snapshot().to_dict()
        current_record.managed_lineage_ref = managed_lineage_ref
        upsert_dispatch_record_by_name(state_path, current_record)
    except Exception:
        logger.warning("managed_food_truck_lineage_state_write_failed", exc_info=True)
        return _complete_failure_with_state(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            "Food-truck dispatch initialization failed.",
        )

    _locator = _effective_backend.session_locator() if _effective_backend is not None else None

    if resume_session_id:
        _primary_jsonl = (
            _locator.session_log_path(str(tool_ctx.project_dir), resume_session_id)
            if _locator is not None
            else None
        )
        if _primary_jsonl is None or not _primary_jsonl.exists():
            logger.warning(
                "resume_jsonl_missing",
                resume_session_id=resume_session_id,
                expected_path=str(_primary_jsonl) if _primary_jsonl else "none",
            )
            _fallback_session_id = prior_session_chain[-1] if prior_session_chain else ""
            if _fallback_session_id:
                _fallback_jsonl = (
                    _locator.session_log_path(str(tool_ctx.project_dir), _fallback_session_id)
                    if _locator is not None
                    else None
                )
                if _fallback_jsonl is not None and _fallback_jsonl.exists():
                    logger.info(
                        "resume_session_fallback",
                        original_session_id=resume_session_id,
                        fallback_session_id=_fallback_session_id,
                    )
                    resume_session_id = _fallback_session_id
                else:
                    return _complete_failure_with_state(
                        FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                        f"JSONL log for session {resume_session_id} not found",
                    )
            else:
                return _complete_failure_with_state(
                    FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                    f"JSONL log for session {resume_session_id} not found",
                )

    if resume_session_id:
        provenance.start(
            DispatchEffectName.EFFECTIVE_RESUME_BINDING,
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )
        provenance.confirm(
            DispatchEffectName.EFFECTIVE_RESUME_BINDING,
            receipt="effective resume session resolved",
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )

    resume_line_offset = 0
    if resume_session_id:
        _resume_jsonl = (
            _locator.session_log_path(str(tool_ctx.project_dir), resume_session_id)
            if _locator is not None
            else None
        )
        if _resume_jsonl is not None and _resume_jsonl.exists():
            resume_line_offset = len(_resume_jsonl.read_text(encoding="utf-8").splitlines())

    completion_marker = identity.completion_marker
    sentinel_contract = identity.sentinel_contract
    from autoskillit.fleet.sidecar import sidecar_path as compute_sidecar_path  # noqa: PLC0415

    dispatch_sidecar_path = str(compute_sidecar_path(dispatch_id, tool_ctx.project_dir))
    started_at = time.time()
    _dispatched_pid: list[int] = []
    _dispatched_ticks: list[int] = []
    _dispatched_create_time: list[float] = []
    _dispatched_boot_id: list[str] = []
    _dispatched_session_id: list[str] = []
    _spawn_error: list[str] = []
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
    _issue_urls_raw = extract_issue_urls(effective_ingredients)

    def _on_spawn(pid: int, ticks: int) -> None:
        from autoskillit.core import read_boot_id

        _dispatched_pid.append(pid)
        provenance.start(
            DispatchEffectName.CHILD_DISCOVERY,
            identities={"pid": pid, "dispatch_id": dispatch_id},
        )
        try:
            create_time = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            create_time = 0.0
        boot_id = read_boot_id() or ""
        _dispatched_ticks.append(ticks)
        _dispatched_create_time.append(create_time)
        _dispatched_boot_id.append(boot_id)
        provenance.confirm(
            DispatchEffectName.CHILD_DISCOVERY,
            receipt="captured one process identity tuple",
            identities={
                "pid": pid,
                "starttime_ticks": ticks,
                "create_time": create_time,
                "boot_id": boot_id,
                "identity_degraded": ticks == 0 or create_time == 0.0 or not boot_id,
            },
        )
        # Resume branch iff preflight was returned by prepare_resume above.
        # Cap enforcement (MAX_CONSECUTIVE_RESUME_ATTEMPTS) lives one layer down
        # in mark_dispatch_running.
        is_resume_branch = preflight is not None
        # Record instead of raising: the executor converts callback errors to a crashed result.
        err = _write_pid(
            state_path,
            effective_name,
            dispatch_id,
            pid,
            ticks,
            dispatch_sidecar_path,
            create_time,
            identity_degraded=(ticks == 0 or create_time == 0.0 or not boot_id),
            issue_url=_issue_urls_raw,
            dispatched_boot_id=boot_id,
            provenance=provenance,
            enforce_max_resume_attempts=is_resume_branch,
        )
        if err is not None:
            _spawn_error.append(err)

    def _on_session_id(session_id: str) -> None:
        from autoskillit.fleet.state import mark_dispatch_session_identity

        _dispatched_session_id.append(session_id)
        mark_dispatch_session_identity(
            state_path, effective_name, dispatched_session_id=session_id
        )
        provenance.confirm(
            DispatchEffectName.PROCESS_SPAWN,
            receipt="executor reported spawned process and authoritative session identity",
            identities={
                "pid": _dispatched_pid[0] if _dispatched_pid else 0,
                "starttime_ticks": _dispatched_ticks[0] if _dispatched_ticks else 0,
                "dispatch_id": dispatch_id,
                "dispatched_session_id": session_id,
            },
        )

    def _on_launch_resolved(launch_contract: ResolvedLaunchContract) -> None:
        bind_dispatch_launch_contract(state_path, effective_name, launch_contract)

    marker_dir: Path | None = None
    if _locator is not None:
        try:
            marker_dir = _locator.project_log_dir(str(tool_ctx.project_dir))
        except OSError:
            pass

    from autoskillit.core import execution_marker  # noqa: PLC0415

    _dispatch_completed_normally = False
    try:
        provenance.start(
            DispatchEffectName.PROCESS_SPAWN,
            identities={"dispatch_id": dispatch_id},
        )
        async with execution_marker(
            marker_dir,
            caller_session_id,
            "dispatch",
        ):
            async with _dispatch_heartbeat(dispatches_dir, dispatch_id):
                skill_result = await tool_ctx.executor.dispatch_food_truck(
                    orchestrator_prompt=prompt,
                    cwd=str(tool_ctx.project_dir),
                    completion_marker=completion_marker,
                    plugin_authority=food_truck_plugin_authority,
                    capability_preparation=food_truck_capability_preparation,
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
                    on_session_id_resolved=_on_session_id,
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
                    backend_authority=(
                        BackendAuthority(
                            backend=dispatch_backend.name,
                            kind=BackendAuthorityKind.CALLER,
                            tier=BackendAuthorityTier.CALLER,
                            key_path="dispatch.backend",
                        )
                        if dispatch_backend is not None
                        else None
                    ),
                    native_shell_capture_decision=capture_decision,
                    managed_lineage_ref=managed_lineage_ref,
                    on_launch_resolved=_on_launch_resolved,
                )

        # L2 fail-closed spawn gate: check closure-scoped error state.
        # If _on_spawn recorded a transition failure (and killed the child
        # via kill_process_tree), translate it to a structured envelope
        # instead of letting the dispatch proceed on a stale record.
        if _spawn_error:
            return _complete_failure_with_state(
                FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
                _spawn_error[0],
                dispatch_status=DispatchStatus.FAILURE,
                dispatched_session_id=(
                    _dispatched_session_id[0] if _dispatched_session_id else ""
                ),
            )
        if skill_result.session_id and not _dispatched_session_id:
            _on_session_id(skill_result.session_id)

        ended_at = max(time.time(), started_at + 1e-6)
        _dispatch_completed_normally = True
    except asyncio.CancelledError:
        provenance.request_cancel()
        try:
            with anyio.CancelScope(shield=True):
                set_lineage_terminal_state(
                    tool_ctx,
                    managed_lineage_ref,
                    ManagedHeadlessSessionTerminalState.CANCELLED,
                )
        except Exception:
            logger.warning(
                "failed to record managed lineage cancellation",
                dispatch_name=effective_name,
                exc_info=True,
            )
        if _dispatched_pid:
            try:
                from autoskillit.execution import kill_process_tree  # noqa: PLC0415

                provenance.start(
                    DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                    identities={"pid": _dispatched_pid[0]},
                )
                with anyio.CancelScope(shield=True):
                    cleanup_result = await anyio.to_thread.run_sync(
                        kill_process_tree,
                        _dispatched_pid[0],
                        2.0,
                    )
                provenance.record_local_cleanup(cleanup_result)
                if cleanup_result.complete:
                    provenance.confirm(
                        DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                        receipt="bounded process-tree wait confirmed no survivors",
                        identities={"pid": _dispatched_pid[0]},
                    )
                else:
                    provenance.mark_ambiguous(
                        DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                        evidence="local process-tree cleanup left survivors",
                        identities={"pid": _dispatched_pid[0]},
                    )
            except Exception:
                provenance.mark_ambiguous(
                    DispatchEffectName.LOCAL_PROCESS_CLEANUP,
                    evidence="local process-tree cleanup raised",
                    identities={"pid": _dispatched_pid[0]},
                )
                logger.warning(
                    "failed to capture local process cleanup evidence",
                    dispatch_name=effective_name,
                    exc_info=True,
                )
            try:
                from autoskillit.fleet.state import mark_dispatch_interrupted  # noqa: PLC0415

                captured_session_id = _dispatched_session_id[0] if _dispatched_session_id else ""
                if not captured_session_id:
                    sr = locals().get("skill_result")
                    if sr is not None:
                        captured_session_id = getattr(sr, "session_id", "") or ""

                with anyio.CancelScope(shield=True):
                    provenance.record_state_cleanup(confirmed=True)
                    mark_dispatch_interrupted(
                        state_path,
                        effective_name,
                        reason="signal_induced_cancellation",
                        dispatched_session_id=captured_session_id,
                        dispatched_session_log_dir=str(marker_dir)
                        if marker_dir is not None
                        else "",
                        effect_provenance=provenance.snapshot().to_dict(),
                    )
            except Exception:
                provenance.record_state_cleanup(confirmed=False)
                logger.warning(
                    "failed to record interrupted state on cancel",
                    dispatch_name=effective_name,
                    exc_info=True,
                )
        raise
    except Exception:
        try:
            set_lineage_terminal_state(
                tool_ctx,
                managed_lineage_ref,
                ManagedHeadlessSessionTerminalState.FAILED,
            )
        except Exception:
            logger.warning(
                "failed to record managed lineage failure",
                dispatch_name=effective_name,
                exc_info=True,
            )
        raise
    finally:
        if not _dispatch_completed_normally:
            from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

            with anyio.CancelScope(shield=True):
                provenance.start(
                    DispatchEffectName.LABEL_CLEANUP,
                    identities={"dispatch_id": dispatch_id},
                )
                labels_cleaned = await cleanup_orphaned_labels(
                    dispatch_sidecar_path, tool_ctx.github_client, issue_url=_issue_urls_raw
                )
                provenance.record_labels_cleanup(confirmed=labels_cleaned)
                if labels_cleaned:
                    provenance.confirm(
                        DispatchEffectName.LABEL_CLEANUP,
                        receipt="cancellation cleanup helper confirmed label cleanup",
                        identities={"dispatch_id": dispatch_id},
                    )
                else:
                    provenance.mark_ambiguous(
                        DispatchEffectName.LABEL_CLEANUP,
                        evidence="cancellation cleanup did not confirm label cleanup",
                        identities={"dispatch_id": dispatch_id},
                    )

    sidecar_file, sidecar_entries, dispatch_checkpoint = load_dispatch_progress(
        tool_ctx=tool_ctx,
        dispatch_sidecar_path=dispatch_sidecar_path,
        dispatch_id=dispatch_id,
        backend_name=_effective_backend.name if _effective_backend else "",
        recipe=recipe,
    )

    extended_chain = prior_session_chain[:]
    additional_jsonl_paths: list[Path] = []
    if skill_result.subtype == "timeout":
        parsed_result = None
    else:
        if prior_dispatched_session_id and prior_dispatched_session_id not in extended_chain:
            extended_chain.append(prior_dispatched_session_id)

        for sid in extended_chain:
            path = (
                _locator.session_log_path(str(tool_ctx.project_dir), sid)
                if _locator is not None
                else None
            )
            if path is not None:
                additional_jsonl_paths.append(path)

        jsonl_path = (
            _locator.session_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")
            if _locator is not None
            else None
        )
        if resume_line_offset and skill_result.session_id and resume_session_id:
            if skill_result.session_id != resume_session_id:
                logger.warning(
                    "resume_line_offset_invalidated",
                    resume_session_id=resume_session_id,
                    actual_session_id=skill_result.session_id,
                )
                resume_line_offset = 0
        parsed_result = parse_l3_result_block(
            stdout=skill_result.result or "",
            expected_dispatch_id=dispatch_id,
            assistant_messages_path=jsonl_path,
            prior_dispatch_ids=prior_ids or None,
            additional_jsonl_paths=additional_jsonl_paths or None,
            resume_line_offset=resume_line_offset,
        )

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
    if final_status != DispatchStatus.RESUMABLE:
        terminal_state = (
            ManagedHeadlessSessionTerminalState.SUCCEEDED
            if final_status == DispatchStatus.SUCCESS
            else ManagedHeadlessSessionTerminalState.FAILED
        )
        try:
            set_lineage_terminal_state(
                tool_ctx,
                managed_lineage_ref,
                terminal_state,
            )
        except Exception:
            logger.warning(
                "failed to record managed lineage terminal state",
                dispatch_name=effective_name,
                terminal_state=terminal_state.value,
                exc_info=True,
            )

    _branch_name = ""
    if sidecar_entries and tool_ctx.runner is not None:
        for _entry in sidecar_entries:
            if _entry.pr_url:
                try:
                    _pr_info = await tool_ctx.runner(
                        ["gh", "pr", "view", _entry.pr_url, "--json", "headRefName"],
                        cwd=tool_ctx.project_dir,
                        timeout=15,
                    )
                    if _pr_info.returncode == 0 and _pr_info.stdout:
                        import json as _json  # noqa: PLC0415

                        _branch_name = _json.loads(_pr_info.stdout).get("headRefName", "")
                except Exception:
                    logger.debug("branch_name_extraction_failed", exc_info=True)
                break

    _labels_cleaned = False
    if final_status not in (DispatchStatus.SUCCESS, DispatchStatus.RESUMABLE):
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

        provenance.start(
            DispatchEffectName.LABEL_CLEANUP,
            identities={"dispatch_id": dispatch_id},
        )
        _labels_cleaned = await cleanup_orphaned_labels(
            dispatch_sidecar_path, tool_ctx.github_client, issue_url=_issue_urls_raw
        )
        provenance.record_labels_cleanup(confirmed=_labels_cleaned)
        if _labels_cleaned:
            provenance.confirm(
                DispatchEffectName.LABEL_CLEANUP,
                receipt="label cleanup helper confirmed cleanup",
                identities={"dispatch_id": dispatch_id},
            )
        else:
            provenance.mark_ambiguous(
                DispatchEffectName.LABEL_CLEANUP,
                evidence="label cleanup helper did not confirm cleanup",
                identities={"dispatch_id": dispatch_id},
            )

    project_log_dir = ""
    if _locator is not None:
        try:
            project_log_dir = str(_locator.project_log_dir(str(tool_ctx.project_dir)))
        except OSError:
            logger.warning("project_log_dir_unavailable", exc_info=True)

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

    if final_status == DispatchStatus.SUCCESS:
        provenance.start(
            DispatchEffectName.COMMIT,
            identities={
                "dispatch_id": dispatch_id,
                "dispatched_session_id": skill_result.session_id or "",
            },
        )
        provenance.confirm(
            DispatchEffectName.COMMIT,
            receipt="dispatch outcome classifier confirmed success",
            identities={
                "dispatch_id": dispatch_id,
                "dispatched_session_id": skill_result.session_id or "",
            },
        )

    record = DispatchRecord(
        name=effective_name,
        status=final_status,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        caller_session_id=caller_session_id,
        caller_backend_name=_caller_backend_name,
        dispatched_session_id=_dispatched_session_id[0]
        if _dispatched_session_id
        else skill_result.session_id,
        session_chain=extended_chain,
        dispatched_session_log_dir=project_log_dir,
        dispatched_pid=_dispatched_pid[0] if _dispatched_pid else 0,
        dispatched_starttime_ticks=_dispatched_ticks[0] if _dispatched_ticks else 0,
        dispatched_boot_id=_dispatched_boot_id[0] if _dispatched_boot_id else "",
        dispatched_create_time=_dispatched_create_time[0] if _dispatched_create_time else 0.0,
        reason=reason,
        retry_reason=skill_result.retry_reason or "",
        infra_exit_category=skill_result.infra.exit_category or "",
        token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
        started_at=started_at,
        ended_at=ended_at,
        sidecar_path=dispatch_sidecar_path,
        labels_cleaned=_labels_cleaned,
        issue_url=_issue_urls_raw,
        branch_name=_branch_name,
        backend_name=_effective_backend.name if _effective_backend else "",
        resume_checkpoint=_checkpoint_to_dict(dispatch_checkpoint),
        effect_provenance=provenance.snapshot().to_dict(),
        managed_lineage_ref=managed_lineage_ref,
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

    provenance.start(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        identities={"dispatch_id": dispatch_id, "state_path": state_path},
    )
    upsert_dispatch_record_by_name(state_path, record)
    if extracted:
        write_captured_values(state_path, extracted)
    provenance.confirm(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        receipt="per-dispatch state and captures persisted",
        identities={"dispatch_id": dispatch_id, "state_path": state_path},
    )
    record.effect_provenance = provenance.snapshot().to_dict()
    upsert_dispatch_record_by_name(state_path, record)
    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    return build_dispatch_result(
        parsed_result=parsed_result,
        final_status=final_status,
        reason=reason,
        dispatch_id=dispatch_id,
        skill_result=skill_result,
        dispatch_checkpoint=dispatch_checkpoint,
        started_at=started_at,
        ended_at=ended_at,
        state_path=state_path,
        effect_provenance=provenance.snapshot(),
    )
