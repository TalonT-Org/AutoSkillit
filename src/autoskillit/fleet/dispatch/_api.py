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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    FleetErrorCode,
    get_logger,
    release_tracker_lease,
)
from autoskillit.fleet._checkpoint_bridge import retain_dispatch_tracker_authority
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
from autoskillit.fleet.dispatch._execution import ExecutionResult as _ExecResult
from autoskillit.fleet.dispatch._execution import SpawnContext, run_execution
from autoskillit.fleet.dispatch._lineage import run_lineage_preparation
from autoskillit.fleet.dispatch._validation import run_pre_launch_gating
from autoskillit.fleet.state_types import (
    DispatchAggregatePhase,
    DispatchCompleted,
    DispatchProvenanceTracker,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
)

if TYPE_CHECKING:
    from exceptiongroup import ExceptionGroup

    from autoskillit.core import (
        CodingAgentBackend,
        NativeShellCaptureMode,
        SessionCheckpoint,
    )
    from autoskillit.pipeline.context import ToolContext

_logger = get_logger(__name__)


class DispatchSpawnFailed(RuntimeError):
    """Raised when a dispatch cannot complete its initialization."""

    def __init__(self, error_code: FleetErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass
class _ExecuteDispatchOutcomes:
    """Return-tuple mirroring the legacy execute_dispatch's contract:

    Same DispatchResult envelope, same per-dispatch state path semantics.
    """

    dispatch_result: DispatchResult


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
        underlying: BaseException = exc
        if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
            underlying = exc.exceptions[0]
        _logger.warning(
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
                    from autoskillit.fleet.state import (  # noqa: PLC0415
                        DispatchRecord,
                        append_dispatch_record,
                    )

                    append_dispatch_record(
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
                    _logger.warning(
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
    finally:
        lock.release()


# ``ExceptionGroup`` is a stdlib symbol from Python 3.11+; we alias it from
# ``exceptiongroup`` so the type-only reference resolves on 3.10.

try:
    from exceptiongroup import ExceptionGroup  # type: ignore[attr-defined]
except ImportError:
    ExceptionGroup = BaseExceptionGroup  # type: ignore[assignment,misc]


async def _run_dispatch(
    *,
    tool_ctx: ToolContext,
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None,
    dispatch_name: str | None,
    timeout_sec: int | None,
    prompt_builder: Callable[..., str],
    quota_checker: Callable[..., Any],
    quota_refresher: Callable[..., Any],
    cache_invalidator: Callable[[str], None] | None,
    capture: dict[str, Any] | None,
    resume_session_id: str | None,
    resume_checkpoint: SessionCheckpoint | None,
    idle_output_timeout: int | None,
    caller_session_id: str,
    prior_dispatch_id: str | None,
    resume_message: str | None,
    caller_instructions: str | None,
    dispatch_backend: CodingAgentBackend | None,
    effective_backend_map: dict[str, str] | None,
    provenance: DispatchProvenanceTracker,
    native_shell_capture_mode: NativeShellCaptureMode | None,
) -> DispatchResult:
    """Inner dispatch body — composed of phase shards.

    For a high-level overview of which lines each phase owns, see the file
    docstring. The transaction-ordering contract is enumerated in issue #4851.
    """
    # --- Phase A: pre-launch gating ---
    gating_result = await run_pre_launch_gating(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task=task,
        ingredients=ingredients,
        dispatch_name=dispatch_name,
        prior_dispatch_id=prior_dispatch_id,
        resume_session_id=resume_session_id,
        native_shell_capture_mode=native_shell_capture_mode,
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
        idle_output_timeout=idle_output_timeout,
        caller_session_id=caller_session_id,
        dispatch_backend=dispatch_backend,
        effective_backend_map=effective_backend_map,
        provenance=provenance,
        native_shell_capture_mode=native_shell_capture_mode,
        timeout_sec=timeout_sec,
    )
    if isinstance(lineage_result, DispatchResult):
        return lineage_result
    if lineage_result.outcome == "prior_success_short_circuit":
        return lineage_result.prior_success_dispatch_result  # type: ignore[return-value]

    ready = lineage_result.ready
    assert ready is not None  # narrowed by outcome check above

    # --- Orchestrator: tracker-lease retention ---
    tracker_key, tracker_lease = retain_dispatch_tracker_authority(tool_ctx, ready.dispatch_id)

    # --- Spawn context: closure-scoped state threaded across phases ---
    spawn_ctx = SpawnContext(
        issue_urls_raw="",  # populated below
        prior_ids=[],
    )

    # Populate prior_ids, issue_urls_raw from the recipe context + lineage prep
    from autoskillit.fleet._issue_url_helpers import extract_issue_urls  # noqa: PLC0415

    spawn_ctx.issue_urls_raw = extract_issue_urls(recipe_ctx.effective_ingredients)
    spawn_ctx.prior_ids = ready.prior_session_chain[:]
    prior_markers: list[str | None] | None = (
        [f"%%L3_DONE::{pid[:8]}%%" for pid in spawn_ctx.prior_ids] if spawn_ctx.prior_ids else None
    )

    # --- Phase C + Phase D + Phase E in outer try/except/finally ---
    # ``execution`` is bound to ``None`` before the try block so the finally
    # clause can inspect its flag without an unbound-variable error in the
    # rare cancel-before-execution case.
    execution: _ExecResult | None = None
    try:
        execution = await run_execution(
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
            authoritative_cwd=ready.launch_tuple[3],
            preflight=ready.preflight,
            full_recipe=recipe_ctx.full_recipe,
            provenance=provenance,
            started_at=__import__("time").time(),
            prior_session_chain=ready.prior_session_chain,
            prior_dispatched_session_id=ready.prior_dispatched_session_id,
            effective_backend=recipe_ctx.effective_backend,
            caller_session_id=caller_session_id,
            idle_output_timeout=idle_output_timeout,
            lineage_backend_name=ready.lineage_backend_name,
            dispatch_sidecar_path=str(Path(ready.handle.state_path) / "sidecar.jsonl"),
            issue_urls_raw=spawn_ctx.issue_urls_raw,
            prior_ids=spawn_ctx.prior_ids,
            prior_completion_markers=prior_markers,
            dispatch_backend=dispatch_backend,
        )
        assert execution is not None  # narrowed by the await above
        if execution.spawn_failure_dispatch_result is not None:
            return execution.spawn_failure_dispatch_result

        skill_result = execution.skill_result
        ended_at = execution.ended_at
        assert skill_result is not None

        # --- Phase E: outcome classification + state finalization ---
        classification = await run_outcome_classification(
            skill_result=skill_result,
            spawn_ctx=spawn_ctx,
            tool_ctx=tool_ctx,
            tracker_lease=tracker_lease,
            dispatch_id=ready.dispatch_id,
            state_path=ready.state_path,
            effective_name=recipe_ctx.effective_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            capture=capture,
            full_recipe=recipe_ctx.full_recipe,
            lineage_backend_name=ready.lineage_backend_name,
            caller_session_id=caller_session_id,
            caller_backend_name=recipe_ctx.caller_backend_name,
            recipe=recipe,
            prior_session_chain=ready.prior_session_chain,
            prior_dispatched_session_id=ready.prior_dispatched_session_id,
            resume_session_id=ready.resume_session_id,
            idle_output_timeout=idle_output_timeout,
            dispatch_checkpoint=ready.preflight.checkpoint
            if ready.preflight is not None
            else None,
            ended_at=ended_at or __import__("time").time(),
            started_at=execution.ended_at or 0.0,
            marker_dir=None,
            effective_backend=recipe_ctx.effective_backend,
        )
        result = await finalize_state_write(  # type: ignore[call-arg]
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
            dispatch_checkpoint=ready.preflight.checkpoint
            if ready.preflight is not None
            else None,
            started_at=execution.ended_at or 0.0,
            ended_at=ended_at or __import__("time").time(),
            cache_invalidator=cache_invalidator,
            quota_refresher=quota_refresher,
            effective_backend_name=ready.lineage_backend_name,
        )
        return result  # type: ignore[return-value]
    except asyncio.CancelledError:
        await handle_cancellation(
            spawn_ctx=spawn_ctx,
            tool_ctx=tool_ctx,
            dispatch_id=ready.dispatch_id,
            effective_name=recipe_ctx.effective_name,
            managed_lineage_ref=ready.managed_lineage_ref,
            provenance=provenance,
            dispatch_sidecar_path=str(Path(ready.handle.state_path) / "sidecar.jsonl"),
            marker_dir=None,
            skill_result=None,
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
        if execution is not None and not execution.dispatch_completed_normally:  # noqa: F821
            await run_finally_label_cleanup(
                spawn_ctx=spawn_ctx,
                dispatch_id=ready.dispatch_id,
                dispatch_sidecar_path=str(Path(ready.handle.state_path) / "sidecar.jsonl"),
                tool_ctx=tool_ctx,
                provenance=provenance,
            )
        with tool_ctx.tracker_leases_lock:
            release_tracker_lease(tool_ctx.tracker_leases, tracker_key)
