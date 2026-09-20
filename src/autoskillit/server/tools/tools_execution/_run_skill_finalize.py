"""run_skill finalize phase: executor invocation, audit materialization, and
terminal response shaping.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from autoskillit.core import (
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcome,
    AuditOutcomeStatus,
    CandidatePreSpawnRejection,
    ExecutionCandidateAttempt,
    ResolvedLaunchContract,
    SkillResult,
    get_logger,
)
from autoskillit.execution import (
    admit_quota,
    resolve_log_dir,
    write_execution_candidate_manifest,
)
from autoskillit.quota_constraints import quota_scope
from autoskillit.server._misc import _ensure_quota_refresh_started
from autoskillit.server.recipe._recipe_execution import get_recipe_execution
from autoskillit.server.recipe._recipe_execution import (
    required_audit_finalization_effect_names as _required_audit_finalization_effect_names,
)
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._execution_helpers import (
    clear_run_skill_state,
    persist_run_skill_state,
)
from autoskillit.server.tools._native_shell_capture import rebind_verified_final_session
from autoskillit.server.tools._types import ToolFailureEnvelope

if TYPE_CHECKING:
    from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState

logger = get_logger(__name__)


def _quota_authority_rejection(
    state: _RunSkillDispatchState,
    contract: ResolvedLaunchContract,
) -> CandidatePreSpawnRejection | None:
    assert state._cfg is not None
    identity = contract.quota_identity
    scope = identity.get("credential_scope", "")
    mode = identity.get("mode", "")
    if state._cfg.quota_guard.enabled and mode == "api-key":
        key = (state.provider_extras or {}).get("ANTHROPIC_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY"
        )
        if not key or f"api-key:{sha256(key.encode()).hexdigest()}" != scope:
            return CandidatePreSpawnRejection(
                reason="quota_authority_changed",
                attempted_contract=contract,
            )
    if state._cfg.quota_guard.enabled and mode.startswith("anthropic-oauth") and not scope:
        return CandidatePreSpawnRejection(
            reason="quota_authority_unknown",
            attempted_contract=contract,
        )
    if state._quota_lease is not None:
        if scope != state._quota_scope:
            return CandidatePreSpawnRejection(
                reason="quota_authority_changed",
                attempted_contract=contract,
            )
        try:
            current_scope = quota_scope(
                "anthropic", Path(state._cfg.quota_guard.credentials_path).expanduser()
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            current_scope = ""
        if current_scope != scope:
            return CandidatePreSpawnRejection(
                reason="quota_authority_changed",
                attempted_contract=contract,
            )
    return None


async def _admit_finalized_launch(
    state: _RunSkillDispatchState,
    replace_current_attempt: Callable[[ExecutionCandidateAttempt], None],
    contract: ResolvedLaunchContract,
) -> CandidatePreSpawnRejection | None:
    assert state._cfg is not None
    rejection = _quota_authority_rejection(state, contract)
    if rejection is not None:
        return rejection
    identity = contract.quota_identity
    scope = identity.get("credential_scope", "")
    mode = identity.get("mode", "")
    if state._quota_lease is not None:
        return None
    decision = await admit_quota(
        config=state._cfg.quota_guard,
        credential_scope=scope or None,
        diagnostic_log_root=resolve_log_dir(state._cfg.linux_tracing.log_dir),
        deadline_monotonic=state._invocation_deadline_monotonic,
        provider=identity.get("provider", "anthropic"),
        binding_scope=scope or None,
        nested_worker="run_skill" in state._skill_caps,
    )
    if not decision.admitted:
        return CandidatePreSpawnRejection(
            reason=decision.reason,
            attempted_contract=contract,
            rate_limit=decision.rate_limit,
        )
    if mode == "anthropic-oauth":
        try:
            _ensure_quota_refresh_started(state.tool_ctx)
        except Exception:
            logger.warning("run_skill_quota_refresh_start_failed", exc_info=True)
    state._quota_lease = decision.lease
    state._quota_scope = scope
    selection = state.execution_selection
    if selection is not None and selection.attempts:
        replace_current_attempt(
            replace(
                selection.attempts[-1],
                admission_status="admitted",
                admission_at_epoch=int(time.time()),
                admission_cache_status=decision.reason,
            )
        )
    return None


def _materialize_audit_result(state: _RunSkillDispatchState) -> str | None:
    if state._audit_reservation is None:
        return None
    assert state.skill_result is not None
    assert not isinstance(state.skill_result, CandidatePreSpawnRejection)
    assert state._completion_invocation_id is not None
    # outcome_fields values are BoundScalar-ish (str | int | ...); the
    # isinstance check below is the real type guard, matching the flat
    # code's untyped-local behavior before this became a state field.
    state._semantic_path = (state.skill_result.outcome_fields or {}).get(
        "audit_semantic_result_path"
    )  # type: ignore[assignment]
    if not isinstance(state._semantic_path, str) or not state._semantic_path:
        state._materialized = _te_pkg._reject_missing_semantic_result(
            state.tool_ctx,
            state._audit_reservation,
        )
    else:
        with state.tool_ctx.recipe_execution_lock:
            if get_recipe_execution(state.tool_ctx) is not state._installed_execution:
                state._materialized = AuditMaterializationResult(
                    status=AuditMaterializationStatus.CONFLICT,
                    attempt_id=state._audit_reservation.current_attempt_id,
                    verdict=None,
                    path=None,
                    error="active recipe execution changed before audit materialization",
                )
            else:
                state._materialized = state.tool_ctx.audit_authority_materializer.materialize(
                    reservation=state._audit_reservation,
                    semantic_result_path=Path(state._semantic_path),
                    preflight_step_names=state._audit_preflight_steps,
                )
    state._materialized_status = _te_pkg._materialization_outcome_status(state._materialized)
    match state._materialized_status:
        case AuditOutcomeStatus.PUBLISHED:
            assert state._materialized.verdict is not None
            assert state._materialized.path is not None
            state.skill_result.result = (
                f"Server-authored audit outcome: {AuditOutcomeStatus.PUBLISHED.value}"
            )
            state.skill_result.outcome_fields = None
            state.skill_result.audit = _te_pkg.AuditResultOutcome(
                status=AuditOutcomeStatus.PUBLISHED,
                verdict=state._materialized.verdict,
                cycle_path=str(state._materialized.path),
                attempt_id=state._materialized.attempt_id,
            )
            state._audit_outcome_to_finalize = AuditOutcome(
                status=AuditOutcomeStatus.PUBLISHED,
                attempt_id=state._materialized.attempt_id,
                verdict=state._materialized.verdict,
                path=state._materialized.path,
                error=None,
                kill_reason=state.skill_result.kill_reason,
                tracker_target_order_id=(
                    state._tracker_target.target_order_id
                    if state._tracker_target is not None
                    else None
                ),
                tracker_expected=(
                    state._tracker_target.expected if state._tracker_target is not None else False
                ),
            )
        case AuditOutcomeStatus.EXACT_REPLAY:
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                _te_pkg._audit_response(
                    status=state._materialized_status,
                    attempt_id=state._materialized.attempt_id,
                    verdict=state._materialized.verdict,
                    path=state._materialized.path,
                    error=state._materialized.error,
                    semantic_result_path=state._audit_reservation.semantic_result_path,
                    kill_reason=state.skill_result.kill_reason,
                ),
                child_session_id=state.skill_result.session_id,
            )
        case (
            AuditOutcomeStatus.SEMANTIC_REJECTED
            | AuditOutcomeStatus.CONFLICT
            | AuditOutcomeStatus.STORAGE_FAILURE
            | AuditOutcomeStatus.QUARANTINED
            | AuditOutcomeStatus.NON_PUBLISHED_STANDALONE
        ):
            state.skill_result.result = ""
            state.skill_result.outcome_fields = None
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                _te_pkg._audit_response(
                    status=state._materialized_status,
                    attempt_id=state._materialized.attempt_id,
                    verdict=None,
                    path=None,
                    error=state._materialized.error,
                    semantic_result_path=state._audit_reservation.semantic_result_path,
                    kill_reason=state.skill_result.kill_reason,
                ),
                child_session_id=state.skill_result.session_id,
            )
    return None


async def _execute_and_finalize_run_skill(
    state: _RunSkillDispatchState,
) -> str | CandidatePreSpawnRejection:
    def _replace_current_attempt(attempt: ExecutionCandidateAttempt) -> None:
        selection = state.execution_selection
        if selection is None:
            return
        primary = selection.attempts[0]
        state.execution_selection = replace(
            selection,
            attempts=(*selection.attempts[:-1], attempt),
            candidate_fallback=(
                selection.candidate_fallback or (attempt.execution_started and attempt.ordinal > 0)
            ),
            backend_rerouted=(
                selection.backend_rerouted
                or (
                    attempt.execution_started
                    and bool(primary.effective_backend)
                    and attempt.effective_backend != primary.effective_backend
                )
            ),
            provider_fallback=(
                selection.provider_fallback
                or (
                    attempt.execution_started
                    and attempt.provider_source_path.startswith("providers.execution_candidates.")
                    and attempt.effective_provider != primary.effective_provider
                )
            ),
        )
        assert state._cfg is not None
        write_execution_candidate_manifest(
            state.execution_selection,
            state._cfg.linux_tracing.log_dir,
            max_sessions=state._cfg.linux_tracing.max_sessions,
            project_dir=str(state.tool_ctx.project_dir),
            build_protected_campaign_ids=state.tool_ctx.build_protected_campaign_ids,
        )

    def _bind_launch(contract: ResolvedLaunchContract) -> None:
        state._current_launch_contract = contract
        state.contract_lifecycle.bind_launch(contract)
        selection = state.execution_selection
        if selection is not None and selection.attempts:
            _replace_current_attempt(
                replace(
                    selection.attempts[-1],
                    effective_backend=contract.effective_backend,
                    effective_provider=contract.provider,
                    effective_model=contract.physical_model or contract.configured_model or "",
                    backend_source_path=contract.backend_authority.key_path,
                    provider_source_path=contract.provider_source.key_path,
                    model_source_path=contract.physical_model_source.key_path,
                )
            )

    def _observe_contract_session_id(candidate_session_id: str) -> None:
        state.contract_lifecycle.observe_candidate(candidate_session_id)

    def _mark_execution_started() -> None:
        selection = state.execution_selection
        if selection is not None and selection.attempts:
            _replace_current_attempt(
                replace(
                    selection.attempts[-1],
                    execution_started=True,
                    admission_status="started",
                )
            )
        state.contract_lifecycle.execution_started = True

    state._start = time.monotonic()
    assert state._cfg is not None
    assert state.tool_ctx.executor is not None
    assert state.resolved_command is not None
    assert state._capability_contract is not None
    assert state.skill_add_dirs is not None
    assert state.expected_output_patterns is not None
    assert state.allowed_write_prefix is not None
    assert state.allowed_write_prefixes is not None
    assert state.write_watch_dirs is not None
    assert state.profile_name_out is not None
    assert state._inspector_model is not None
    assert state._network_access is not None
    assert state._execution_identity is not None
    assert state._caller_hook_session_id is not None
    assert state._completion_invocation_id is not None
    assert state._lineage_store is not None
    try:
        try:
            with anyio.fail_after(
                max(0.0, state._invocation_deadline_monotonic - time.monotonic())
            ):
                async with _te_pkg.execution_marker(
                    state._marker_dir,
                    state._caller_hook_session_id,
                    "run-skill",
                ):
                    if state._audit_reservation is not None:
                        if state.provider_extras is None:
                            state.provider_extras = {}
                        state.provider_extras[AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR] = str(
                            state.tool_ctx.audit_admission_ledger.store_authority.database_path
                        )
                    async with _te_pkg.progress_heartbeat(state.ctx):
                        state.skill_result = await state.tool_ctx.executor.run(
                            state.resolved_command,
                            state._capability_contract.cwd,
                            model=state.effective_model,
                            add_dirs=state.skill_add_dirs,
                            step_name=state.step_name,
                            kitchen_id=state.tool_ctx.kitchen_id,
                            order_id=state.effective_order_id,
                            expected_output_patterns=state.expected_output_patterns,
                            write_behavior=state.write_spec,
                            stale_threshold=float(state.stale_threshold)
                            if state.stale_threshold is not None
                            else None,
                            idle_output_timeout=float(state.idle_output_timeout)
                            if state.idle_output_timeout is not None
                            else None,
                            completion_marker=state.invocation_marker,
                            recipe_name=state.tool_ctx.recipe_name,
                            recipe_content_hash=state.tool_ctx.recipe_content_hash,
                            recipe_composite_hash=state.tool_ctx.recipe_composite_hash,
                            recipe_version=state.tool_ctx.recipe_version,
                            allowed_write_prefix=state.allowed_write_prefix,
                            allowed_write_prefixes=state.allowed_write_prefixes,
                            readonly_skill=state.is_read_only,
                            scope_discipline_skill=state.scope_discipline_skill,
                            completion_required=state.completion_required,
                            write_watch_dirs=state.write_watch_dirs,
                            provider_extras=state.provider_extras,
                            profile_name=state.profile_name_out,
                            provider_name=(
                                state.provider_binding.provider
                                if state.provider_binding is not None
                                else state.profile_name_out
                            ),
                            provider_binding=state.provider_binding,
                            model_pin=state.model_pin,
                            mark_execution_started=_mark_execution_started,
                            pre_spawn_admission=partial(
                                _admit_finalized_launch,
                                state,
                                _replace_current_attempt,
                            ),
                            execution_selection=state.execution_selection,
                            execution_selection_provider=lambda: state.execution_selection,
                            backend_authority=state._backend_authority,
                            resume_session_id=state.resume_session_id,
                            resume_launch_contract=state._resume_launch_contract,
                            marker_dir=state._marker_dir,
                            caller_session_id=state._caller_hook_session_id,
                            inspector_eligible=state._in_fleet_dispatch
                            and bool(state._inspector_model),
                            inspector_model=state._inspector_model,
                            network_access=state._network_access,
                            closure_spec=state.closure_spec,
                            closure_report_root=state.closure_report_root,
                            skill_contract=state._skill_contract,
                            capability_contract=state._capability_contract,
                            native_shell_capture_decision=(state._native_shell_capture_decision),
                            managed_lineage_ref=state._managed_lineage_ref,
                            on_launch_resolved=_bind_launch,
                            execution_identity=state._execution_identity,
                            on_session_id_resolved=(
                                _observe_contract_session_id
                                if state.contract_lifecycle.correlation_key is not None
                                else None
                            ),
                        )
        except TimeoutError as exc:
            state.contract_lifecycle.retain_bound = False
            logger.error(
                "run_skill_mcp_tool_timeout",
                timeout_sec=state._cfg.run_skill.mcp_tool_timeout_sec,
            )
            state._timeout_exc = TimeoutError(
                f"MCP tool timeout ({state._cfg.run_skill.mcp_tool_timeout_sec}s) exceeded"
            )
            state._timeout_exc.__cause__ = exc
            state._timeout_result = SkillResult.crashed(
                exception=state._timeout_exc,
                skill_command=state.resolved_command,
                order_id=state.effective_order_id,
            )
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                state._timeout_result.to_json(),
                child_session_id=state._timeout_result.session_id,
            )

        if isinstance(state.skill_result, CandidatePreSpawnRejection):
            return state.skill_result
        if state.skill_result.execution_selection is not None:
            state.execution_selection = state.skill_result.execution_selection
        state.contract_lifecycle.finalize(state.skill_result.session_id)

        rebind_verified_final_session(
            store=state._lineage_store,
            backend=state._effective_backend_obj,
            reference=state._managed_lineage_ref,
            is_resume=state._stored_contract_entry is not None,
            requested_session_id=state.resume_session_id,
            returned_session_id=state.skill_result.session_id,
            on_rebind=state.contract_lifecycle.rebind_final,
        )
        state.contract_lifecycle.apply_retention(state.skill_result.needs_retry)

        state._audit_outcome_to_finalize = None
        if state.skill_result.success:
            if (audit_response := _materialize_audit_result(state)) is not None:
                return audit_response
            if state._audit_outcome_to_finalize is not None:
                _te_pkg._complete_audit_finalization_effects(
                    state.tool_ctx,
                    attempt_id=state._audit_outcome_to_finalize.attempt_id,
                    skill_command=state.skill_command,
                )
            else:
                state.tool_ctx.audit.record_success(state.skill_command)
                clear_run_skill_state(state.tool_ctx.project_dir)
        else:
            await _te_pkg._notify(
                state.ctx,
                "error",
                "run_skill failed",
                "autoskillit.run_skill",
                extra={
                    "exit_code": state.skill_result.exit_code,
                    "subtype": state.skill_result.subtype,
                },
            )
            persist_run_skill_state(state.skill_result, state.tool_ctx.project_dir)
        if state.effective_order_id:
            state.skill_result.order_id = state.effective_order_id
        from autoskillit.server._misc import (  # circular-break
            _refresh_quota_cache,
        )

        if state.tool_ctx.background is not None:
            state.tool_ctx.background.submit(
                _refresh_quota_cache(state.tool_ctx.config.quota_guard),
                label="quota_post_run_refresh",
            )
        _json_str = state.skill_result.to_json()
        try:
            state._parsed = json.loads(_json_str)
        except Exception as exc:
            logger.warning("run_skill_json_parse_failed", exc_info=True)
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error=f"Degraded SkillResult payload: JSON parse failed: {exc}",
                        stage="validate_result:run_skill",
                        retriable=True,
                    )
                ),
                child_session_id=state.skill_result.session_id,
            )
        state._missing = {"success", "exit_code"} - state._parsed.keys()
        if state._missing:
            logger.warning(
                "run_skill_degraded_payload",
                absent_fields=sorted(state._missing),
            )
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error=(
                            f"Degraded SkillResult payload: missing keys {sorted(state._missing)}"
                        ),
                        stage="validate_result:run_skill",
                        retriable=True,
                    )
                ),
                child_session_id=state.skill_result.session_id,
            )
        state._shaped_response = _te_pkg.shape_execution_response(
            state.tool_ctx,
            state._parsed,
            tool_name="run_skill",
            work_dir=state.cwd,
        )
        if state._audit_outcome_to_finalize is not None:
            state._replay_payload = json.loads(state._shaped_response)
            state._replay_payload["audit_status"] = AuditOutcomeStatus.EXACT_REPLAY.value
            _replay_response = json.dumps(
                state._replay_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            state.tool_ctx.audit_admission_ledger.finalize_response(
                state._audit_outcome_to_finalize.attempt_id,
                AuditOutcome(
                    status=state._audit_outcome_to_finalize.status,
                    attempt_id=state._audit_outcome_to_finalize.attempt_id,
                    verdict=state._audit_outcome_to_finalize.verdict,
                    path=state._audit_outcome_to_finalize.path,
                    error=state._audit_outcome_to_finalize.error,
                    kill_reason=state._audit_outcome_to_finalize.kill_reason,
                    replay_response_json=_replay_response,
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
                ),
                required_effect_names=_required_audit_finalization_effect_names(),
            )
        return _te_pkg._finalize_run_skill_completion(
            state.tool_ctx,
            state._completion_invocation_id,
            state._shaped_response,
            child_session_id=state.skill_result.session_id,
        )
    except Exception as exc:
        state.contract_lifecycle.retain_bound = False
        logger.error("run_skill executor raised unexpectedly", exc_info=True)
        state._crashed_result = SkillResult.crashed(
            exception=exc,
            skill_command=state.resolved_command,
            order_id=state.effective_order_id,
        )
        return _te_pkg._finalize_run_skill_completion(
            state.tool_ctx,
            state._completion_invocation_id,
            state._crashed_result.to_json(),
            child_session_id=state._crashed_result.session_id,
        )
    finally:
        if state.step_name:
            state.tool_ctx.timing_log.record(
                state.step_name, time.monotonic() - state._start, order_id=state.effective_order_id
            )
