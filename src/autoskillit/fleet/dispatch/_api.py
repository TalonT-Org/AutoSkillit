"""Fleet dispatch engine entry point — Phase-split orchestrator (#4851).

This module decomposes the legacy ``fleet/_api.py::_run_dispatch`` into a thin
orchestrator that composes the per-phase shards:

* Phase A — pre-launch gating (``_validation.run_pre_launch_gating``)
* Phase B — identity, lineage, launch preparation (``_lineage.run_lineage_preparation``)
* Orchestrator — tracker-lease retention (this module)
* Phase C — execution monitoring + callbacks (``_execution.run_execution``)
* Phase D — cancellation/exception/finally cleanup (``_cleanup.handle_*``)
* Phase E — outcome classification + state finalization

The closure-scoped state that the legacy function captured (``_dispatched_pid``,
``_spawn_error``, ``_dispatch_completed_normally``, etc.) is now threaded
explicitly via ``SpawnContext`` and ``ExecutionResult`` records.

Public surface — ``DispatchSpawnFailed``, ``execute_dispatch`` — is re-exported
from ``fleet/_api.py`` (the public-API facade).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CaptureEntrySpec,
    FleetErrorCode,
    get_logger,
    release_tracker_lease,
)
from autoskillit.core._managed_worker_capacity import ManagedWorkerCapacityError
from autoskillit.fleet import state as _fleet_state
from autoskillit.fleet._outcome import (
    _sanitize_managed_capture_diagnostics,
)
from autoskillit.fleet._startup_warm import warm_failure_path_imports
from autoskillit.fleet.dispatch._classification import (
    finalize_state_write,
    run_outcome_classification,
)
from autoskillit.fleet.dispatch._cleanup import (
    handle_cancellation,
    handle_generic_exception,
    run_finally_label_cleanup,
)
from autoskillit.fleet.dispatch._errors import complete_failure_with_state
from autoskillit.fleet.dispatch._execution import ExecutionResult as _ExecResult
from autoskillit.fleet.dispatch._execution import SpawnContext, run_execution
from autoskillit.fleet.dispatch._lineage import run_lineage_preparation
from autoskillit.fleet.dispatch._validation import run_pre_launch_gating
from autoskillit.fleet.sidecar import sidecar_path
from autoskillit.fleet.state import DispatchRecord, DispatchStatus
from autoskillit.fleet.state_effects import (
    DispatchAggregatePhase,
    DispatchProvenanceTracker,
)
from autoskillit.fleet.state_outcomes import (
    DispatchCompleted,
    DispatchRejected,
    DispatchResult,
)

if TYPE_CHECKING:
    from autoskillit.core import (
        CodingAgentBackend,
        NativeShellCaptureMode,
        SessionCheckpoint,
    )
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)


class DispatchSpawnFailed(RuntimeError):
    """Spawn-time failure signal for the execute_dispatch callback path.

    Raised when a dispatch cannot complete its initialization callback. Carries
    the structured ``error_code`` so callers can branch on the specific failure
    mode without parsing the message text.

    Attributes:
        error_code: The structured ``FleetErrorCode`` describing why the
            dispatch could not be spawned.
        message: Human-readable detail string for diagnostic logs.
    """

    def __init__(self, error_code: FleetErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


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

    Composes: lock → validate → quota → prompt → dispatch → parse → state → cleanup.
    Returns DispatchResult wrapping the outcome plus the per-dispatch state path.
    """
    from autoskillit.fleet._capture import _normalize_capture_spec

    warm_failure_path_imports()
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

    capacity = tool_ctx.worker_capacity
    if capacity is None:
        return _reject(
            error_code=FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED,
            message="Managed worker capacity is not initialized — open_kitchen with fleet mode.",
        )
    if capacity.at_capacity():
        return _reject(
            error_code=FleetErrorCode.FLEET_PARALLEL_REFUSED,
            message=(
                f"Fleet at capacity ({capacity.active_count}/{capacity.max_concurrent}"
                " dispatches running)."
            ),
        )

    try:
        # Call ``_run_dispatch`` through the public facade so that
        # ``monkeypatch.setattr("autoskillit.fleet._api._run_dispatch", ...)``
        # patches reach this call site (Tier-1 test patch preservation).
        from autoskillit.fleet import _api as _facade  # noqa: PLC0415

        return await _facade._run_dispatch(
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
        underlying: BaseException = exc
        if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
            underlying = exc.exceptions[0]
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
            state_path_obj = (
                Path(identities["state_path"]) if identities.get("state_path") else None
            )
            if state_path_obj is not None:
                try:
                    _fleet_state.append_dispatch_record(
                        state_path_obj,
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
                    state_path_obj = None
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
                per_dispatch_state_path=state_path_obj,
            )
        return _reject(
            error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            message=(
                sanitized_failure_text
                if sanitized_failure_text
                else "Food-truck dispatch failed during startup."
            ),
        )


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
    """Inner dispatch body — acquires capacity after durable dispatch identity.

    Composes the per-phase shards (validation → lineage → execution →
    classification → finalize).  Capacity is acquired only after Phase B has
    persisted ``ready.dispatch_id`` so the owner key is durable and immutable.
    """
    provenance = provenance or DispatchProvenanceTracker()
    # --- Phase A: pre-launch gating ---
    gating_result = await run_pre_launch_gating(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task=task,
        ingredients=ingredients,
        dispatch_name=dispatch_name,
        dispatch_backend=dispatch_backend,
        effective_backend_map=effective_backend_map,
        provenance=provenance,
    )
    if isinstance(gating_result, DispatchResult):
        return gating_result
    recipe_ctx = gating_result

    # --- Phase B: identity + lineage + launch preparation ---
    lineage_result = await run_lineage_preparation(
        tool_ctx=tool_ctx,
        recipe=recipe,
        recipe_obj=recipe_ctx.recipe_obj,  # type: ignore[arg-type]
        task=task,
        effective_ingredients=recipe_ctx.effective_ingredients,
        effective_name=recipe_ctx.effective_name,
        full_recipe=recipe_ctx.full_recipe,
        effective_backend=recipe_ctx.effective_backend,
        caller_backend_name=recipe_ctx.caller_backend_name,
        dispatch_name=dispatch_name,
        prompt_builder=prompt_builder,
        quota_checker=quota_checker,
        capture=capture,
        resume_session_id=resume_session_id,
        resume_checkpoint=resume_checkpoint,
        resume_message=resume_message,
        caller_instructions=caller_instructions,
        prior_dispatch_id=prior_dispatch_id,
        caller_session_id=caller_session_id,
        provenance=provenance,
        native_shell_capture_mode=native_shell_capture_mode,
        timeout_sec=timeout_sec,
    )
    if isinstance(lineage_result, DispatchResult):
        return lineage_result
    if lineage_result.outcome == "prior_success_short_circuit":
        return lineage_result.prior_success_dispatch_result  # type: ignore[return-value]

    ready = lineage_result.ready
    if ready is None:
        # Defensive: outcome == "prior_success_short_circuit" already returned above;
        # any other outcome should produce a non-None ``ready``.
        raise RuntimeError(
            f"LineagePreparationResult.outcome={lineage_result.outcome!r} "
            "produced a None ready record"
        )

    # --- Orchestrator: tracker-lease retention ---
    # Access via the public facade (not the local top-level import) so that
    # ``monkeypatch.setattr("autoskillit.fleet._api.retain_dispatch_tracker_authority", ...)``
    # patches observed by tests reach this call site.
    from autoskillit.fleet import _api as _facade  # noqa: PLC0415

    tracker_key, tracker_lease = _facade.retain_dispatch_tracker_authority(
        tool_ctx, ready.dispatch_id
    )

    # --- Orchestrator: prepare_resume chokepoint (Tier-2 universal coverage) ---
    # ``prepare_resume`` is also called from ``run_lineage_preparation`` via the
    # ``resume_preparer`` closure, but the universal-coverage AST guard
    # ``tests/fleet/test_resume_precondition.py::TestPrepareResumeIsUniversal``
    # walks ``_run_dispatch`` directly and requires a top-level Call node
    # targeting ``prepare_resume``. This call is a no-op when the state file
    # is missing/corrupt (returns ``None``) and preserves the chokepoint
    # semantics from the legacy implementation.
    from autoskillit.fleet.state_recovery import (  # noqa: PLC0415
        prepare_resume,
    )

    prepare_resume(ready.state_path, recipe_ctx.effective_name)

    # --- Spawn context: closure-scoped state threaded across phases ---
    from autoskillit.fleet._issue_url_helpers import extract_issue_urls  # noqa: PLC0415

    spawn_ctx = SpawnContext(
        issue_urls_raw=extract_issue_urls(recipe_ctx.effective_ingredients),
        prior_ids=ready.prior_session_chain[:],
    )
    prior_markers: list[str | None] | None = (
        [f"%%L3_DONE::{pid[:8]}%%" for pid in spawn_ctx.prior_ids] if spawn_ctx.prior_ids else None
    )

    capacity = tool_ctx.worker_capacity
    if capacity is None:
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED,
            message="Managed worker capacity is not initialized.",
            dispatch_id=ready.dispatch_id,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            tool_ctx=tool_ctx,
        )
    try:
        permit = await capacity.acquire(ready.dispatch_id)
    except TimeoutError:
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_ACQUIRE_TIMEOUT,
            message=(
                f"Timed out waiting for managed worker capacity after {capacity.timeout}s "
                f"({capacity.active_count}/{capacity.max_concurrent} dispatches running)."
            ),
            dispatch_id=ready.dispatch_id,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            tool_ctx=tool_ctx,
        )
    except ManagedWorkerCapacityError as exc:
        # New failure mode introduced when FleetSemaphore was replaced by
        # ManagedWorkerCapacity: foreign/duplicate owner and owner-already-holds
        # permits surface as ManagedWorkerCapacityError rather than TimeoutError.
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_HARD_REFUSAL_HEADLESS,
            message=str(exc),
            dispatch_id=ready.dispatch_id,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            tool_ctx=tool_ctx,
        )
    except asyncio.CancelledError:
        raise

    # --- Phase C + Phase D + Phase E in outer try/except/finally ---
    # ``execution`` is bound to ``None`` before the try block so the finally
    # clause can inspect its flag without an unbound-variable error in the
    # rare cancel-before-execution case.
    execution_result: _ExecResult | None = None
    dispatch_completed_normally = False
    try:
        execution_result = await run_execution(
            tool_ctx=tool_ctx,
            spawn_ctx=spawn_ctx,
            dispatch_id=ready.dispatch_id,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            capture_decision=ready.capture_decision,
            resume_session_id=ready.resume_session_id,
            resume_checkpoint=ready.resume_checkpoint,
            resume_message=ready.resume_message,
            prompt=ready.launch_tuple[0],
            plugin_authority=ready.launch_tuple[1],
            capability_preparation=ready.launch_tuple[2],
            preflight=ready.preflight,
            full_recipe=recipe_ctx.full_recipe,
            provenance=provenance,
            started_at=time.time(),
            prior_session_chain=ready.prior_session_chain,
            effective_backend=recipe_ctx.effective_backend,
            caller_session_id=caller_session_id,
            caller_backend_name=recipe_ctx.caller_backend_name,
            idle_output_timeout=idle_output_timeout,
            lineage_backend_name=ready.lineage_backend_name,
            dispatch_sidecar_path=str(sidecar_path(ready.dispatch_id, tool_ctx.project_dir)),
            issue_urls_raw=spawn_ctx.issue_urls_raw,
            prior_ids=spawn_ctx.prior_ids,
            prior_completion_markers=prior_markers,
            dispatch_backend=dispatch_backend,
            completion_marker=ready.identity.completion_marker,
            sentinel_contract=ready.identity.sentinel_contract,
            dispatches_dir=ready.dispatches_dir,
            resolved_timeout=ready.resolved_timeout,
        )
        if execution_result is None:
            raise RuntimeError("run_execution returned None — Phase B/C contract violation")
        if execution_result.spawn_failure_dispatch_result is not None:
            return execution_result.spawn_failure_dispatch_result

        skill_result = execution_result.skill_result
        ended_at = execution_result.ended_at
        if skill_result is None:
            raise RuntimeError(
                "run_execution returned skill_result=None without a "
                "spawn_failure_dispatch_result — Phase B/C contract violation"
            )
        if execution_result.dispatch_completed_normally:
            dispatch_completed_normally = True

        # --- Phase E: outcome classification + state finalization ---
        classification = await run_outcome_classification(
            skill_result=skill_result,
            spawn_ctx=spawn_ctx,
            tool_ctx=tool_ctx,
            tracker_lease=tracker_lease,
            dispatch_id=ready.dispatch_id,
            effective_name=recipe_ctx.effective_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            recipe=recipe,
            prior_session_chain=ready.prior_session_chain,
            prior_dispatched_session_id=ready.prior_dispatched_session_id,
            resume_session_id=ready.resume_session_id,
            dispatch_checkpoint=ready.resume_checkpoint,
            marker_dir=execution_result.marker_dir,
            effective_backend=recipe_ctx.effective_backend,
            dispatch_sidecar_path=execution_result.dispatch_sidecar_path,
        )
        result = await finalize_state_write(
            classification=classification,
            spawn_ctx=spawn_ctx,
            tool_ctx=tool_ctx,
            dispatch_id=ready.dispatch_id,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            campaign_id=tool_ctx.kitchen_id,
            caller_session_id=caller_session_id,
            caller_backend_name=recipe_ctx.caller_backend_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            capture=capture,
            dispatch_checkpoint=ready.resume_checkpoint,
            started_at=execution_result.started_at,
            ended_at=ended_at or time.time(),
            cache_invalidator=cache_invalidator,
            quota_refresher=quota_refresher,
            effective_backend_name=ready.lineage_backend_name,
        )
        return result
    except asyncio.CancelledError:
        await handle_cancellation(
            spawn_ctx=spawn_ctx,
            tool_ctx=tool_ctx,
            effective_name=recipe_ctx.effective_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            marker_dir=execution_result.marker_dir if execution_result is not None else None,
            state_path=ready.state_path,
        )
        raise
    except Exception:
        await handle_generic_exception(
            tool_ctx=tool_ctx,
            managed_lineage_ref=ready.managed_lineage_ref,
        )
        raise
    finally:
        # Two-step cleanup:
        # 1. Label cleanup FIRST (if dispatch did not complete normally) — preserves
        #    the source's nesting where the inner finally: runs before the outer
        #    tracker-lease release.
        # 2. Tracker-lease release SECOND — always under the leases lock.
        try:
            if not dispatch_completed_normally:
                await run_finally_label_cleanup(
                    spawn_ctx=spawn_ctx,
                    dispatch_id=ready.dispatch_id,
                    dispatch_sidecar_path=str(
                        sidecar_path(ready.dispatch_id, tool_ctx.project_dir)
                    ),
                    tool_ctx=tool_ctx,
                    provenance=provenance,
                )
            with tool_ctx.tracker_leases_lock:
                release_tracker_lease(tool_ctx.tracker_leases, tracker_key)
        finally:
            capacity.release(permit)
