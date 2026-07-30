"""MCP tool handlers: dispatch_food_truck, record_gate_dispatch."""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import os
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio

if TYPE_CHECKING:
    from autoskillit.fleet import DispatchOutcome

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    CapabilityResolutionDetail,
    CodingAgentBackend,
    FleetErrorCode,
    SessionCheckpoint,
    SkillExecutionRole,
    detect_autoskillit_mcp_prefix,
    find_caller_session_id,
    fleet_error,
    get_logger,
    is_feature_enabled,
)
from autoskillit.fleet import (
    _INFRASTRUCTURE_FAILURE_REASONS,
    CampaignStateMutator,
    DispatchAggregatePhase,
    DispatchCompleted,
    DispatchEffectName,
    DispatchEffectProvenance,
    DispatchProvenanceTracker,
    DispatchRecord,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
    _build_food_truck_prompt,
    evaluate_skip_when,
    execute_dispatch,
    find_completed_dispatch,
    has_blocking_dispatch,
    prepare_resume,
    read_all_campaign_captures,
    read_state,
    record_gate_outcome,
    upsert_dispatch_record_by_name,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_fleet
from autoskillit.server._misc import (
    SkillProjectionContext,
    project_agent_skill_document,
    resolve_backend_override,
    resolve_log_dir,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._auto_overrides import (
    _compute_effective_backend_map,
    _provider_aware_capability_overrides,
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._preflight import (
    _check_dispatch_feasibility,
    filter_steps_by_post_prune,
)
from autoskillit.server.tools._serve_helpers import build_backend_capabilities_map

logger = get_logger(__name__)

_BOUND_DISPATCH_PROVENANCE: ContextVar[DispatchProvenanceTracker | None] = ContextVar(
    "bound_dispatch_provenance",
    default=None,
)
_ACTIVE_DISPATCH_PROVENANCE: ContextVar[DispatchProvenanceTracker] = ContextVar(
    "active_dispatch_provenance"
)


def _attach_dispatch_provenance(
    raw: str,
    provenance: DispatchProvenanceTracker,
) -> str:
    """Attach the current immutable provenance snapshot to any JSON envelope."""
    try:
        envelope = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    if not isinstance(envelope, dict):
        return raw
    envelope["effect_provenance"] = provenance.snapshot().to_dict()
    return json.dumps(envelope)


def _bound_dispatch_provenance() -> DispatchProvenanceTracker:
    provenance = _BOUND_DISPATCH_PROVENANCE.get()
    if provenance is None:
        raise RuntimeError("dispatch provenance binder was not initialized")
    return provenance


def _dispatch_cancellation_response(
    provenance: DispatchProvenanceTracker,
    _exc: asyncio.CancelledError,
) -> str:
    provenance.request_cancel()
    return _attach_dispatch_provenance(
        fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            "CancelledError: transport teardown",
        ),
        provenance,
    )


def _bind_dispatch_provenance(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Create one argument-aware provenance journal at the outer MCP boundary."""
    signature = inspect.signature(fn)

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind_partial(*args, **kwargs)
        tracker = DispatchProvenanceTracker()
        requested_resume = str(bound.arguments.get("resume_session_id") or "")
        prior_dispatch = str(bound.arguments.get("prior_dispatch_id") or "")
        if requested_resume:
            tracker.start(
                DispatchEffectName.REQUESTED_RESUME_BINDING,
                retry_relevant=False,
                identities={
                    "resume_session_id": requested_resume,
                    "prior_dispatch_id": prior_dispatch,
                },
            )
            tracker.confirm(
                DispatchEffectName.REQUESTED_RESUME_BINDING,
                receipt="outer MCP request arguments bound",
                retry_relevant=False,
                identities={
                    "resume_session_id": requested_resume,
                    "prior_dispatch_id": prior_dispatch,
                },
            )
        token = _BOUND_DISPATCH_PROVENANCE.set(tracker)
        try:
            raw = await fn(*args, **kwargs)
            return _attach_dispatch_provenance(raw, tracker)
        finally:
            _BOUND_DISPATCH_PROVENANCE.reset(token)

    return wrapper


def _read_health_report(diagnostics_log_dir: Path, dispatch_id: str) -> dict[str, Any] | None:
    """Read the per-dispatch health report JSON written by analyze-pipeline-health."""
    report_path = diagnostics_log_dir / "health-reports" / f"{dispatch_id}_health_report.json"
    if not report_path.is_file():
        return None
    try:
        return json.loads(report_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


_MAX_CALLER_INSTRUCTIONS_LEN = 2000


def _write_dispatch_to_campaign_state(
    campaign_state_path_str: str,
    effective_name: str,
    outcome: DispatchOutcome,
    per_dispatch_state_path: Path | None = None,
) -> bool:
    """Write the dispatch outcome to the campaign state file.

    Accepts a DispatchOutcome (DispatchCompleted or DispatchRejected) and persists
    the dispatch record to AUTOSKILLIT_CAMPAIGN_STATE_PATH. Never raises — state
    write failures are non-fatal.

    When per_dispatch_state_path is provided, reads the authoritative DispatchRecord
    from the per-dispatch state file and forwards it directly, avoiding manual
    field reconstruction and eliminating double-normalization of token_usage.
    """
    try:
        match outcome:
            case DispatchRejected(error_code=code, message=msg):
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord.for_refusal(
                        name=effective_name,
                        error_code=code,
                        diagnostic_message=msg,
                        dispatch_id=outcome.dispatch_id,
                        effect_provenance=outcome.effect_provenance.to_dict(),
                    ),
                )
            case DispatchCompleted() as completed:
                if per_dispatch_state_path is not None:
                    per_dispatch_state = read_state(per_dispatch_state_path)
                    if per_dispatch_state is None:
                        logger.warning(
                            "_write_dispatch_to_campaign_state: read_state(%s) returned None "
                            "— falling back to manual reconstruction",
                            per_dispatch_state_path,
                        )
                    else:
                        for d in per_dispatch_state.dispatches:
                            if d.name == effective_name:
                                upsert_dispatch_record_by_name(
                                    Path(campaign_state_path_str),
                                    d,
                                )
                                return True
                        logger.warning(
                            "_write_dispatch_to_campaign_state: no dispatch named %r in %s "
                            "— falling back to manual reconstruction",
                            effective_name,
                            per_dispatch_state_path,
                        )
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord(
                        name=effective_name,
                        status=completed.dispatch_status,
                        dispatch_id=completed.dispatch_id,
                        dispatched_session_id=completed.dispatched_session_id,
                        reason=completed.reason,
                        diagnostic_message=completed.diagnostic_message,
                        token_usage=completed.token_usage,
                        effect_provenance=completed.effect_provenance.to_dict(),
                    ),
                )
        return True
    except Exception:
        logger.warning("_write_dispatch_to_campaign_state: failed", exc_info=True)
        return False


def _confirm_campaign_state_write(
    provenance: DispatchProvenanceTracker,
    campaign_state_path_str: str,
    effective_name: str,
) -> bool:
    """Confirm the write and persist its post-confirmation provenance receipt."""
    provenance.confirm(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        receipt="campaign state writer confirmed persistence",
        identities={"campaign_state_path": campaign_state_path_str},
    )
    try:
        receipt_persisted = False
        with CampaignStateMutator(Path(campaign_state_path_str)) as mutator:
            if mutator.state is not None:
                record = next(
                    (
                        dispatch
                        for dispatch in mutator.state.dispatches
                        if dispatch.name == effective_name
                    ),
                    None,
                )
                if record is not None:
                    receipt = provenance.snapshot().to_dict()
                    if record.effect_provenance != receipt:
                        record.effect_provenance = receipt
                        mutator.mark_dirty()
                    receipt_persisted = True
    except Exception:
        logger.warning(
            "_confirm_campaign_state_write: receipt persistence failed",
            exc_info=True,
        )
        receipt_persisted = False
    if not receipt_persisted:
        provenance.mark_ambiguous(
            DispatchEffectName.CAMPAIGN_STATE_WRITE,
            evidence="campaign state confirmation receipt persistence failed",
            identities={"campaign_state_path": campaign_state_path_str},
        )
    return receipt_persisted


def _get_food_truck_prompt_builder(
    backend: CodingAgentBackend,
    has_unguarded_filesystem_access: bool = False,
    projected_sous_chef: str = "",
) -> Callable[..., str]:
    """Return the food truck prompt builder with mcp_prefix pre-bound."""

    mcp_prefix = detect_autoskillit_mcp_prefix(backend.capabilities)
    return functools.partial(
        _build_food_truck_prompt,
        mcp_prefix=mcp_prefix,
        has_unguarded_filesystem_access=has_unguarded_filesystem_access,
        projected_sous_chef=projected_sous_chef,
    )


def _project_food_truck_sous_chef(
    tool_ctx: Any,
    backend: CodingAgentBackend | None,
) -> str:
    """Project L2 orchestration guidance before crossing into the fleet layer."""
    if tool_ctx.skill_resolver is None:
        return ""
    catalog = tool_ctx.skill_resolver.list_effective(
        tool_ctx.project_dir,
        SkillExecutionRole.ORCHESTRATOR,
        visibility=tool_ctx.config.skill_visibility_spec(),
        recipe_packs=tool_ctx.active_recipe_packs,
        recipe_features=tool_ctx.active_recipe_features,
    )
    sous_chef = next((skill for skill in catalog.skills if skill.name == "sous-chef"), None)
    if sous_chef is None:
        return ""
    return project_agent_skill_document(
        sous_chef,
        SkillProjectionContext(
            cwd=tool_ctx.project_dir.resolve(),
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions if backend is not None else None,
            gating=False,
        ),
    ).content


def _dispatch_effect_identities(
    snapshot: DispatchEffectProvenance,
) -> dict[str, str]:
    """Collect the latest recorded value for each downstream identity."""
    identities: dict[str, str] = {}
    for effect in snapshot.effects:
        identities.update(effect.known_downstream_identities)
    return identities


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
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

    Resume vs. Fresh Dispatch:
        - ``resume_session_id``: Session ID to resume (``--resume <id>``).
        - ``resume_checkpoint``: Completed-items context from the prior session.
        - ``resume_message``: Free-text caller context injected into the resume prompt.
        All three are optional and only meaningful when resuming a prior dispatch.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    if (fleet_gate := _require_fleet("dispatch_food_truck")) is not None:
        return fleet_gate

    try:
        provenance = _ACTIVE_DISPATCH_PROVENANCE.get()
        if caller_instructions and len(caller_instructions) > _MAX_CALLER_INSTRUCTIONS_LEN:
            caller_instructions = caller_instructions[:_MAX_CALLER_INSTRUCTIONS_LEN]

        dispatch_backend: CodingAgentBackend | None = None
        if backend is not None:
            try:
                dispatch_backend = resolve_backend_override(backend)
            except ValueError as exc:
                return fleet_error(
                    FleetErrorCode.FLEET_INVALID_BACKEND,
                    str(exc),
                )

        # Feature guard: config authority check independent of MCP visibility state.
        # Fleet sessions open the gate unconditionally at boot; this catch-all ensures
        # dispatch_food_truck never executes when features.fleet is disabled in config.
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
                # Backward-compat with the legacy path: also probe the global
                # blocking-dispatch set (handles campaigns where the named
                # dispatch isn't the one in the blocking state).
                if has_blocking_dispatch(campaign_sp):
                    return fleet_error(
                        FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                        "Campaign halted: a prior dispatch failed and "
                        "continue_on_failure is false. "
                        "No further dispatches permitted.",
                    )
            else:
                if has_blocking_dispatch(campaign_sp):
                    return fleet_error(
                        FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                        "Campaign halted: a prior dispatch failed and "
                        "continue_on_failure is false. "
                        "No further dispatches permitted.",
                    )

        from autoskillit.server import _get_ctx  # circular-break
        from autoskillit.server._misc import (  # circular-break
            _refresh_quota_cache,
            check_and_sleep_if_needed,
            invalidate_cache,
        )

        parsed_checkpoint = (
            SessionCheckpoint.from_dict(resume_checkpoint) if resume_checkpoint else None
        )
        tool_ctx = _get_ctx()
        _override_backend = dispatch_backend if dispatch_backend is not None else tool_ctx.backend
        provenance.start(
            DispatchEffectName.CALLER_IDENTITY,
            retry_relevant=False,
        )
        caller_session_id = find_caller_session_id(project_dir=tool_ctx.project_dir)
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
        # spawning a subprocess.
        _fleet_load_result: dict[str, Any] = {}
        _capability_overrides: dict[str, str] = {}
        _cap_detail: CapabilityResolutionDetail | None = None
        _effective_backend_map: dict[str, str] | None = None
        if tool_ctx.recipes is not None:
            try:
                _preflight_recipe_info = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
                _preflight_raw_steps = (
                    tool_ctx.recipes.load(_preflight_recipe_info.path).steps
                    if _preflight_recipe_info is not None
                    else None
                )
                _capability_overrides, _cap_detail = _provider_aware_capability_overrides(
                    _override_backend,
                    recipe,
                    tool_ctx.config.providers,
                    _preflight_raw_steps,
                    skill_resolver=tool_ctx.skill_resolver,
                    config_backend=tool_ctx.config.agent_backend,
                    project_root=tool_ctx.project_dir,
                )
                _merged_ingredients = {**(ingredients or {}), **_capability_overrides}
                _effective_backend_map, _backend_origin_map = _compute_effective_backend_map(
                    _preflight_raw_steps,
                    _override_backend.name if _override_backend else None,
                    tool_ctx.config.providers,
                    recipe,
                    skill_resolver=tool_ctx.skill_resolver,
                    config_backend=tool_ctx.config.agent_backend,
                    project_root=tool_ctx.project_dir,
                )
                _preflight_backend_capabilities_map = build_backend_capabilities_map(
                    _effective_backend_map, _override_backend
                )
                _fleet_load_result = tool_ctx.recipes.load_and_validate(
                    recipe,
                    tool_ctx.project_dir,
                    suppressed=tool_ctx.config.migration.suppressed if tool_ctx.config else None,
                    ingredient_overrides=_merged_ingredients,
                    temp_dir=tool_ctx.temp_dir,
                    backend_name=_override_backend.name if _override_backend else None,
                    effective_backend_map=_effective_backend_map,
                    backend_capabilities_map=_preflight_backend_capabilities_map,
                )
            except Exception:
                logger.warning("dispatch_food_truck_preflight_load_failed", exc_info=True)

        if not _fleet_load_result.get("dispatch_feasible", True):
            _infeasible_steps = _fleet_load_result.get("infeasible_steps", [])
            _infeasible_msg = (
                f"Recipe '{recipe}' is dispatch-infeasible: capability gate(s) "
                f"blocked at preflight. Infeasible steps: {_infeasible_steps}"
            )
            if _cap_detail is not None and _cap_detail.resolution_path == "none_pass":
                _missing = list(_cap_detail.missing_provider_steps)
                _escape_hatch = (
                    f"Add provider overrides with ANTHROPIC_BASE_URL for steps: "
                    f"{_missing}. Example config: "
                    f"providers.recipe_overrides.<recipe>.*: <profile>"
                )
                _infeasible_msg = (
                    f"Recipe '{recipe}' is dispatch-infeasible: steps {_missing} "
                    f"lack ANTHROPIC_BASE_URL provider overrides. {_escape_hatch}"
                )
                logger.warning(
                    "dispatch_food_truck_capability_infeasible",
                    recipe=recipe,
                    infeasible_steps=_infeasible_steps,
                )
                _err_envelope = json.loads(
                    fleet_error(FleetErrorCode.FLEET_RECIPE_INVALID, _infeasible_msg)
                )
                _err_envelope["missing_provider_steps"] = _missing
                _err_envelope["escape_hatch"] = _escape_hatch
                return json.dumps(_err_envelope)
            logger.warning(
                "dispatch_food_truck_capability_infeasible",
                recipe=recipe,
                infeasible_steps=_infeasible_steps,
            )
            return fleet_error(FleetErrorCode.FLEET_RECIPE_INVALID, _infeasible_msg)

        _active_recipe_steps: dict[str, Any] | None = None
        if _fleet_load_result and tool_ctx.recipes is not None:
            try:
                _recipe_info = tool_ctx.recipes.find(recipe, tool_ctx.project_dir)
                if _recipe_info is not None:
                    _recipe_obj = tool_ctx.recipes.load(_recipe_info.path)
                    _active_recipe_steps = filter_steps_by_post_prune(
                        _recipe_obj.steps,
                        _fleet_load_result.get("post_prune_step_names", []),
                    )
            except Exception:
                logger.warning("dispatch_food_truck_preflight_recipe_load_failed", exc_info=True)

        if _override_backend is not None and _active_recipe_steps is not None:
            _preflight_err = _check_dispatch_feasibility(
                post_prune_step_names=_fleet_load_result.get("post_prune_step_names", []),
                active_recipe_steps=_active_recipe_steps,
                backend=_override_backend,
                config_providers=tool_ctx.config.providers,
                recipe_name=recipe,
                config_backend=tool_ctx.config.agent_backend,
                skill_resolver=tool_ctx.skill_resolver,
                project_root=tool_ctx.project_dir,
            )
            if _preflight_err is not None:
                return _preflight_err

        _supports_quota = (
            _override_backend is not None
            and _override_backend.capabilities.anthropic_provider_capable
        )
        effective_dispatch_backend = _override_backend or tool_ctx.backend
        if effective_dispatch_backend is None:
            return fleet_error(
                FleetErrorCode.FLEET_INVALID_BACKEND,
                "Fleet dispatch requires a configured backend.",
            )
        cancel_scope: anyio.CancelScope | None = None
        try:
            with anyio.fail_after(tool_ctx.config.run_skill.mcp_tool_timeout_sec) as cancel_scope:
                result = await execute_dispatch(
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
                        ),
                    ),
                    quota_checker=lambda cfg: check_and_sleep_if_needed(
                        cfg,
                        provider="anthropic" if _supports_quota else "",
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
                    provider_capability_overrides=_capability_overrides,
                    dispatch_backend=dispatch_backend,
                    effective_backend_map=_effective_backend_map,
                    provenance=provenance,
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

        # Post-dispatch halt: if continue_on_failure=false and the dispatch failed
        # (logic failure, not infrastructure), return FLEET_CAMPAIGN_HALTED immediately.
        # Infrastructure failures (fleet_l3_no_result_block, fleet_quota_exhausted) do
        # not halt — they are retriable at the L3 level without campaign-level impact.
        # Also skip halt if dispatch_name was provided — the pre-dispatch gate already
        # handled the reset case, and a retry of the blocking dispatch should proceed.
        if (
            campaign_state_path_str
            and not continue_on_failure
            and isinstance(outcome, DispatchCompleted)
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

        if (
            campaign_state_path_str
            and isinstance(outcome, DispatchCompleted)
            and outcome.dispatch_status != DispatchStatus.SUCCESS
            and (continue_on_failure or dispatch_name)
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
    if (fleet_gate := _require_fleet("record_gate_dispatch")) is not None:
        return fleet_gate

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
