"""run_skill finalize phase: executor invocation, audit materialization, and
terminal response shaping.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from autoskillit.core import (
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcome,
    AuditOutcomeStatus,
    SkillResult,
    get_logger,
)
from autoskillit.server._recipe_execution import get_recipe_execution
from autoskillit.server._recipe_execution import (
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


async def _execute_and_finalize_run_skill(state: _RunSkillDispatchState) -> str:
    def _observe_contract_session_id(candidate_session_id: str) -> None:
        state.contract_lifecycle.observe_candidate(candidate_session_id)

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
            with anyio.fail_after(state._cfg.run_skill.mcp_tool_timeout_sec):
                async with _te_pkg.execution_marker(
                    state._marker_dir,
                    state._caller_hook_session_id,
                    "run-skill",
                ):
                    state.contract_lifecycle.execution_started = True
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
                            provider_name=state.profile_name_out,
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
                            on_launch_resolved=state.contract_lifecycle.bind_launch,
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
            if state._audit_reservation is not None:
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
                                error=(
                                    "active recipe execution changed before audit materialization"
                                ),
                            )
                        else:
                            state._materialized = (
                                state.tool_ctx.audit_authority_materializer.materialize(
                                    reservation=state._audit_reservation,
                                    semantic_result_path=Path(state._semantic_path),
                                    preflight_step_names=state._audit_preflight_steps,
                                )
                            )
                state._materialized_status = _te_pkg._materialization_outcome_status(
                    state._materialized
                )
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
                                state._tracker_target.expected
                                if state._tracker_target is not None
                                else False
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
                                kill_reason=state.skill_result.kill_reason,
                            ),
                            child_session_id=state.skill_result.session_id,
                        )
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
