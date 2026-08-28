"""run_skill admission phase: recipe-execution attestation, audit reservation,
and resume/dynamic/standalone child-prompt construction.

Returns the terminal MCP response string when an early exit is warranted;
``None`` otherwise, in which case dispatch continues to the next phase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE,
    RECIPE_EXECUTION_INACTIVE_MESSAGE,
    AuditAttemptId,
    AuditCycleVerificationError,
    AuditCycleVerifier,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcomeStatus,
    AuditReservationRequest,
    DeclaredTruthUnresolved,
    DeclaredTruthUnsupported,
    RecipeExecutionId,
    ReservationDecision,
    SkillContractError,
    compute_audit_slot_intent_digest,
    compute_runtime_binding_digest,
    normalize_declared_truth,
    resolve_temp_dir,
)
from autoskillit.server._audit_authority_materializer import (
    derive_initial_lifecycle_ids,
    load_current_prior_authority,
    normalize_audited_plan_refs,
)
from autoskillit.server._recipe_execution import (
    RecipeExecutionAdmissionError,
    bind_attested_runtime_invocation,
    build_bound_child_prompt,
    build_standalone_child_prompt,
    get_recipe_execution,
    record_runtime_binding_digest,
    resolve_attested_input_preflight,
)
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._execution_helpers import (
    AuditOutputMode,
    select_audit_output_contract,
)
from autoskillit.server.tools._types import deny_envelope
from autoskillit.server.tools.tools_pipeline_tracker import _restore_reserved_tracker_authority

if TYPE_CHECKING:
    from collections.abc import Mapping

    from autoskillit.core import BoundScalar, ToolDef
    from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState


def _recipe_execution_deny(code: str, message: str) -> str:
    return json.dumps(
        deny_envelope(
            f"RECIPE EXECUTION REJECTED [{code}]: {message}",
            stage="preflight:recipe_execution",
            retriable=False,
        )
    )


def _admit_step_guard(state: _RunSkillDispatchState) -> str | None:
    """Adjudicate an installed recipe's server-authoritative step guard."""
    if state._installed_execution is None:
        return None
    step_context = f"step {state.step_name!r}: "
    guard = state._installed_execution.snapshot.step_guards.get(state.step_name)
    if guard is None:
        if state.step_guard_value is not None:
            return _recipe_execution_deny(
                "recipe_step_guard_unexpected",
                f"{step_context}step_guard_value was supplied for an unguarded recipe step",
            )
        return None
    try:
        should_skip = normalize_declared_truth(state.step_guard_value)
    except DeclaredTruthUnresolved as exc:
        return _recipe_execution_deny("recipe_step_guard_value_required", f"{step_context}{exc}")
    except DeclaredTruthUnsupported as exc:
        return _recipe_execution_deny("recipe_step_guard_value_invalid", f"{step_context}{exc}")
    if not should_skip:
        return None
    if state._tracker_target is None or state._tracker_lease is None:
        return _recipe_execution_deny(
            "recipe_step_guard_tracker_unavailable",
            f"{step_context}cannot record a skipped guarded step without tracker authority",
        )
    marked = _te_pkg.mark_step_skipped(
        state._tracker_target, state._tracker_lease, state.step_name
    )
    if not marked.get("success"):
        return json.dumps(marked)
    return json.dumps(
        {
            "next_step": guard.bypass_target,
            "reason": "skip_when_true",
            "skipped": True,
            "step_name": state.step_name,
            "success": True,
        }
    )


def _audit_preflight_step_names(
    tool_ctx,
    installed,
) -> tuple[str, ...]:
    resolver = tool_ctx.skill_contract_resolver
    if resolver is None:
        raise RecipeExecutionAdmissionError(
            "audit_preflight_resolver_unavailable",
            "audit publication requires the compiled skill-contract resolver",
        )
    names = tuple(
        step_name
        for step_name, template in installed.snapshot.templates.items()
        if (
            template.invocation.skill_name is not None
            and (contract := resolver(f"/autoskillit:{template.invocation.skill_name}"))
            is not None
            and getattr(contract, "input_preflight", None) == "audit_cycle_inventory"
        )
    )
    if not names:
        raise RecipeExecutionAdmissionError(
            "audit_preflight_consumers_missing",
            "audit publication has no compiled preflight consumer steps",
        )
    return names


def _build_actual_mcp_kwargs(
    tool_def: ToolDef, values: Mapping[str, BoundScalar | None]
) -> dict[str, BoundScalar]:
    """Assemble the actual runtime kwargs the attestation gate checks.

    Iterates ``tool_def``'s handler params only — a param declared
    ``handler_parameter=False`` (e.g. ``dispatch_items``) or
    ``structured_skill_inputs=True`` (``skill_inputs``, which has its own
    dedicated channel) is excluded — and pulls each remaining param's
    runtime value from ``values``, keyed by param name, which the caller
    builds from the handler's locals.

    A param missing from ``values``, or a ``values`` key absent from the
    filtered param set, is a loud construction-time bug: adding a
    ``ToolParamDef`` without wiring its runtime value (or vice versa) fails
    the first attested call instead of silently drifting. ``None`` values
    are omitted — the vacancy sentinel for optional params such as
    ``stale_threshold``/``idle_output_timeout`` (subsumes the prior
    hand-typed ``if ... is not None`` special case).
    """
    handler_param_names = frozenset(
        param.name
        for param in tool_def.params
        if param.handler_parameter and not param.structured_skill_inputs
    )
    missing = handler_param_names - values.keys()
    if missing:
        raise ValueError(
            f"{tool_def.name}: no runtime value supplied for handler params: {sorted(missing)!r}"
        )
    unknown = values.keys() - handler_param_names
    if unknown:
        raise ValueError(
            f"{tool_def.name}: runtime values supplied for unknown handler params: "
            f"{sorted(unknown)!r}"
        )
    result: dict[str, BoundScalar] = {}
    for name in handler_param_names:
        value = values[name]
        if value is not None:
            result[name] = value
    return result


def _admit_recipe_execution(state: _RunSkillDispatchState) -> str | None:
    state._preflight_result = None
    state._bound_recipe_inputs = ()
    state._invocation_template = None
    state._audit_reservation = None
    state._audit_preflight_steps = ()
    state._target_contract = (
        state.tool_ctx.skill_contract_resolver(state.skill_command)
        if (
            state._stored_contract_entry is None
            and state.tool_ctx.skill_contract_resolver is not None
        )
        else None
    )
    state._audit_publication = getattr(
        state._target_contract,
        "audit_authority_publication",
        None,
    )
    state.child_skill_command = state.skill_command
    state._claims_recipe_execution = bool(
        state.recipe_execution_id or state.invocation_template_digest
    )
    state._dynamic_recipe_call = bool(
        state._installed_execution is not None
        and state.step_name
        and state.step_name in state._installed_execution.snapshot.dynamic_skill_step_names
    )
    state._audit_output_mode = None
    if state._audit_publication is not None and not state.resume_session_id:
        state._audit_output_mode = (
            AuditOutputMode.ATTESTED
            if state._installed_execution is not None and not state._dynamic_recipe_call
            else AuditOutputMode.STANDALONE
        )
        if state._target_contract is None:
            raise SkillContractError("audit output contract is unavailable")
        select_audit_output_contract(state._target_contract, state._audit_output_mode)
    # Resolve the clone-local containment anchor before every publication branch (#4387).
    # Only active recipe executions consume it, so only they require a non-empty cwd.
    if state._installed_execution is not None and not state.cwd:
        return json.dumps(
            deny_envelope(
                "run_skill: cwd must not be empty when a recipe execution is active.",
                stage="preflight:cwd",
                retriable=False,
            )
        )
    state._clone_allowed_root = resolve_temp_dir(
        Path(state.cwd), state.tool_ctx.config.workspace.temp_dir
    )
    if state._dynamic_recipe_call:
        if (terminal := _admit_step_guard(state)) is not None:
            return terminal
        if state._claims_recipe_execution:
            return _recipe_execution_deny(
                "recipe_execution_dynamic_attestation",
                "a dynamic recipe skill step cannot claim a concrete invocation template",
            )
        if not state.resume_session_id:
            try:
                state.child_skill_command = build_standalone_child_prompt(
                    state.skill_command,
                    state.cwd,
                    state.skill_inputs,
                    audit_output_mode=state._audit_output_mode,
                )
            except RecipeExecutionAdmissionError as exc:
                return _recipe_execution_deny(exc.code, str(exc))
    elif state._installed_execution is not None:
        if not state.recipe_execution_id or not state.invocation_template_digest:
            return _recipe_execution_deny(
                "recipe_execution_attestation_missing",
                RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE,
            )
        if not state.step_name:
            return _recipe_execution_deny(
                "recipe_execution_step_missing",
                "an attested recipe invocation requires its exact step_name",
            )
        _run_skill_tool_def = _te_pkg.get_tool_def("run_skill")
        if _run_skill_tool_def is None:
            raise RuntimeError("run_skill must be a registered ToolDef")
        _actual_mcp_kwargs = _build_actual_mcp_kwargs(
            _run_skill_tool_def,
            {
                "skill_command": state.skill_command,
                "cwd": state.cwd,
                "model": state.model,
                "step_name": state.step_name,
                "recipe_execution_id": state.recipe_execution_id,
                "invocation_template_digest": state.invocation_template_digest,
                "step_provider": state.step_provider,
                "order_id": state.order_id,
                "stale_threshold": state.stale_threshold,
                "idle_output_timeout": state.idle_output_timeout,
                "output_dir": state.output_dir,
                "resume_session_id": state.resume_session_id,
                "closure_authority_path": state.closure_authority_path,
                "closure_authority_hash": state.closure_authority_hash,
                "closure_plan_paths": state.closure_plan_paths,
                "closure_base_sha": state.closure_base_sha,
                "closure_diff_sha": state.closure_diff_sha,
                "closure_target_sha": state.closure_target_sha,
                "step_guard_value": state.step_guard_value,
                "retry_after_audit_attempt_id": state.retry_after_audit_attempt_id,
                "native_shell_capture_mode": state.native_shell_capture_mode,
            },
        )
        try:
            state._bound_recipe_inputs, state._invocation_template = (
                bind_attested_runtime_invocation(
                    state._installed_execution,
                    execution_id=state.recipe_execution_id,
                    step_name=state.step_name,
                    template_digest=state.invocation_template_digest,
                    skill_command=state.skill_command,
                    skill_inputs=state.skill_inputs,
                    actual_mcp_kwargs=_actual_mcp_kwargs,
                )
            )
        except RecipeExecutionAdmissionError as exc:
            return _recipe_execution_deny(exc.code, str(exc))
        try:
            state._preflight_result = resolve_attested_input_preflight(
                state.tool_ctx,
                state._installed_execution,
                skill_command=state.skill_command,
                execution_id=state.recipe_execution_id,
                step_name=state.step_name,
                template=state._invocation_template,
                bound_inputs=state._bound_recipe_inputs,
                allowed_root=state._clone_allowed_root,
            )
        except RecipeExecutionAdmissionError as exc:
            return _recipe_execution_deny(exc.code, str(exc))
        _runtime_digest = compute_runtime_binding_digest(
            execution_id=state.recipe_execution_id,
            step_name=state.step_name,
            template_digest=state.invocation_template_digest,
            bound_inputs=state._bound_recipe_inputs,
            actual_mcp_kwargs=_actual_mcp_kwargs,
            preflight=state._preflight_result,
            retry_after_audit_attempt_id=state.retry_after_audit_attempt_id or None,
        )
        try:
            state._installed_execution = record_runtime_binding_digest(
                state.tool_ctx,
                execution_id=state.recipe_execution_id,
                step_name=state.step_name,
                digest=_runtime_digest,
            )
        except RecipeExecutionAdmissionError as exc:
            return _recipe_execution_deny(exc.code, str(exc))
        if (terminal := _admit_step_guard(state)) is not None:
            return terminal
        if state._audit_publication is not None:
            try:
                state._slot_intent_digest = compute_audit_slot_intent_digest(
                    execution_id=state.recipe_execution_id,
                    step_name=state.step_name,
                    template_digest=state.invocation_template_digest,
                    bound_inputs=state._bound_recipe_inputs,
                    actual_mcp_kwargs=_actual_mcp_kwargs,
                    preflight=state._preflight_result,
                    retry_after_audit_attempt_id=(state.retry_after_audit_attempt_id or None),
                )
                state._bound_input_map = dict(state._bound_recipe_inputs)
                state._prior_input_field = state._audit_publication.prior_input_field
                # _bound_input_map values are BoundScalar (str | int | bool); the
                # isinstance check below is the real type guard, matching the flat
                # code's untyped-local behavior before this became a state field.
                state._prior_path = state._bound_input_map.get(  # type: ignore[assignment]
                    state._prior_input_field
                )
                state._recipe_execution_key = RecipeExecutionId(state.recipe_execution_id)
                if isinstance(state._prior_path, str) and state._prior_path:
                    _prior_authority = load_current_prior_authority(
                        state._prior_path,
                        allowed_root=state._clone_allowed_root,
                        ledger=state.tool_ctx.audit_admission_ledger,
                        recipe_execution_id=state._recipe_execution_key,
                    )
                    state._audited_plan_refs = (
                        normalize_audited_plan_refs(
                            str(state._bound_input_map.get("all_plan_paths") or ""),
                            allowed_root=state._clone_allowed_root,
                        )
                        if state._bound_input_map.get("all_plan_paths")
                        else _prior_authority.audited_plan_refs
                    )
                    state._cycle_id = _prior_authority.cycle_id
                    state._scope_id = _prior_authority.scope_id
                    state._part_id = _prior_authority.part_id
                    state._parent_digest = _prior_authority.authority_digest
                else:
                    state._audited_plan_refs = normalize_audited_plan_refs(
                        str(state._bound_input_map.get("all_plan_paths") or ""),
                        allowed_root=state._clone_allowed_root,
                    )
                    state._cycle_id, state._scope_id, state._part_id = (
                        derive_initial_lifecycle_ids(
                            recipe_execution_id=state._recipe_execution_key,
                            step_name=state.step_name,
                            slot_intent_digest=state._slot_intent_digest,
                        )
                    )
                    state._parent_digest = None
                state._audit_preflight_steps = _audit_preflight_step_names(
                    state.tool_ctx,
                    state._installed_execution,
                )
                with state.tool_ctx.recipe_execution_lock:
                    if get_recipe_execution(state.tool_ctx) is not state._installed_execution:
                        raise RecipeExecutionAdmissionError(
                            "recipe_execution_replaced",
                            "active recipe execution changed before audit reservation",
                        )
                    state._reservation_outcome = state.tool_ctx.audit_admission_ledger.reserve(
                        AuditReservationRequest(
                            recipe_execution_id=state._recipe_execution_key,
                            installation_version=(state._installed_execution.installation_version),
                            step_name=state.step_name,
                            invocation_template_digest=(state.invocation_template_digest),
                            slot_intent_digest=state._slot_intent_digest,
                            runtime_binding_digest=_runtime_digest,
                            audited_plan_refs=state._audited_plan_refs,
                            cycle_id=state._cycle_id,
                            scope_id=state._scope_id,
                            part_id=state._part_id,
                            allowed_root=state._clone_allowed_root,
                            parent_authority_digest=state._parent_digest,
                            retry_after_audit_attempt_id=(
                                AuditAttemptId(state.retry_after_audit_attempt_id)
                                if state.retry_after_audit_attempt_id
                                else None
                            ),
                            tracker_target_order_id=(
                                state._tracker_target.target_order_id
                                if state._tracker_target is not None
                                else None
                            ),
                            tracker_expected=(
                                state._tracker_target.expected
                                if state._tracker_target is not None
                                else False
                            ),
                        )
                    )
                if state._reservation_outcome.reservation is not None:
                    (
                        state._tracker_target,
                        state._tracker_authority,
                        state._tracker_key,
                        state._tracker_lease,
                    ) = _restore_reserved_tracker_authority(
                        state.tool_ctx,
                        state._reservation_outcome.reservation,
                        state._tracker_key,
                    )
                match state._reservation_outcome.decision:
                    case ReservationDecision.DISPATCH_NEW | ReservationDecision.REDISPATCH_OPEN:
                        assert state._reservation_outcome.reservation is not None
                        assert state._reservation_outcome.reservation_handle is not None
                        state._audit_reservation = state._reservation_outcome.reservation
                        state.child_skill_command = build_bound_child_prompt(
                            state.skill_command,
                            state._bound_recipe_inputs,
                            state._preflight_result,
                            audit_reservation_handle=(
                                state._reservation_outcome.reservation_handle
                            ),
                            audit_reserved_plan_refs=state._audited_plan_refs,
                            audit_output_mode=state._audit_output_mode,
                        )
                    case ReservationDecision.EXACT_REPLAY:
                        assert state._reservation_outcome.replay_outcome is not None
                        state._replay = state._reservation_outcome.replay_outcome
                        if state._replay.replay_response_json is not None:
                            _replay_response = state._replay.replay_response_json
                        else:
                            _replay_response = _te_pkg._audit_response(
                                status=AuditOutcomeStatus.EXACT_REPLAY,
                                attempt_id=state._replay.attempt_id,
                                verdict=state._replay.verdict,
                                path=state._replay.path,
                                error=state._replay.error,
                                kill_reason=state._replay.kill_reason,
                            )
                        return _te_pkg._finalize_run_skill_completion(
                            state.tool_ctx,
                            _te_pkg._begin_run_skill_completion(
                                state.tool_ctx,
                                request_context=state.ctx,
                                order_id=state.order_id,
                                step_name=state.step_name,
                                tracker_target=state._tracker_target,
                            ),
                            _replay_response,
                        )
                    case ReservationDecision.RESUME_PREPARED:
                        assert state._reservation_outcome.reservation is not None
                        with state.tool_ctx.recipe_execution_lock:
                            if (
                                get_recipe_execution(state.tool_ctx)
                                is not state._installed_execution
                            ):
                                raise RecipeExecutionAdmissionError(
                                    "recipe_execution_replaced",
                                    "active recipe execution changed before audit recovery",
                                )
                            state._resumed = (
                                state.tool_ctx.audit_authority_materializer.materialize(
                                    reservation=state._reservation_outcome.reservation,
                                    semantic_result_path=(
                                        state._reservation_outcome.reservation.semantic_result_path
                                    ),
                                    preflight_step_names=state._audit_preflight_steps,
                                )
                            )
                        _resumed_response = _te_pkg._complete_resumed_audit(
                            state.tool_ctx,
                            result=state._resumed,
                            skill_command=state.skill_command,
                            tracker_target=state._tracker_target,
                        )
                        return _te_pkg._finalize_run_skill_completion(
                            state.tool_ctx,
                            _te_pkg._begin_run_skill_completion(
                                state.tool_ctx,
                                request_context=state.ctx,
                                order_id=state.order_id,
                                step_name=state.step_name,
                                tracker_target=state._tracker_target,
                            ),
                            _resumed_response,
                        )
                    case ReservationDecision.PUBLISHED_PENDING_FINALIZATION:
                        assert state._reservation_outcome.reservation is not None
                        state._authority = AuditCycleVerifier(
                            state._clone_allowed_root
                        ).load_authority(state._reservation_outcome.reservation.authority_path)
                        state._published = AuditMaterializationResult(
                            status=(AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION),
                            attempt_id=state._reservation_outcome.attempt_id,
                            verdict=state._authority.verdict,
                            path=state._reservation_outcome.reservation.authority_path,
                            error=None,
                        )
                        _published_response = _te_pkg._complete_resumed_audit(
                            state.tool_ctx,
                            result=state._published,
                            skill_command=state.skill_command,
                            tracker_target=state._tracker_target,
                        )
                        return _te_pkg._finalize_run_skill_completion(
                            state.tool_ctx,
                            _te_pkg._begin_run_skill_completion(
                                state.tool_ctx,
                                request_context=state.ctx,
                                order_id=state.order_id,
                                step_name=state.step_name,
                                tracker_target=state._tracker_target,
                            ),
                            _published_response,
                        )
                    case ReservationDecision.CONFLICT:
                        return _te_pkg._audit_response(
                            status=AuditOutcomeStatus.CONFLICT,
                            attempt_id=state._reservation_outcome.attempt_id,
                            verdict=None,
                            path=None,
                            error=state._reservation_outcome.conflict_detail,
                        )
            except (
                AuditCycleVerificationError,
                OSError,
                RecipeExecutionAdmissionError,
                ValueError,
            ) as exc:
                code = (
                    exc.code
                    if isinstance(exc, RecipeExecutionAdmissionError)
                    else "audit_reservation_failed"
                )
                return _recipe_execution_deny(code, str(exc))
        else:
            state.child_skill_command = build_bound_child_prompt(
                state.skill_command,
                state._bound_recipe_inputs,
                state._preflight_result,
                audit_output_mode=state._audit_output_mode,
            )
    elif state._claims_recipe_execution:
        return _recipe_execution_deny(
            "recipe_execution_inactive",
            RECIPE_EXECUTION_INACTIVE_MESSAGE,
        )
    elif not state.resume_session_id:
        try:
            state.child_skill_command = build_standalone_child_prompt(
                state.skill_command,
                state.cwd,
                state.skill_inputs,
                audit_output_mode=state._audit_output_mode,
            )
        except RecipeExecutionAdmissionError as exc:
            return _recipe_execution_deny(
                exc.code,
                str(exc),
            )
    return None
