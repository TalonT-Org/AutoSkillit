from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import anyio
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.config import ExecutionCandidateSpec
from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    ApiFailureOutcome,
    CandidatePreSpawnRejection,
    ExecutionCandidateAttempt,
    ExecutionSelection,
    InfrastructureFaultError,
    RateLimitWindow,
    RetryReason,
    SkillContractError,
    SkillResult,
    get_logger,
    read_tracker_authority,
)
from autoskillit.core import current_order_id as _current_order_id
from autoskillit.core import current_step_name as _current_step_name
from autoskillit.execution import (
    read_session_index_rows,
    resolve_log_dir,
    write_execution_candidate_manifest,
)
from autoskillit.fleet import warm_failure_path_imports
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.lifecycle._guards import (
    _require_enabled,
    _require_orchestrator_exact,
)
from autoskillit.server.recipe._recipe_execution import get_recipe_execution
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    rehydrate_skill_invocation as _rehydrate_skill_invocation,
)
from autoskillit.server.tools._execution_helpers import (
    validate_resumed_skill_contract as _validate_resumed_skill_contract,
)
from autoskillit.server.tools._types import deny_envelope
from autoskillit.server.tools.tools_execution._managed_leaf import (  # noqa: F401
    _MAX_CLEANUP_FAILURE_RECORDS,
    _ChildResourceOwnerRequest,
    _record_cleanup_failure,
)
from autoskillit.server.tools.tools_execution._run_skill_session import (
    _prepare_owned_dispatch_session,
)
from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState
from autoskillit.server.tools.tools_pipeline_tracker import (
    _authority_blocks_dependency_check,
    _release_context_tracker,
    _select_tracker_authority,
)

logger = get_logger(__name__)


def _restore_resume_dispatch(state: _RunSkillDispatchState) -> str | None:
    """Restore the invocation and backend binding for a resumed session."""
    assert state._contract_store is not None
    try:
        state._stored_contract_entry = state._contract_store.load(state.resume_session_id)
        state._resume_launch_contract = state._stored_contract_entry.contract.launch_contract
        if state._resume_launch_contract is None:
            raise SkillContractError("Resume contract has no resolved launch contract")
        log_root = resolve_log_dir(state.tool_ctx.config.linux_tracing.log_dir)
        index_rows = read_session_index_rows(
            log_root / "sessions.jsonl",
            max_bytes=max(
                2_000_000,
                state.tool_ctx.config.linux_tracing.max_sessions * 4096,
            ),
        )
        selection_payload = next(
            (
                row.get("execution_selection")
                for row in reversed(index_rows)
                if row.get("session_id") == state.resume_session_id
            ),
            None,
        )
        if not isinstance(selection_payload, dict):
            raise SkillContractError("Resume selection evidence is unavailable")
        selection = ExecutionSelection.from_payload(selection_payload)
        expected_ref = f"execution-candidates/{selection.selection_id}.json"
        if selection.manifest_ref != expected_ref:
            raise SkillContractError("Resume selection manifest reference is invalid")
        with (log_root / selection.manifest_ref).open("rb") as manifest_file:
            manifest_bytes = manifest_file.read(1_000_001)
        if len(manifest_bytes) > 1_000_000:
            raise SkillContractError("Resume selection manifest is too large")
        if json.loads(manifest_bytes) != selection.to_payload():
            raise SkillContractError("Resume selection manifest does not match session")
        terminal_attempt = selection.terminal_attempt
        if (
            not selection.completed
            or terminal_attempt is None
            or not terminal_attempt.execution_started
            or terminal_attempt.child_session_id != state.resume_session_id
            or terminal_attempt.effective_backend
            != state._resume_launch_contract.effective_backend
            or terminal_attempt.effective_provider != state._resume_launch_contract.provider
        ):
            raise SkillContractError("Resume selection binding is invalid")
        state.execution_selection = selection
        state._resume_backend_authority = state._resume_launch_contract.backend_authority
        state._resume_backend_obj = state.tool_ctx.launch_resolver.backend_for_authority(
            state._resume_backend_authority
        )
        _validate_resumed_skill_contract(
            state._stored_contract_entry.contract,
            cwd=state.cwd,
            project_root=state.tool_ctx.project_dir,
            backend=state._resume_backend_obj,
        )
        if state._resume_backend_obj is None:
            raise SkillContractError("Resume contract backend is unavailable")
        state.invocation, state.projection_context = _rehydrate_skill_invocation(
            state._stored_contract_entry.contract,
            state._resume_backend_obj,
        )
    except (OSError, ValueError, SkillContractError) as exc:
        return SkillResult.crashed(
            exception=SkillContractError(
                f"Cannot resume session {state.resume_session_id!r}: {exc}"
            ),
            skill_command=state.skill_command,
            order_id=state.order_id,
        ).to_json()
    state.contract_lifecycle.bound_session_id = state.resume_session_id
    state.target_name = state._stored_contract_entry.contract.root_name
    return None


def _resolve_fresh_recipe_step(state: _RunSkillDispatchState) -> str | None:
    """Resolve and verify an omitted step name for a fresh recipe invocation."""
    if (
        state._installed_execution is not None
        or state.step_name
        or not state.tool_ctx.active_recipe_steps
    ):
        return None
    resolved, ambiguous = _te_pkg._resolve_step_name_from_recipe(
        state.skill_command, state.tool_ctx.active_recipe_steps
    )
    if state._tracker_target is not None and state._tracker_lease is not None:
        state._tracker_authority = read_tracker_authority(
            state._tracker_target, state._tracker_lease
        )
    if resolved:
        state.step_name = resolved
        logger.warning(
            "step_name_resolved_from_recipe",
            step=state.step_name,
            command=state.skill_command[:80],
        )
        if (
            lock_denial := _te_pkg._check_ingredient_locks(state.step_name, state.order_id)
        ) is not None:
            return lock_denial
        if (
            dep_denial := _te_pkg._check_pipeline_deps(state.step_name, state._tracker_authority)
        ) is not None:
            return dep_denial
        if (
            plan_path_denial := _te_pkg._check_review_approach_plan_path(
                state.step_name, state.skill_command
            )
        ) is not None:
            return plan_path_denial
    elif ambiguous and _authority_blocks_dependency_check(state._tracker_authority):
        return json.dumps(
            deny_envelope(
                (
                    f"{_te_pkg.DEPENDENCY_DENY_PREFIX}: step_name is empty and "
                    "matched multiple recipe steps by skill_command prefix "
                    "(ambiguous). Cannot verify dependency status. Pass "
                    "step_name explicitly."
                ),
                stage="preflight:ambiguous_step",
                retriable=False,
            )
        )
    elif not ambiguous and _te_pkg._has_active_locks(state.order_id):
        return json.dumps(
            deny_envelope(
                (
                    f"{_te_pkg.INGREDIENT_LOCK_DENY_PREFIX}: step_name is empty and "
                    "could not be resolved from the recipe. Cannot verify lock "
                    "status. Pass step_name explicitly or call "
                    "lock_ingredients(unlock=[...]) to release all locks."
                ),
                stage="preflight:ingredient_locks",
                retriable=False,
            )
        )
    elif not ambiguous and _authority_blocks_dependency_check(state._tracker_authority):
        return json.dumps(
            deny_envelope(
                (
                    f"{_te_pkg.DEPENDENCY_DENY_PREFIX}: step_name is empty and could "
                    "not be resolved from the recipe. Cannot verify dependency "
                    "status. Pass step_name explicitly."
                ),
                stage="preflight:unresolved_step",
                retriable=False,
            )
        )
    return None


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    recipe_execution_id: str = "",
    invocation_template_digest: str = "",
    step_provider: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    output_dir: str = "",
    resume_session_id: str = "",
    retry_after_audit_attempt_id: str = "",
    native_shell_capture_mode: str = "",
    closure_authority_path: str = "",
    closure_authority_hash: str = "",
    closure_plan_paths: str = "",
    closure_base_sha: str = "",
    closure_diff_sha: str = "",
    closure_target_sha: str = "",
    step_guard_value: str | bool | None = None,
    skill_inputs: dict[str, str | int | bool] | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Delegate one already-selected recipe step to a separate L1 headless coding-agent worker.

    Use this tool only when a headless recipe orchestrator operating at L2 or an
    interactive AutoSkillit cook/order session intends separate-worker delegation. The
    recipe step must already be selected before this call.

    When a user names or asks to use an available local skill for the current interactive
    conversation, load and follow its SKILL.md in the current interactive session.
    Do not call run_skill merely because the skill was named.

    Returns JSON with success, result, session_id, subtype, is_error, exit_code, needs_retry,
    and retry_reason. When needs_retry is true, follow the recipe's declared retry route.

    Args:
        skill_command: Full recipe-declared skill invocation or resume continuation.
        cwd: Absolute working directory for the separate coding-agent worker.
        model: Optional model identifier. Empty string uses the configured default.
            Under an attested call, a non-empty value is denied unless the step's
            with: block declares model — the step's model: field is normally
            resolved server-side instead. For unattested calls, where the
            recipe-step gate does not apply, it is a caller override.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
        order_id: Optional per-issue/order identifier for token telemetry scoping. When set,
            token and timing entries are keyed by this value, enabling per-issue isolation
            in get_token_summary/get_timing_summary and in the token_summary_appender hook.
        stale_threshold: Override the staleness kill threshold in seconds. When set on
            a RecipeStep, the recipe orchestrator passes it here. None uses the global
            config default (RunSkillConfig.stale_threshold, default 1200s).
        idle_output_timeout: Override the idle stdout kill threshold in seconds.
            0 = disabled for this step. None = use global config
            (RunSkillConfig.idle_output_timeout, default 1000s).
        resume_session_id: Session ID from a previous run_skill call that was interrupted.
            When set, resume that coding-agent session instead of starting fresh. The
            skill_command becomes a continuation instruction; pass the prior result's session_id.
        retry_after_audit_attempt_id: Server-issued rejected audit attempt to correct.
            This is attested control data and is never passed to the child as a skill input.
        native_shell_capture_mode: Optional managed Codex shell I/O mode. Omission
            defaults fresh launches to capture. Resumes inherit their durable lineage.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_exact("run_skill")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if cwd and not Path(cwd).is_absolute():
        return json.dumps(
            deny_envelope(
                (
                    f"run_skill: cwd must be an absolute path, got: {cwd!r}. "
                    "Check that the skill resolved the worktree_path to absolute "
                    '(e.g. WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)").'
                ),
                stage="preflight:cwd",
                retriable=False,
            )
        )
    if cwd and not os.path.isdir(cwd):
        return json.dumps(
            deny_envelope(
                f"run_skill: cwd does not exist: {cwd}",
                stage="preflight:cwd",
                retriable=False,
            )
        )
    if (
        step_name
        and not resume_session_id
        and (_lock_denial := _te_pkg._check_ingredient_locks(step_name, order_id)) is not None
    ):
        return _lock_denial
    if (
        step_name
        and not resume_session_id
        and not (recipe_execution_id or invocation_template_digest)
        and (
            _plan_path_denial := _te_pkg._check_review_approach_plan_path(step_name, skill_command)
        )
        is not None
    ):
        return _plan_path_denial
    warm_failure_path_imports()
    state: _RunSkillDispatchState | None = None
    try:
        from autoskillit.server import _get_ctx  # circular-break

        state = _RunSkillDispatchState(
            skill_command=skill_command,
            cwd=cwd,
            model=model,
            step_name=step_name,
            recipe_execution_id=recipe_execution_id,
            invocation_template_digest=invocation_template_digest,
            step_provider=step_provider,
            order_id=order_id,
            stale_threshold=stale_threshold,
            idle_output_timeout=idle_output_timeout,
            output_dir=output_dir,
            resume_session_id=resume_session_id,
            retry_after_audit_attempt_id=retry_after_audit_attempt_id,
            native_shell_capture_mode=native_shell_capture_mode,
            closure_authority_path=closure_authority_path,
            closure_authority_hash=closure_authority_hash,
            closure_plan_paths=closure_plan_paths,
            closure_base_sha=closure_base_sha,
            closure_diff_sha=closure_diff_sha,
            closure_target_sha=closure_target_sha,
            step_guard_value=step_guard_value,
            skill_inputs=skill_inputs,
            ctx=ctx,
            tool_ctx=_get_ctx(),
        )
        (
            state._tracker_target,
            state._tracker_authority,
            state._tracker_key,
            state._tracker_lease,
        ) = _select_tracker_authority(state.tool_ctx, order_id)
        if (
            step_name
            and not resume_session_id
            and (_dep_denial := _te_pkg._check_pipeline_deps(step_name, state._tracker_authority))
            is not None
        ):
            return _dep_denial
        state._installed_execution = get_recipe_execution(state.tool_ctx)
        state._contract_store = state.tool_ctx.skill_session_contract_store
        state.contract_lifecycle.store = state._contract_store
        state._stored_contract_entry = None
        state._session_contract = None
        state._session_snapshot = None
        state._native_shell_capture_decision = None
        state._managed_lineage_ref = None
        state._resume_backend_obj = None
        state._resume_backend_authority = None
        state._resume_launch_contract = None
        state._effective_skill_resolver = None
        state.invocation = None
        state.projection_context = None
        state.target_name = None
        if resume_session_id:
            if (terminal := _restore_resume_dispatch(state)) is not None:
                return terminal
        else:
            if (terminal := _te_pkg._resolve_fresh_invocation(state)) is not None:
                return terminal
            if (terminal := _resolve_fresh_recipe_step(state)) is not None:
                return terminal
        if state.invocation is None or state.projection_context is None:
            raise SkillContractError("Skill dispatch branches did not produce a bound contract")

        if (terminal := _te_pkg._admit_recipe_execution(state)) is not None:
            return terminal

        state._invocation_cwd = state.cwd
        started_epoch = time.time()
        started_monotonic = time.monotonic()
        run_config = state.tool_ctx.config.run_skill
        deadline_epoch = min(
            started_epoch + run_config.timeout,
            started_epoch + run_config.mcp_tool_timeout_sec,
        )
        try:
            inherited_deadline = float(os.environ.get("AUTOSKILLIT_SESSION_DEADLINE", ""))
        except ValueError:
            inherited_deadline = 0.0
        if 0 < inherited_deadline < deadline_epoch:
            deadline_epoch = inherited_deadline
        if resume_session_id and state.execution_selection is not None:
            original_deadline = state.execution_selection.invocation_deadline_epoch
            if original_deadline is None or original_deadline <= started_epoch:
                raise SkillContractError("Resume continuation deadline is unavailable or expired")
            deadline_epoch = min(deadline_epoch, float(original_deadline))
        state._invocation_deadline_epoch = deadline_epoch
        state._invocation_deadline_monotonic = started_monotonic + (deadline_epoch - started_epoch)
        state._completion_invocation_id = _te_pkg._begin_run_skill_completion(
            state.tool_ctx,
            request_context=state.ctx,
            order_id=state.order_id,
            step_name=state.step_name,
            tracker_target=state._tracker_target,
        )
        if state._completion_invocation_id is None:
            raise SkillContractError("Completion invocation identity is unavailable")
        if state.execution_selection is None:
            state.execution_selection = ExecutionSelection(
                selection_id=state._completion_invocation_id,
                campaign_id=os.environ.get(CAMPAIGN_ID_ENV_VAR, ""),
                invocation_deadline_epoch=int(state._invocation_deadline_epoch),
                remaining_retry_budget=(state.tool_ctx.config.providers.provider_retry_limit),
            )
        else:
            budget = state.execution_selection.remaining_retry_budget
            if budget is None or budget <= 0:
                raise SkillContractError("Resume continuation retry budget is exhausted")
            state.execution_selection = replace(
                state.execution_selection,
                completed=False,
                continuation=None,
                remaining_retry_budget=budget - 1,
            )

        with structlog.contextvars.bound_contextvars(tool="run_skill", cwd=cwd):
            logger.info("run_skill", command=skill_command[:80], cwd=cwd)
            config = state.tool_ctx.config

            def _persist_selection() -> None:
                assert state.execution_selection is not None
                write_execution_candidate_manifest(
                    state.execution_selection,
                    config.linux_tracing.log_dir,
                    max_sessions=config.linux_tracing.max_sessions,
                    project_dir=str(state.tool_ctx.project_dir),
                    build_protected_campaign_ids=(state.tool_ctx.build_protected_campaign_ids),
                )

            alternatives = (
                list(config.providers.execution_candidates)
                if not resume_session_id
                and _te_pkg.is_feature_enabled(
                    "providers",
                    config.features,
                    experimental_enabled=config.experimental_enabled,
                )
                else []
            )
            routes: list[tuple[int, ExecutionCandidateSpec | None]]
            if resume_session_id:
                assert state.execution_selection is not None
                prior_attempt = state.execution_selection.terminal_attempt
                assert prior_attempt is not None
                routes = [(prior_attempt.ordinal, None)]
            else:
                routes = list(enumerate((None, *alternatives)))
            with anyio.fail_after(
                max(0.0, state._invocation_deadline_monotonic - time.monotonic())
            ):
                for ordinal, candidate in routes:
                    state.cwd = state._invocation_cwd
                    state._current_launch_contract = None
                    assert state.execution_selection is not None
                    if resume_session_id:
                        assert prior_attempt is not None
                        attempt = replace(
                            prior_attempt,
                            attempt=1
                            + max(
                                row.attempt
                                for row in state.execution_selection.attempts
                                if row.candidate_id == prior_attempt.candidate_id
                            ),
                            admission_status="pending",
                            admission_at_epoch=None,
                            admission_cache_status="",
                            admission_cache_age_seconds=None,
                            rate_limit_status="",
                            rate_limit_type="",
                            rate_limit_resets_at_epoch=None,
                            rejection_reason=None,
                            transition="same_binding_resume",
                            execution_started=False,
                            child_session_id=None,
                        )
                    else:
                        attempt = ExecutionCandidateAttempt(
                            candidate_id=f"{state.execution_selection.selection_id}:{ordinal}",
                            ordinal=ordinal,
                            attempt=1,
                            parent_backend=(
                                state.tool_ctx.backend.name if state.tool_ctx.backend else ""
                            ),
                            parent_provider=config.model.provider,
                            parent_model=config.model.default_model,
                            requested_backend=(candidate.backend if candidate is not None else ""),
                            requested_provider=(
                                candidate.profile
                                if candidate is not None and candidate.profile is not None
                                else state.requested_step_provider or state.step_provider
                            ),
                            requested_model=(
                                candidate.model
                                if candidate is not None and candidate.model is not None
                                else state.model
                            ),
                            admission_status="pending",
                            transition=("primary" if ordinal == 0 else "configured_alternative"),
                        )
                    state.execution_selection = replace(
                        state.execution_selection,
                        attempts=(*state.execution_selection.attempts, attempt),
                    )
                    _persist_selection()
                    try:
                        if (
                            terminal := await _te_pkg._prepare_dispatch_backend(
                                state, candidate, ordinal
                            )
                        ) is not None:
                            return terminal
                        assert state._backend_authority is not None
                        assert state.execution_selection is not None
                        attempt = replace(
                            state.execution_selection.attempts[-1],
                            effective_backend=state._backend_authority.backend,
                            effective_provider=(
                                state.provider_binding.provider
                                if state.provider_binding is not None
                                else ""
                            ),
                            effective_model=state.effective_model,
                            backend_source_path=state._backend_authority.key_path,
                            provider_source_path=(
                                state.provider_binding.provider_source.key_path
                                if state.provider_binding is not None
                                else ""
                            ),
                            model_source_path=(
                                state.model_pin.source.key_path
                                if state.model_pin is not None
                                else ""
                            ),
                        )
                        state.execution_selection = replace(
                            state.execution_selection,
                            attempts=(*state.execution_selection.attempts[:-1], attempt),
                        )
                        _persist_selection()
                        if state._candidate_rejection_reason is not None:
                            attempt = replace(
                                attempt,
                                admission_status="incompatible",
                                rejection_reason=state._candidate_rejection_reason,
                            )
                            state.execution_selection = replace(
                                state.execution_selection,
                                attempts=(*state.execution_selection.attempts[:-1], attempt),
                            )
                            _persist_selection()
                            continue

                        _te_pkg._prepare_dispatch_session(state)
                        resource_request = _ChildResourceOwnerRequest(
                            source_cwd=Path(state._invocation_cwd),
                            prepare=lambda owned_cwd: _prepare_owned_dispatch_session(
                                state, owned_cwd
                            ),
                            session_manager=state.tool_ctx.session_skill_manager,
                            generated_home_id=state._cleanup_session_id,
                            generated_home_materialized=lambda: (
                                state._generated_home_cleanup_required
                            ),
                            copied_snapshot_path=lambda: state._copied_snapshot_dir,
                            cleanup_errors_are_terminal=False,
                        )
                        resource_owner = _te_pkg.scoped_child_resource_owner(resource_request)
                        prepared = await resource_owner.__aenter__()
                        state._child_resource_owner = resource_owner
                        if prepared.value is not None:
                            return prepared.value
                        outcome = await _te_pkg._execute_and_finalize_run_skill(state)
                        if isinstance(outcome, CandidatePreSpawnRejection):
                            if state.contract_lifecycle.execution_started:
                                raise SkillContractError(
                                    "A started worker cannot advance to another candidate"
                                )
                            assert state.execution_selection is not None
                            rejected = replace(
                                state.execution_selection.attempts[-1],
                                admission_status="rejected",
                                rejection_reason=outcome.reason,
                                rate_limit_status=outcome.rate_limit.status,
                                rate_limit_type=outcome.rate_limit.limit_type,
                                rate_limit_resets_at_epoch=(outcome.rate_limit.resets_at_epoch),
                            )
                            state.execution_selection = replace(
                                state.execution_selection,
                                attempts=(*state.execution_selection.attempts[:-1], rejected),
                            )
                            _persist_selection()
                            continue
                        return outcome
                    finally:
                        if (
                            not state.contract_lifecycle.execution_started
                            and state.contract_lifecycle.correlation_key is not None
                        ):
                            state.contract_lifecycle.cleanup()
                            state.contract_lifecycle.correlation_key = None
                        if state._quota_lease is not None:
                            state._quota_lease.close()
                            state._quota_lease = None
                            state._quota_scope = ""
                        if (
                            state._explorer_launch_lease is not None
                            and not state.contract_lifecycle.execution_started
                        ):
                            exploration_store = state.tool_ctx.exploration_context_store
                            if exploration_store is not None:
                                _te_pkg._cleanup_explorer_launch(
                                    exploration_store,
                                    session_id=state._explorer_launch_lease.session_id,
                                    session_home=state._explorer_launch_lease.session_home,
                                    backend=state._explorer_launch_lease.backend,
                                )
                            state._explorer_launch_lease = None
                        if (
                            state._child_resource_owner is not None
                            and not state.contract_lifecycle.execution_started
                        ):
                            await state._child_resource_owner.__aexit__(None, None, None)
                            state._child_resource_owner = None
                        state.cwd = state._invocation_cwd

            assert state.execution_selection is not None
            state.execution_selection = replace(
                state.execution_selection, completed=True, terminal_candidate_id=None
            )
            _persist_selection()
            exhausted = SkillResult.crashed(
                exception=SkillContractError("candidate_exhausted"),
                skill_command=skill_command,
                order_id=order_id,
            )
            exhausted.candidate_exhausted = True
            exhausted.execution_selection = state.execution_selection
            blocked = [
                attempt
                for attempt in state.execution_selection.attempts
                if attempt.rate_limit_type
                or attempt.rejection_reason in {"observed_quota_blocked", "quota_exhausted"}
            ]
            if blocked:
                next_admission = min(
                    blocked,
                    key=lambda attempt: (
                        attempt.rate_limit_resets_at_epoch
                        if attempt.rate_limit_resets_at_epoch is not None
                        else float("inf")
                    ),
                )
                exhausted.needs_retry = True
                exhausted.retry_reason = RetryReason.RATE_LIMITED
                exhausted.api_failure = ApiFailureOutcome(
                    rate_limit=RateLimitWindow(
                        status="rejected",
                        limit_type=next_admission.rate_limit_type,
                        resets_at_epoch=next_admission.rate_limit_resets_at_epoch,
                    )
                )
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                exhausted.to_json(),
                child_session_id="",
            )
    except InfrastructureFaultError as exc:
        logger.error("run_skill unhandled infrastructure fault", exc_info=True)
        _unhandled_infra_fault_result = SkillResult.infrastructure_fault(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        )
        if state is not None and state._completion_invocation_id:
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                _unhandled_infra_fault_result.to_json(),
                child_session_id=_unhandled_infra_fault_result.session_id,
            )
        return _unhandled_infra_fault_result.to_json()
    except Exception as exc:
        logger.error("run_skill unhandled exception", exc_info=True)
        _unhandled_result = SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        )
        if state is not None and state._completion_invocation_id:
            return _te_pkg._finalize_run_skill_completion(
                state.tool_ctx,
                state._completion_invocation_id,
                _unhandled_result.to_json(),
                child_session_id=_unhandled_result.session_id,
            )
        return _unhandled_result.to_json()
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            logger.warning("run_skill cancelled", exc_info=True)
        if state is not None:
            _cmd = state.resolved_command if state.resolved_command is not None else skill_command
            _oid = state.effective_order_id or order_id
        else:
            _cmd = skill_command
            _oid = order_id
        _cancelled_result = SkillResult.cancelled(
            skill_command=_cmd,
            order_id=_oid,
        )
        if state is not None and state._completion_invocation_id:
            with anyio.CancelScope(shield=True):
                return _te_pkg._finalize_run_skill_completion(
                    state.tool_ctx,
                    state._completion_invocation_id,
                    _cancelled_result.to_json(),
                    child_session_id=_cancelled_result.session_id,
                )
        return _cancelled_result.to_json()
    finally:
        if state is not None:
            state._completion_authority = state.tool_ctx.run_skill_completion
            if (
                state._completion_invocation_id
                and state._completion_authority is not None
                and state._completion_authority.abort(state._completion_invocation_id)
            ):
                logger.error(
                    "run_skill_completion_invocation_escaped",
                    invocation_id=state._completion_invocation_id,
                )
            state.contract_lifecycle.cleanup()
            if state._tracker_key is not None:
                _release_context_tracker(state.tool_ctx, state._tracker_key)
            if state._explorer_launch_lease is not None:
                exploration_store = state.tool_ctx.exploration_context_store
                if exploration_store is None:
                    logger.warning(
                        "explorer_context_store_unavailable_during_cleanup",
                        session_id=state._explorer_launch_lease.session_id,
                    )
                else:
                    _te_pkg._cleanup_explorer_launch(
                        exploration_store,
                        session_id=state._explorer_launch_lease.session_id,
                        session_home=state._explorer_launch_lease.session_home,
                        backend=state._explorer_launch_lease.backend,
                    )
            if state._child_resource_owner is not None:
                await state._child_resource_owner.__aexit__(None, None, None)
            if state._sn_token is not None:
                _current_step_name.reset(state._sn_token)
            if state._oid_token is not None:
                _current_order_id.reset(state._oid_token)
