"""MCP tool handler: run_skill. Orchestrates the admission, prepare, session
and finalize dispatch phases over a shared ``_RunSkillDispatchState``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    CODEX_SESSIONS_SUBDIR,
    InfrastructureFaultError,
    SkillContractError,
    SkillExecutionRole,
    SkillResult,
    extract_skill_name,
    get_logger,
    read_tracker_authority,
)
from autoskillit.core import current_order_id as _current_order_id
from autoskillit.core import current_step_name as _current_step_name
from autoskillit.fleet import warm_failure_path_imports
from autoskillit.server import mcp
from autoskillit.server._guards import (
    _require_enabled,
    _require_orchestrator_exact,
    _validate_skill_command,
)
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_execution import get_recipe_execution
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import build_fresh_projection_context
from autoskillit.server.tools._execution_helpers import (
    make_project_skill_resolver as _make_project_skill_resolver,
)
from autoskillit.server.tools._execution_helpers import (
    rehydrate_skill_invocation as _rehydrate_skill_invocation,
)
from autoskillit.server.tools._execution_helpers import (
    validate_resumed_skill_contract as _validate_resumed_skill_contract,
)
from autoskillit.server.tools._types import deny_envelope
from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState
from autoskillit.server.tools.tools_pipeline_tracker import (
    _authority_blocks_dependency_check,
    _release_context_tracker,
    _select_tracker_authority,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)


# RecipeStep fallbacks for execution-tuning parameters left at their vacancy sentinel.
# `_run_skill_prepare.py` keeps explicit branches because the sentinels differ by type.
_EXECUTION_TUNING_STEP_FIELDS: Mapping[str, str] = {
    "model": "model",
    "stale_threshold": "stale_threshold",
    "idle_output_timeout": "idle_output_timeout",
}
# Execution-tuning parameters resolved outside the prepare-phase fallback block.
_EXECUTION_TUNING_EXTERNALLY_RESOLVED: Mapping[str, str] = {
    # Pre-gate profile resolution — see the step_provider_resolved_from_recipe
    # block earlier in run_skill().
    "step_provider": "provider",
}


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
            try:
                state._stored_contract_entry = state._contract_store.load(resume_session_id)
                state._resume_launch_contract = (
                    state._stored_contract_entry.contract.launch_contract
                )
                if state._resume_launch_contract is None:
                    raise SkillContractError("Resume contract has no resolved launch contract")
                state._resume_backend_authority = state._resume_launch_contract.backend_authority
                state._resume_backend_obj = state.tool_ctx.launch_resolver.backend_for_authority(
                    state._resume_backend_authority
                )
                _validate_resumed_skill_contract(
                    state._stored_contract_entry.contract,
                    cwd=cwd,
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
                        f"Cannot resume session {resume_session_id!r}: {exc}"
                    ),
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            state.contract_lifecycle.bound_session_id = resume_session_id
            state.target_name = state._stored_contract_entry.contract.root_name
        else:
            if (cmd_error := _validate_skill_command(skill_command)) is not None:
                return cmd_error
            state._effective_skill_resolver = state.tool_ctx.skill_resolver
            if state._effective_skill_resolver is None:
                state._effective_skill_resolver = _make_project_skill_resolver()
            state.target_name = extract_skill_name(skill_command)
            if state.target_name is None:
                return SkillResult.crashed(
                    exception=SkillContractError(
                        f"Cannot resolve a logical skill target from {skill_command!r}"
                    ),
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            try:
                state.invocation = state._effective_skill_resolver.resolve_invocation(
                    state.target_name,
                    state.tool_ctx.project_dir,
                    SkillExecutionRole.SESSION,
                    visibility=state.tool_ctx.config.skill_visibility_spec(),
                    recipe_packs=state.tool_ctx.active_recipe_packs,
                    recipe_features=state.tool_ctx.active_recipe_features,
                )
                state.projection_context = build_fresh_projection_context(cwd, state.invocation)
            except SkillContractError as exc:
                return SkillResult.crashed(
                    exception=exc,
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            if (
                state._installed_execution is None
                and not step_name
                and state.tool_ctx.active_recipe_steps
            ):
                _resolved, _ambiguous = _te_pkg._resolve_step_name_from_recipe(
                    skill_command, state.tool_ctx.active_recipe_steps
                )
                if state._tracker_target is not None and state._tracker_lease is not None:
                    state._tracker_authority = read_tracker_authority(
                        state._tracker_target, state._tracker_lease
                    )
                if _resolved:
                    step_name = _resolved
                    state.step_name = _resolved
                    logger.warning(
                        "step_name_resolved_from_recipe",
                        step=step_name,
                        command=skill_command[:80],
                    )
                    if (
                        _lock_denial := _te_pkg._check_ingredient_locks(step_name, order_id)
                    ) is not None:
                        return _lock_denial
                    if (
                        _dep_denial := _te_pkg._check_pipeline_deps(
                            step_name, state._tracker_authority
                        )
                    ) is not None:
                        return _dep_denial
                    if (
                        _plan_path_denial := _te_pkg._check_review_approach_plan_path(
                            step_name, skill_command
                        )
                    ) is not None:
                        return _plan_path_denial
                elif _ambiguous:
                    if _authority_blocks_dependency_check(state._tracker_authority):
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
                elif _te_pkg._has_active_locks(order_id):
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
                elif _authority_blocks_dependency_check(state._tracker_authority):
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
        if state.invocation is None or state.projection_context is None:
            raise SkillContractError("Skill dispatch branches did not produce a bound contract")

        if (terminal := _te_pkg._admit_recipe_execution(state)) is not None:
            return terminal

        with structlog.contextvars.bound_contextvars(tool="run_skill", cwd=cwd):
            logger.info("run_skill", command=skill_command[:80], cwd=cwd)
            if (terminal := await _te_pkg._prepare_dispatch_backend(state)) is not None:
                return terminal
            if (terminal := _te_pkg._prepare_dispatch_session(state)) is not None:
                return terminal
            return await _te_pkg._execute_and_finalize_run_skill(state)
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
            if state._sn_token is not None:
                _current_step_name.reset(state._sn_token)
            if state._oid_token is not None:
                _current_order_id.reset(state._oid_token)
            state._sid = state._cleanup_session_id
            if state._sid is not None:
                state._ssm = state.tool_ctx.session_skill_manager
                if state._ssm is not None:
                    try:
                        state._ssm.cleanup_session(state._sid)
                    except Exception:
                        logger.warning(
                            "session_skill_cleanup_failed",
                            session_id=state._sid,
                            exc_info=True,
                        )
                elif state.tool_ctx.ephemeral_root is not None:
                    state._cleanup_dir = state.tool_ctx.ephemeral_root / state._sid
                    if state._cleanup_dir.is_dir():
                        try:
                            shutil.rmtree(state._cleanup_dir)
                        except Exception:
                            logger.warning(
                                "session_dir_rmtree_failed",
                                path=str(state._cleanup_dir),
                                exc_info=True,
                            )
                    else:
                        state._codex_fallback = (
                            state.tool_ctx.temp_dir / CODEX_SESSIONS_SUBDIR / state._sid
                        )
                        if state._codex_fallback.is_dir():
                            try:
                                shutil.rmtree(state._codex_fallback)
                            except Exception:
                                logger.warning(
                                    "session_dir_rmtree_failed",
                                    path=str(state._codex_fallback),
                                    exc_info=True,
                                )
