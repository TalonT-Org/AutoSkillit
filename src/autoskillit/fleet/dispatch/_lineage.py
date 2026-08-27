"""Phase B — dispatch identity, lineage preparation & launch tuple assembly (#4851).

Owns the per-dispatch state handle creation, the resume/prior-success
short-circuit, the captured-ingredient interpolation, the launch tuple
preparation, and the call into
``prepare_food_truck_lineage``.

Returns a ``LineagePreparationResult`` consumed by Phase C. Either a
``prior_success_short_circuit`` outcome (the prior dispatch already succeeded
and can be mirrored without launching a subprocess) or a ``ready`` outcome
carrying every value Phase C needs to spawn the executor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from autoskillit.core import (
    CaptureEntrySpec,
    CodingAgentBackend,
    FleetErrorCode,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    SessionCheckpoint,
    get_logger,
)
from autoskillit.fleet._expressions import _CAMPAIGN_REF_RE, _interpolate_campaign_refs
from autoskillit.fleet._native_shell_capture import (
    FoodTruckLineageInitializationError,
    prepare_dispatch_identity,
    prepare_food_truck_lineage,
    resolve_dispatch_timeout,
)
from autoskillit.fleet._outcome import build_success_short_circuit as _build_success_short_circuit
from autoskillit.fleet.dispatch._errors import complete_failure_with_state
from autoskillit.fleet.state import (
    DispatchIdentity,
    DispatchRecord,
    DispatchStateHandle,
    read_all_campaign_captures,
)
from autoskillit.fleet.state_recovery import ResumePreflight, prepare_resume
from autoskillit.fleet.state_types import (
    DispatchEffectName,
    DispatchProvenanceTracker,
    DispatchResult,
)
from autoskillit.workspace import default_skill_resolver, prepare_skill_projection

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)


@dataclass
class ReadyLineage:
    """Phase B output for the normal (non-prior-success) path — consumed by Phase C."""

    handle: DispatchStateHandle
    identity: DispatchIdentity
    dispatch_id: str
    state_path: Path
    capture_decision: Any
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None
    preflight: Any
    resume_session_id: str | None
    resume_checkpoint: SessionCheckpoint | None
    resume_message: str | None
    prior_session_chain: list[str]
    prior_dispatched_session_id: str | None
    lineage_backend_name: str
    launch_tuple: tuple[str, Any, Any, Path]
    # Resolved timeout (in seconds, float) from resolve_dispatch_timeout — Phase C
    # must forward this to the executor; falling back to 0.0 makes the dispatch
    # immediately stale.
    resolved_timeout: float
    # Authority for the heartbeats-and-marker parent directory. Computed once in
    # Phase B so Phase C can open ``_dispatch_heartbeat`` against the same root.
    dispatches_dir: Path


@dataclass
class LineagePreparationResult:
    """Phase B output envelope — consumed by Phase C."""

    outcome: Literal["prior_success_short_circuit", "ready"]
    prior_success_dispatch_result: DispatchResult | None
    ready: ReadyLineage | None


def create_fresh_handle(
    *,
    dispatches_dir: Path,
    campaign_id: str,
    effective_name: str,
    caller_session_id: str,
    caller_backend_name: str,
    recipe_snapshot: dict,
) -> DispatchStateHandle:
    """Build a brand-new per-dispatch state handle with the caller record seeded in.

    Phase B no longer threads closure-captured variables as closure cells —
    the orchestrator passes them in as keyword arguments.
    """
    return DispatchStateHandle.create_fresh(
        dispatches_dir,
        campaign_id,
        effective_name,
        "",
        [
            DispatchRecord(
                name=effective_name,
                caller_session_id=caller_session_id,
                caller_backend_name=caller_backend_name,
            ),
        ],
        recipe_snapshot,
    )


def prepare_launch(
    *,
    for_dispatch_id: str,
    prompt_builder: Callable[..., str],
    recipe: str,
    task: str,
    effective_ingredients: dict[str, str],
    capture: dict | None,
    caller_instructions: str | None,
    l3_timeout_sec: int | None,
    campaign_id: str,
    effective_backend: CodingAgentBackend | None,
    tool_ctx: ToolContext,
) -> tuple[str, Any, Any, Path]:
    """Build the (prompt, plugin_authority, capability_preparation, authoritative_cwd) tuple.

    Phase B no longer threads closure-captured variables as closure cells —
    the orchestrator passes them in as keyword arguments.

    Note: ``l3_timeout_sec`` is passed through ``int(...)`` to mirror the
    original ``int(resolved_timeout)`` coercion. The original closure always
    received a non-None ``resolved_timeout`` (Phase B resolves it from the
    ``timeout_sec`` parameter + config default above), but we accept ``None``
    here as a defensive fallback that yields ``0`` — matching the original
    behavior of always calling ``int(...)`` on the resolved value.
    """
    prepared_prompt = prompt_builder(
        recipe=recipe,
        task=task,
        ingredients=effective_ingredients,
        dispatch_id=for_dispatch_id,
        campaign_id=campaign_id,
        l3_timeout_sec=int(l3_timeout_sec) if l3_timeout_sec is not None else 0,
        capture=capture,
        caller_instructions=caller_instructions,
    )
    plugin_authority = capability_preparation = None
    if effective_backend is not None:
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
        capability_preparation.cwd if capability_preparation is not None else tool_ctx.project_dir
    ).resolve()
    return (
        prepared_prompt,
        plugin_authority,
        capability_preparation,
        authoritative_cwd,
    )


async def run_lineage_preparation(
    *,
    tool_ctx: ToolContext,
    recipe: str,
    recipe_obj: Any,  # RecipeInfo — mirrors the local from `_run_dispatch`
    task: str,
    effective_ingredients: dict[str, str],
    effective_name: str,
    full_recipe: Recipe,
    effective_backend: CodingAgentBackend | None,
    caller_backend_name: str,
    dispatch_name: str | None,
    prompt_builder: Callable[..., str],
    quota_checker: Callable[..., Any],
    capture: dict[str, CaptureEntrySpec] | None,
    resume_session_id: str | None,
    resume_checkpoint: SessionCheckpoint | None,
    resume_message: str | None,
    caller_instructions: str | None,
    prior_dispatch_id: str | None,
    caller_session_id: str,
    provenance: DispatchProvenanceTracker,
    native_shell_capture_mode: NativeShellCaptureMode | None,
    timeout_sec: int | None,
) -> LineagePreparationResult | DispatchResult:
    """Mint the per-dispatch state handle, prepare the managed lineage, build the launch tuple.

    All closure-scoped variables from the legacy ``_create_fresh_handle`` and
    ``_prepare_launch`` closures are now explicit keyword arguments to the
    free functions ``create_fresh_handle`` and ``prepare_launch`` defined in
    this module.

    ``managed_lineage_ref`` is threaded via the local variable: every call to
    ``complete_failure_with_state`` from this Phase passes
    ``managed_lineage_ref=None`` because the assignment at the end of the
    function has not fired yet — the original closure's free-variable lookup
    always read the latest value, but the only caller that would have
    observed a non-None ``managed_lineage_ref`` was the catch-block that
    fires BEFORE the post-try assignment.
    """
    effective_name = dispatch_name or effective_name
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

    identity_preparation = prepare_dispatch_identity(
        create_fresh_handle=lambda: create_fresh_handle(
            dispatches_dir=dispatches_dir,
            campaign_id=campaign_id,
            effective_name=effective_name,
            caller_session_id=caller_session_id,
            caller_backend_name=caller_backend_name,
            recipe_snapshot=recipe_snapshot,
        ),
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
        return LineagePreparationResult(
            outcome="prior_success_short_circuit",
            prior_success_dispatch_result=_build_success_short_circuit(
                identity_preparation.prior_success_record,
                handle,
                provenance.snapshot(),
            ),
            ready=None,
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

    if effective_ingredients:
        unknown = set(effective_ingredients.keys()) - set(full_recipe.ingredients.keys())
        if unknown:
            return complete_failure_with_state(
                error_code=FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                message=(
                    f"Unknown ingredient keys: {sorted(unknown)}. "
                    f"Valid keys: {sorted(full_recipe.ingredients.keys())}"
                ),
                dispatch_id=dispatch_id,
                managed_lineage_ref=managed_lineage_ref,
                provenance=provenance,
                state_path=state_path,
                effective_name=effective_name,
                tool_ctx=tool_ctx,
            )

    missing_required = [
        key
        for key, ing in full_recipe.ingredients.items()
        if getattr(ing, "required", False)
        and getattr(ing, "default", None) is None
        and key not in effective_ingredients
    ]
    if missing_required:
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_MISSING_INGREDIENT,
            message=(
                f"Missing required ingredients: {sorted(missing_required)}. "
                f"These have no default and must be supplied."
            ),
            dispatch_id=dispatch_id,
            managed_lineage_ref=managed_lineage_ref,
            provenance=provenance,
            state_path=state_path,
            effective_name=effective_name,
            tool_ctx=tool_ctx,
        )

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
            return complete_failure_with_state(
                error_code=FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                message=str(exc),
                dispatch_id=dispatch_id,
                managed_lineage_ref=managed_lineage_ref,
                provenance=provenance,
                state_path=state_path,
                effective_name=effective_name,
                tool_ctx=tool_ctx,
            )

    quota_result = await quota_checker(tool_ctx.config.quota_guard)
    if quota_result.get("should_sleep"):
        await asyncio.sleep(quota_result.get("sleep_seconds", 0))

    resolved_timeout = resolve_dispatch_timeout(
        timeout_sec, tool_ctx.config.fleet.default_timeout_sec
    )
    if tool_ctx.executor is None:
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_MANIFEST_MISSING,
            message="Executor not configured.",
            dispatch_id=dispatch_id,
            managed_lineage_ref=managed_lineage_ref,
            provenance=provenance,
            state_path=state_path,
            effective_name=effective_name,
            tool_ctx=tool_ctx,
        )

    lineage_backend_name = (
        effective_backend.name
        if effective_backend is not None
        else (caller_backend_name or "unknown")
    )
    try:
        lineage_preparation = prepare_food_truck_lineage(
            tool_ctx=tool_ctx,
            identity_preparation=identity_preparation,
            launch=prepare_launch(
                for_dispatch_id=dispatch_id,
                prompt_builder=prompt_builder,
                recipe=recipe,
                task=task,
                effective_ingredients=effective_ingredients,
                capture=capture,
                caller_instructions=caller_instructions,
                l3_timeout_sec=int(resolved_timeout),
                campaign_id=campaign_id,
                effective_backend=effective_backend,
                tool_ctx=tool_ctx,
            ),
            prepare_launch=lambda for_dispatch_id: prepare_launch(
                for_dispatch_id=for_dispatch_id,
                prompt_builder=prompt_builder,
                recipe=recipe,
                task=task,
                effective_ingredients=effective_ingredients,
                capture=capture,
                caller_instructions=caller_instructions,
                l3_timeout_sec=int(resolved_timeout),
                campaign_id=campaign_id,
                effective_backend=effective_backend,
                tool_ctx=tool_ctx,
            ),
            create_fresh_handle=lambda: create_fresh_handle(
                dispatches_dir=dispatches_dir,
                campaign_id=campaign_id,
                effective_name=effective_name,
                caller_session_id=caller_session_id,
                caller_backend_name=caller_backend_name,
                recipe_snapshot=recipe_snapshot,
            ),
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
        return complete_failure_with_state(
            error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            message="Food-truck dispatch initialization failed.",
            dispatch_id=dispatch_id,
            managed_lineage_ref=managed_lineage_ref,
            provenance=provenance,
            state_path=state_path,
            effective_name=effective_name,
            tool_ctx=tool_ctx,
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
    capture_decision: NativeShellCaptureDecision = lineage_preparation.capture_decision
    managed_lineage_ref = lineage_preparation.managed_lineage_ref
    preflight: ResumePreflight | None = lineage_preparation.preflight
    resume_session_id = lineage_preparation.resume_session_id
    resume_checkpoint = lineage_preparation.resume_checkpoint
    resume_message = lineage_preparation.resume_message
    prior_session_chain = list(lineage_preparation.prior_session_chain)
    prior_dispatched_session_id = lineage_preparation.prior_dispatched_session_id

    return LineagePreparationResult(
        outcome="ready",
        prior_success_dispatch_result=None,
        ready=ReadyLineage(
            handle=handle,
            identity=identity,
            dispatch_id=dispatch_id,
            state_path=state_path,
            capture_decision=capture_decision,
            managed_lineage_ref=managed_lineage_ref,
            preflight=preflight,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            prior_session_chain=prior_session_chain,
            prior_dispatched_session_id=prior_dispatched_session_id,
            lineage_backend_name=lineage_backend_name,
            launch_tuple=(
                prompt,
                food_truck_plugin_authority,
                food_truck_capability_preparation,
                _lineage_anchor,
            ),
            resolved_timeout=resolved_timeout,
            dispatches_dir=dispatches_dir,
        ),
    )
