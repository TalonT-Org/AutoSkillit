"""MCP tool handlers: dispatch_food_truck, record_gate_dispatch."""

from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path

import anyio
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    CodingAgentBackend,
    FinalizedRecipeProjection,
    FinalizedRecipeStep,
    FleetErrorCode,
    NativeShellCaptureMode,
    SessionCheckpoint,
    fleet_error,
    get_logger,
    is_feature_enabled,
    new_managed_launch_id,
)
from autoskillit.fleet import (
    _INFRASTRUCTURE_FAILURE_REASONS,
    DispatchAggregatePhase,
    DispatchCompleted,
    DispatchEffectName,
    DispatchRecord,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
    evaluate_skip_when,
    find_completed_dispatch,
    has_blocking_dispatch,
    prepare_resume,
    read_all_campaign_captures,
    record_gate_outcome,
    resume_managed_join_parent_id,
    upsert_dispatch_record_by_name,
)
from autoskillit.pipeline import ToolContext
from autoskillit.server import mcp
from autoskillit.server._misc import resolve_backend_override, resolve_log_dir
from autoskillit.server._notify import track_response_size
from autoskillit.server.lifecycle._guards import _require_enabled
from autoskillit.server.lifecycle._session_scope import SCOPE_FLEET, session_scoped
from autoskillit.server.managed_join_prelaunch import (
    ManagedJoinIssuanceRefusal,
    acquire_managed_join_evidence,
)
from autoskillit.server.tools import (
    tools_fleet_dispatch,  # noqa: F401 — late-binding for monkeypatch reach
)
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._preflight import _check_dispatch_feasibility
from autoskillit.server.tools._serve_helpers import (
    build_backend_capabilities_map,
    pop_finalized_recipe_projection,
)
from autoskillit.server.tools.tools_fleet_dispatch._campaign_state import (
    _confirm_campaign_state_write,
    _dispatch_effect_identities,
    _get_food_truck_prompt_builder,
    _project_food_truck_sous_chef,
    _write_dispatch_to_campaign_state,
)
from autoskillit.server.tools.tools_fleet_dispatch._provenance import (
    _ACTIVE_DISPATCH_PROVENANCE,
    _bind_dispatch_provenance,
    _bound_dispatch_provenance,
    _dispatch_cancellation_response,
    _read_health_report,
)

logger = get_logger(__name__)

_MAX_CALLER_INSTRUCTIONS_LEN = 2000


def _finalized_recipe_steps(
    projection: FinalizedRecipeProjection,
) -> dict[str, FinalizedRecipeStep]:
    return {step.name: step for step in projection.ordered_steps}


def _load_preflight_projection(
    tool_ctx: ToolContext,
    recipe: str,
    ingredients: dict[str, str] | None,
    override_backend: CodingAgentBackend | None,
) -> tuple[dict[str, str] | None, FinalizedRecipeProjection | None]:
    """Load the projection used by the dispatch feasibility check."""
    effective_backend_map: dict[str, str] | None = None
    finalized_projection: FinalizedRecipeProjection | None = None
    if tool_ctx.recipes is None:
        return effective_backend_map, finalized_projection

    try:
        recipe_info = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
        raw_steps = (
            tool_ctx.recipes.load(recipe_info.path).steps if recipe_info is not None else None
        )
        effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
            raw_steps,
            override_backend.name if override_backend else None,
            recipe,
            config_backend=tool_ctx.config.agent_backend,
        )
        backend_capabilities_map = build_backend_capabilities_map(
            effective_backend_map, override_backend
        )
        load_result = tool_ctx.recipes.load_and_validate(
            recipe,
            tool_ctx.project_dir,
            suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
            ingredient_overrides=ingredients,
            temp_dir=tool_ctx.temp_dir,
            backend_name=override_backend.name if override_backend else None,
            effective_backend_map=effective_backend_map,
            backend_capabilities_map=backend_capabilities_map,
            include_finalized_projection=True,
        )
        if load_result.get("valid", False):
            finalized_projection = pop_finalized_recipe_projection(load_result)
    except Exception:
        logger.warning("dispatch_food_truck_preflight_load_failed", exc_info=True)

    return effective_backend_map, finalized_projection


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@session_scoped(SCOPE_FLEET)
@_bind_dispatch_provenance
@_cancellation_shield(
    state_factory=_bound_dispatch_provenance,
    state_context_var=_ACTIVE_DISPATCH_PROVENANCE,
    response_factory=_dispatch_cancellation_response,
)
@track_response_size("dispatch_food_truck")
async def dispatch_food_truck(
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None = None,
    dispatch_name: str | None = None,
    timeout_sec: int | None = None,
    capture: dict[str, str | dict[str, str]] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: dict[str, object] | None = None,
    idle_output_timeout: int | None = None,
    prior_dispatch_id: str | None = None,
    skip_when: str | None = None,
    resume_message: str | None = None,
    caller_instructions: str | None = None,
    backend: str | None = None,
    native_shell_capture_mode: NativeShellCaptureMode | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Dispatch a single food truck L2 session for one recipe.

    Spawns a headless subprocess that executes the given recipe with the
    provided task and ingredient overrides. Returns a JSON envelope with
    dispatch_id, dispatched_session_id, l3_payload, and token_usage.

    Args:
        recipe: Recipe name to dispatch (must be kind=standard).
        task: Task description for the L2 food truck session.
        ingredients: Optional ingredient overrides (all values must be strings).
        dispatch_name: Optional display name for the dispatch record.
        timeout_sec: Optional L2 session timeout override in seconds.
        capture: Optional dict mapping capture keys to "${{ result.field }}" templates.
            Extracted values are persisted in the campaign context for downstream
            dispatches to reference via "${{ campaign.key }}" in their ingredients.
        resume_checkpoint: Checkpoint dict from a prior RESUMABLE dispatch envelope.
            Pass the "resume_checkpoint" field from the prior result to inject completed
            items context into the resume prompt.
        skip_when: Optional condition expression. If evaluation against accumulated
            campaign captures returns true, the dispatch is recorded as SKIPPED without
            executing the recipe.
        resume_message: Optional caller-supplied context for the resumed session.
            Injected as a CALLER CONTEXT section in the resume prompt, allowing the
            caller to communicate changed conditions (e.g. "quota guard is now
            disabled") that the LLM should act on.
        caller_instructions: Optional free-text instructions from the dispatching caller to
            the food truck session. Injected as a CALLER INSTRUCTIONS section in
            the food truck system prompt. Use to forward user guidance such as "use model opus
            for the implement step" or "skip review if diff is under 20 lines."
            Only meaningful on fresh dispatches (not resumes). When None or empty,
            the L2 prompt is unchanged.
        native_shell_capture_mode: Optional typed launch mode. Omission defaults to
            managed capture. Long-lived server dispatch never consults ambient mode.

    Resume vs. Fresh Dispatch:
        - ``resume_session_id``: Session ID to resume (``--resume <id>``).
        - ``resume_checkpoint``: Completed-items context from the prior session.
        - ``resume_message``: Free-text caller context injected into the resume prompt.
        All three are optional and only meaningful when resuming a prior dispatch.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        provenance = _ACTIVE_DISPATCH_PROVENANCE.get()
        if caller_instructions and len(caller_instructions) > _MAX_CALLER_INSTRUCTIONS_LEN:
            caller_instructions = caller_instructions[:_MAX_CALLER_INSTRUCTIONS_LEN]

        dispatch_backend: CodingAgentBackend | None = None
        if backend is not None:
            try:
                dispatch_backend = resolve_backend_override(
                    backend,
                    launch_resolver=tool_ctx.launch_resolver,
                )
            except ValueError as exc:
                return fleet_error(
                    FleetErrorCode.FLEET_INVALID_BACKEND,
                    str(exc),
                )

        # Feature guard: config authority check independent of MCP visibility state.
        # Fleet sessions open the gate unconditionally at boot; this catch-all ensures
        # dispatch_food_truck never executes when features.fleet is disabled in config.
        if not is_feature_enabled(
            "fleet",
            tool_ctx.config.features,
            experimental_enabled=tool_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        provenance.start(
            DispatchEffectName.CAMPAIGN_PATH_CAPTURE,
            retry_relevant=False,
        )
        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        provenance.confirm(
            DispatchEffectName.CAMPAIGN_PATH_CAPTURE,
            receipt="campaign path environment captured",
            retry_relevant=False,
            identities={"campaign_state_path": campaign_state_path_str or ""},
        )
        continue_on_failure = (
            os.environ.get("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false").lower() == "true"
        )
        if campaign_state_path_str and not continue_on_failure:
            campaign_sp = Path(campaign_state_path_str)
            if dispatch_name:
                # L1 — Funnel the campaign precondition gate through the
                # single chokepoint.
                preflight = prepare_resume(campaign_sp, dispatch_name, continue_on_failure=False)
                if preflight is not None and preflight.halt:
                    return fleet_error(
                        FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                        preflight.halted_reason
                        or "Campaign halted: a prior dispatch failed and "
                        "continue_on_failure is false. "
                        "No further dispatches permitted.",
                    )
            # Probe the global blocking-dispatch set after preparing a named
            # resume so a different blocking dispatch still prevents execution.
            if has_blocking_dispatch(campaign_sp):
                return fleet_error(
                    FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                    "Campaign halted: a prior dispatch failed and "
                    "continue_on_failure is false. "
                    "No further dispatches permitted.",
                )

        from autoskillit.server._misc import (  # circular-break
            _refresh_quota_cache,
            invalidate_cache,
        )

        parsed_checkpoint = (
            SessionCheckpoint.from_dict(resume_checkpoint) if resume_checkpoint else None
        )
        _override_backend = dispatch_backend if dispatch_backend is not None else tool_ctx.backend
        provenance.start(
            DispatchEffectName.CALLER_IDENTITY,
            retry_relevant=False,
        )
        caller_session_id = tools_fleet_dispatch.find_caller_session_id(
            project_dir=tool_ctx.project_dir
        )
        provenance.confirm(
            DispatchEffectName.CALLER_IDENTITY,
            receipt="caller session identity resolved",
            retry_relevant=False,
            identities={"caller_session_id": caller_session_id},
        )
        effective_name = dispatch_name or recipe

        if campaign_state_path_str:
            prior_record = find_completed_dispatch(Path(campaign_state_path_str), effective_name)
            if prior_record is not None:
                provenance.confirm(
                    DispatchEffectName.PRIOR_DISPATCH_BINDING,
                    receipt="campaign state reported prior success",
                    retry_relevant=False,
                    identities={
                        "dispatch_id": prior_record.dispatch_id,
                        "dispatched_session_id": prior_record.dispatched_session_id,
                    },
                )
                provenance.confirm(
                    DispatchEffectName.COMMIT,
                    receipt="reused committed campaign dispatch",
                    identities={
                        "dispatch_id": prior_record.dispatch_id,
                        "dispatched_session_id": prior_record.dispatched_session_id,
                    },
                )
                return DispatchCompleted(
                    success=True,
                    dispatch_status=DispatchStatus.SUCCESS,
                    dispatch_id=prior_record.dispatch_id,
                    dispatched_session_id=prior_record.dispatched_session_id,
                    reason="prior dispatch already succeeded",
                    effect_provenance=provenance.snapshot(),
                ).to_envelope()

        if skip_when:
            dispatches_dir = tool_ctx.temp_dir / "dispatches"
            accumulated_captures = read_all_campaign_captures(dispatches_dir, tool_ctx.kitchen_id)
            error_code, error_message, skip_condition_true = evaluate_skip_when(
                skip_when, accumulated_captures, ingredients
            )
            if error_code is not None:
                return fleet_error(error_code, error_message or "")

            if skip_condition_true:
                if campaign_state_path_str:
                    provenance.start(
                        DispatchEffectName.CAMPAIGN_STATE_WRITE,
                        identities={"campaign_state_path": campaign_state_path_str},
                    )
                    upsert_dispatch_record_by_name(
                        Path(campaign_state_path_str),
                        DispatchRecord(
                            name=effective_name,
                            status=DispatchStatus.SKIPPED,
                            reason="skip_when condition evaluated to true",
                            effect_provenance=provenance.snapshot().to_dict(),
                        ),
                    )
                    _confirm_campaign_state_write(
                        provenance,
                        campaign_state_path_str,
                        effective_name,
                    )
                return DispatchCompleted(
                    success=False,
                    dispatch_status=DispatchStatus.SKIPPED,
                    dispatch_id="",
                    dispatched_session_id="",
                    reason=FleetErrorCode.FLEET_DISPATCH_SKIPPED,
                    effect_provenance=provenance.snapshot(),
                ).to_envelope()

        # Dispatch-feasibility preflight: verify the backend can enforce
        # all fix-required hooks for the recipe's run_skill steps before
        # handing execution to the fleet facade.
        _effective_backend_map, _fleet_finalized_projection = _load_preflight_projection(
            tool_ctx,
            recipe,
            ingredients,
            _override_backend,
        )

        if _override_backend is not None and _fleet_finalized_projection is not None:
            _preflight_err = _check_dispatch_feasibility(
                post_prune_step_names=list(_fleet_finalized_projection.ordered_step_names),
                active_recipe_steps=_finalized_recipe_steps(_fleet_finalized_projection),
                backend=_override_backend,
                config_providers=tool_ctx.config.providers,
                recipe_name=recipe,
                config_backend=tool_ctx.config.agent_backend,
                skill_resolver=tool_ctx.skill_resolver,
                project_root=tool_ctx.project_dir,
                temp_dir=tool_ctx.temp_dir,
            )
            if _preflight_err is not None:
                return _preflight_err

        effective_dispatch_backend = _override_backend or tool_ctx.backend
        if effective_dispatch_backend is None:
            return fleet_error(
                FleetErrorCode.FLEET_INVALID_BACKEND,
                "Fleet dispatch requires a configured backend.",
            )
        managed_join_parent_id: str | None = None
        adaptation_context = None
        if effective_dispatch_backend.capabilities.managed_fixed_batch_route_capable:
            if resume_session_id and prior_dispatch_id:
                managed_join_parent_id = resume_managed_join_parent_id(
                    tool_ctx.temp_dir / "dispatches" / f"{prior_dispatch_id}.json",
                    effective_name,
                )
            if managed_join_parent_id is None and not resume_session_id:
                managed_join_parent_id = new_managed_launch_id()
            if managed_join_parent_id is not None:

                def _log_refusal(refusal: ManagedJoinIssuanceRefusal) -> None:
                    logger.warning("managed_join_issuance_refused", reason=refusal.reason)

                evidence = acquire_managed_join_evidence(
                    backend=effective_dispatch_backend,
                    configured_model=(
                        tool_ctx.config.model.model_override or tool_ctx.config.model.default_model
                    ),
                    state_root=tool_ctx.project_dir,
                    parent_id=managed_join_parent_id,
                    launch_context="direct",
                    on_refusal=_log_refusal,
                )
                if evidence is not None:
                    adaptation_context = evidence.context
                else:
                    managed_join_parent_id = None
        cancel_scope: anyio.CancelScope | None = None
        try:
            tool_timeout_sec = tool_ctx.config.run_skill.mcp_tool_timeout_sec
            if (
                not isinstance(tool_timeout_sec, (int, float))
                or isinstance(tool_timeout_sec, bool)
                or not math.isfinite(tool_timeout_sec)
                or tool_timeout_sec <= 0
            ):
                return fleet_error(
                    FleetErrorCode.FLEET_INVALID_BACKEND,
                    f"run_skill.mcp_tool_timeout_sec must be a positive number of "
                    f"seconds, got {tool_timeout_sec!r}.",
                )
            with anyio.fail_after(tool_timeout_sec) as cancel_scope:
                async with tools_fleet_dispatch.progress_heartbeat(ctx):
                    result = await tools_fleet_dispatch.execute_dispatch(
                        tool_ctx=tool_ctx,
                        recipe=recipe,
                        task=task,
                        ingredients=ingredients,
                        dispatch_name=dispatch_name,
                        timeout_sec=timeout_sec,
                        prompt_builder=_get_food_truck_prompt_builder(
                            effective_dispatch_backend,
                            has_unguarded_filesystem_access=(
                                effective_dispatch_backend.capabilities.has_unguarded_filesystem_access
                            ),
                            projected_sous_chef=_project_food_truck_sous_chef(
                                tool_ctx,
                                effective_dispatch_backend,
                                adaptation_context=adaptation_context,
                            ),
                        ),
                        quota_refresher=_refresh_quota_cache,
                        cache_invalidator=invalidate_cache,
                        capture=capture,
                        resume_session_id=resume_session_id,
                        resume_checkpoint=parsed_checkpoint,
                        idle_output_timeout=idle_output_timeout,
                        caller_session_id=caller_session_id,
                        prior_dispatch_id=prior_dispatch_id,
                        resume_message=resume_message,
                        caller_instructions=caller_instructions,
                        dispatch_backend=dispatch_backend,
                        effective_backend_map=_effective_backend_map,
                        provenance=provenance,
                        native_shell_capture_mode=native_shell_capture_mode,
                        managed_join_parent_id=managed_join_parent_id,
                        adaptation_context=adaptation_context,
                    )
        except TimeoutError:
            if cancel_scope is None or not cancel_scope.cancel_called:
                raise
            provenance.request_cancel()
            logger.error(
                "dispatch_food_truck_mcp_tool_timeout",
                timeout_sec=tool_ctx.config.run_skill.mcp_tool_timeout_sec,
            )
            timeout_message = (
                f"MCP tool timeout ({tool_ctx.config.run_skill.mcp_tool_timeout_sec}s) "
                "exceeded during dispatch"
            )
            snapshot = provenance.snapshot()
            identities = _dispatch_effect_identities(snapshot)
            if snapshot.aggregate_phase == DispatchAggregatePhase.NOT_STARTED:
                return DispatchRejected(
                    error_code=FleetErrorCode.FLEET_L3_TIMEOUT,
                    message=timeout_message,
                    effect_provenance=snapshot,
                ).to_envelope()
            return DispatchCompleted(
                success=False,
                dispatch_status=DispatchStatus.INTERRUPTED,
                dispatch_id=identities.get("dispatch_id", ""),
                dispatched_session_id=identities.get("dispatched_session_id", ""),
                reason=FleetErrorCode.FLEET_L3_TIMEOUT,
                diagnostic_message=timeout_message,
                effect_provenance=snapshot,
            ).to_envelope()

        if campaign_state_path_str and isinstance(result, DispatchResult):
            provenance.start(
                DispatchEffectName.CAMPAIGN_STATE_WRITE,
                identities={"campaign_state_path": campaign_state_path_str},
            )
            campaign_write_confirmed = _write_dispatch_to_campaign_state(
                campaign_state_path_str,
                effective_name,
                result.outcome,
                result.per_dispatch_state_path,
            )
            if campaign_write_confirmed:
                _confirm_campaign_state_write(
                    provenance,
                    campaign_state_path_str,
                    effective_name,
                )
            else:
                provenance.mark_ambiguous(
                    DispatchEffectName.CAMPAIGN_STATE_WRITE,
                    evidence="campaign state writer failed",
                    identities={"campaign_state_path": campaign_state_path_str},
                )

        outcome = (
            replace(result.outcome, effect_provenance=provenance.snapshot())
            if isinstance(result.outcome, (DispatchCompleted, DispatchRejected))
            else result.outcome
        )

        if campaign_state_path_str and isinstance(outcome, DispatchCompleted):
            # Logic failures halt the campaign unless a named retry or its policy allows
            # progression. Infrastructure failures remain retriable at L3.
            if (
                not continue_on_failure
                and outcome.dispatch_status == DispatchStatus.FAILURE
                and outcome.reason not in _INFRASTRUCTURE_FAILURE_REASONS
                and not dispatch_name
            ):
                return fleet_error(
                    FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                    "Campaign halted: a prior dispatch failed and "
                    "continue_on_failure is false. "
                    "No further dispatches permitted.",
                )

            if outcome.dispatch_status != DispatchStatus.SUCCESS and (
                continue_on_failure or dispatch_name
            ):
                logger.warning(
                    "dispatch_non_success_allowed_past_halt_gate",
                    dispatch_name=effective_name,
                    dispatch_status=outcome.dispatch_status,
                    reason=outcome.reason,
                    continue_on_failure=continue_on_failure,
                    has_dispatch_name=bool(dispatch_name),
                )

        if isinstance(outcome, DispatchCompleted) and outcome.dispatch_id:
            diag_log_dir = resolve_log_dir(tool_ctx.config.linux_tracing.log_dir)
            hr = _read_health_report(diag_log_dir, outcome.dispatch_id)
            if hr is not None:
                outcome = replace(outcome, health_report=hr)

        return outcome.to_envelope()
    except Exception as exc:
        logger.error("dispatch_food_truck unhandled exception", exc_info=True)
        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@session_scoped(SCOPE_FLEET)
@_cancellation_shield(result_type="fleet_error")
@track_response_size("record_gate_dispatch")
async def record_gate_dispatch(
    dispatch_name: str,
    approved: bool,
    ctx: Context = CurrentContext(),
) -> str:
    """Record the outcome of a gate dispatch to the campaign state file.

    Gate dispatches are handled by AskUserQuestion (no L3 session). This tool
    persists the user's approval/rejection so that campaign resume can skip
    completed gates.

    Args:
        dispatch_name: Name of the gate dispatch in the campaign manifest.
        approved: True if the user approved the gate, False if rejected.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx as _get_ctx_for_feature_check  # circular-break

        _feature_ctx = _get_ctx_for_feature_check()
        if not is_feature_enabled(
            "fleet",
            _feature_ctx.config.features,
            experimental_enabled=_feature_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        if not campaign_state_path_str:
            return fleet_error(
                FleetErrorCode.FLEET_GATE_NO_CAMPAIGN,
                "No AUTOSKILLIT_CAMPAIGN_STATE_PATH set — not running in campaign mode.",
            )

        result = record_gate_outcome(Path(campaign_state_path_str), dispatch_name, approved)
        if not result.success:
            try:
                error_code = FleetErrorCode(result.error_code)
            except ValueError:
                error_code = FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
            return fleet_error(error_code, result.error_message)

        return json.dumps(
            {"success": True, "dispatch_name": result.dispatch_name, "status": result.status}
        )
    except Exception as exc:
        logger.error("record_gate_dispatch unhandled exception", exc_info=True)
        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )
