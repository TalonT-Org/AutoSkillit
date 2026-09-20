"""run_skill session phase: skill-snapshot replay/materialization, closure
write-scope expansion, and native-shell lineage.

Returns the terminal MCP response string when an early exit is warranted;
``None`` otherwise, in which case dispatch continues to the finalize phase.
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import (
    LAUNCH_ID_ENV_VAR,
    SKILL_COMMAND_DISPLAY_MAX,
    WORKTREE_SKILLS,
    SkillContractError,
    SkillExecutionRole,
    SkillResult,
    WriteBehaviorSpec,
    destination_location,
    extract_skill_name,
    get_logger,
)
from autoskillit.core import current_order_id as _current_order_id
from autoskillit.core import current_step_name as _current_step_name
from autoskillit.core import (
    resolve_skill_temp_dir as _resolve_skill_temp_dir,
)
from autoskillit.pipeline import canonical_step_name as _canonical_step_name
from autoskillit.pipeline import gate_error_result
from autoskillit.server._explorer_projection import _build_requested_execution_identity
from autoskillit.server.lifecycle._guards import _validate_skill_command
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._execution_helpers import (
    bind_projection_backend,
    build_fresh_projection_context,
    build_validated_skill_dispatch_contract,
    invocation_member_names,
    propagate_session_deadline,
)
from autoskillit.server.tools._execution_helpers import (
    build_skill_session_contract as _build_skill_session_contract,
)
from autoskillit.server.tools._execution_helpers import (
    compute_write_prefixes as _compute_write_prefixes,
)
from autoskillit.server.tools._execution_helpers import (
    make_project_skill_resolver as _make_project_skill_resolver,
)
from autoskillit.server.tools._execution_helpers import (
    scope_covers_cwd as _scope_covers_cwd,
)
from autoskillit.server.tools._execution_helpers import (
    serialize_skill_contract as _serialize_skill_contract,
)
from autoskillit.server.tools._native_shell_capture import prepare_skill_native_shell_lineage
from autoskillit.server.tools._types import ToolFailureEnvelope

if TYPE_CHECKING:
    from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState

logger = get_logger(__name__)


def _mint_fresh_explorer_binding(
    state: _RunSkillDispatchState,
    session_id: str,
    authority_home: Path,
) -> dict[str, dict[str, str]] | None:
    assert state.projection_context is not None
    binding_env = _te_pkg._issue_explorer_binding_env(
        state.tool_ctx,
        session_id=session_id,
        projection_context=state.projection_context,
        identity=state._explorer_parent_identity,
        authority_home=authority_home,
    )
    if binding_env is not None:
        _te_pkg._record_explorer_launch_lease(
            state,
            bound_session_id=session_id,
            session_home=authority_home,
            operation="launch",
        )
    return binding_env


def _prepare_dispatch_session(state: _RunSkillDispatchState) -> None:
    """Select the session branch and reserve its identity without allocating it."""
    assert state.resolved_command is not None
    state._cleanup_session_id = f"headless-{uuid4().hex[:12]}"
    state._generated_home_cleanup_required = False
    state._copied_snapshot_dir = None


def _resolve_fresh_invocation(state: _RunSkillDispatchState) -> str | None:
    """Resolve the fresh skill invocation and projection context."""
    if (cmd_error := _validate_skill_command(state.skill_command)) is not None:
        return cmd_error
    state._effective_skill_resolver = state.tool_ctx.skill_resolver
    if state._effective_skill_resolver is None:
        state._effective_skill_resolver = _make_project_skill_resolver()
    state.target_name = extract_skill_name(state.skill_command)
    if state.target_name is None:
        return SkillResult.crashed(
            exception=SkillContractError(
                f"Cannot resolve a logical skill target from {state.skill_command!r}"
            ),
            skill_command=state.skill_command,
            order_id=state.order_id,
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
        state.projection_context = build_fresh_projection_context(state.cwd, state.invocation)
    except SkillContractError as exc:
        return SkillResult.crashed(
            exception=exc,
            skill_command=state.skill_command,
            order_id=state.order_id,
        ).to_json()
    return None


def _resolve_dispatch_paths(
    state: _RunSkillDispatchState,
    *,
    base_cwd: Path,
) -> str | None:
    """Resolve closure and write paths relative to the current dispatch cwd."""
    state.closure_report_root = None
    if state.output_dir and state.closure_spec:
        state._closure_root = Path(state.output_dir)
        if not state._closure_root.is_absolute():
            state._closure_root = base_cwd / state.output_dir
        state.closure_report_root = state._closure_root
    elif state.closure_spec and not state.output_dir:
        return json.dumps(
            ToolFailureEnvelope(
                success=False,
                error=(
                    "closure_spec requires output_dir to locate the closure report, "
                    "but output_dir is empty"
                ),
                stage="validate_args:run_skill",
                retriable=False,
            )
        )

    state.write_watch_dirs = []
    if state.output_dir:
        resolved_dir = Path(state.output_dir)
        if not resolved_dir.is_absolute():
            resolved_dir = base_cwd / state.output_dir
        state.write_watch_dirs.append(resolved_dir)
    if not state.write_watch_dirs:
        state._default_temp = _resolve_skill_temp_dir(str(base_cwd), state.skill_command)
        if state._default_temp:
            state.write_watch_dirs.append(state._default_temp)
    return None


def _rebuild_owned_dispatch_context(
    state: _RunSkillDispatchState,
    owned_cwd: Path,
) -> str | None:
    """Rebind fresh projection and cwd-derived write scope inside child ownership."""
    state._owned_cwd = owned_cwd
    state.cwd = str(owned_cwd)
    if state._stored_contract_entry is None:
        assert state.invocation is not None
        previous_projection = state.projection_context
        if previous_projection is None:
            raise SkillContractError("Fresh execution lacks projection authority")
        state.projection_context = bind_projection_backend(
            build_fresh_projection_context(
                state.cwd,
                state.invocation,
                adaptation_context=previous_projection.adaptation_context,
            ),
            state._effective_backend_obj,
            resolution=state._explicit_resolution,
            parent_sandbox_mode=state._fresh_parent_sandbox_mode or "workspace-write",
            resolved_exploration_profile=previous_projection.resolved_exploration_profile,
            active_exploration_applicabilities=state._active_exploration_applicabilities,
        )

    return _resolve_dispatch_paths(state, base_cwd=owned_cwd)


def _restore_or_replay_snapshot(state: _RunSkillDispatchState) -> str | None:
    """Restore a direct snapshot or replay the runner snapshot when available."""
    assert state.resolved_command is not None
    assert state.skill_add_dirs is not None
    if state._stored_contract_entry is not None:
        manager = state.tool_ctx.session_skill_manager
        if manager is None:
            return SkillResult.crashed(
                exception=SkillContractError(
                    f"Cannot materialize direct skill {state.target_name!r}: "
                    "session skill manager is unavailable"
                ),
                skill_command=state.resolved_command,
                session_id=state.resume_session_id,
                order_id=state.effective_order_id,
            ).to_json()
        session_id = state._cleanup_session_id
        assert session_id is not None
        if state.projection_context is None:
            raise SkillContractError("Projection context was not prepared")

        # restore_snapshot_session may create the home before failing, so reserve
        # cleanup ownership before invoking it.
        state._generated_home_cleanup_required = True
        state._restored = manager.restore_snapshot_session(
            session_id,
            state._stored_contract_entry.snapshot_dir,
            state.projection_context,
        )
        if not state._restored.session_home:
            raise SkillContractError("Restored session lacks a bound generated home")
        state.skill_add_dirs.append(state._restored)
    elif (
        state.step_name
        and state._runner is not None
        and getattr(state._runner, "skill_snapshots", None)
        and hasattr(state._runner, "restore_skill_snapshot")
        and state.tool_ctx.ephemeral_root is not None
    ):
        state._ephemeral_root = state.tool_ctx.ephemeral_root
        if state.invocation is None:
            raise SkillContractError("Fresh replay requires a validated effective invocation")
        if hasattr(state._runner, "validate_skill_snapshot"):
            state._runner.validate_skill_snapshot(  # type: ignore[attr-defined]
                state.step_name,
                invocation_member_names(state.invocation),
            )
        session_id = state._cleanup_session_id
        assert session_id is not None
        state._copied_snapshot_dir = state._ephemeral_root / session_id
        state._restored = state._runner.restore_skill_snapshot(  # type: ignore[attr-defined]
            state.step_name, state._ephemeral_root, session_id
        )
        if state._restored is not None:
            if not Path(state._restored.path).is_dir():
                logger.warning(
                    "stale_snapshot_path",
                    session_id=session_id,
                    path=state._restored.path,
                )
                return SkillResult.crashed(
                    exception=RuntimeError(
                        f"Snapshot path {state._restored.path!r} does not exist. "
                        f"The /dev/shm directory may have been reclaimed."
                    ),
                    skill_command=state.resolved_command,
                    session_id=session_id,
                    order_id=state.effective_order_id,
                ).to_json()
            state.skill_add_dirs.append(state._restored)
            state.replay_snapshot_used = True
            logger.debug(
                "replay_skill_snapshot_restored",
                step=state.step_name,
                session_id=session_id,
            )
    return None


def _resolve_caller_session(state: _RunSkillDispatchState) -> str | None:
    """Resolve the current launch's exact caller session before dispatching."""
    state._launch_id = os.environ.get(LAUNCH_ID_ENV_VAR, "")
    if state._launch_id:
        state._session_registry = _te_pkg.read_registry(state.tool_ctx.project_dir)
        state._registry_row = (
            state._session_registry.get(state._launch_id)
            if isinstance(state._session_registry, Mapping)
            else None
        )
        state._registered_session_id = (
            state._registry_row.get("claude_session_id")
            if isinstance(state._registry_row, Mapping)
            else None
        )
        if not (
            isinstance(state._registered_session_id, str)
            and bool(state._registered_session_id.strip())
        ):
            return json.dumps(
                ToolFailureEnvelope(
                    success=False,
                    error=(
                        "run_skill: current launch has no exact caller session binding: "
                        f"{state._launch_id!r}"
                    ),
                    stage="preflight:caller_session",
                    retriable=False,
                )
            )
        state._caller_hook_session_id = state._registered_session_id
    else:
        state._caller_hook_session_id = _te_pkg.find_caller_session_id(
            project_dir=state.tool_ctx.project_dir
        )
    return None


async def _prepare_owned_dispatch_session(
    state: _RunSkillDispatchState,
    owned_cwd: Path,
) -> str | None:
    assert state.resolved_command is not None
    assert state._contract_store is not None
    assert state._cfg is not None
    assert state.expected_output_patterns is not None
    if (terminal := _rebuild_owned_dispatch_context(state, owned_cwd)) is not None:
        return terminal
    assert state.write_watch_dirs is not None
    state.skill_add_dirs = []
    state.replay_snapshot_used = False
    state._runner = state.tool_ctx.runner
    state._ephemeral_root = None
    state._restored = None
    if (terminal := _restore_or_replay_snapshot(state)) is not None:
        return terminal

    if (
        state._stored_contract_entry is None
        and not state.replay_snapshot_used
        and state.tool_ctx.session_skill_manager is not None
    ):
        if state.invocation is None:
            raise SkillContractError("Fresh execution requires a resolved skill invocation")
        session_id = state._cleanup_session_id
        assert session_id is not None
        if state.projection_context is None:
            raise SkillContractError("Projection context was not prepared")

        # materialize_invocation may create the home before failing, so its
        # reserved identity is cleanup-owned before the call begins.
        state._generated_home_cleanup_required = True
        session_root = state.tool_ctx.session_skill_manager.materialize_invocation(
            session_id,
            state.invocation,
            state.projection_context,
            explorer_binding_env_factory=functools.partial(
                _mint_fresh_explorer_binding, state, session_id
            ),
        )
        if not session_id or not state.tool_ctx.session_skill_manager.validate_session_exists(
            session_id
        ):
            logger.warning(
                "stale_session_path",
                session_id=session_id,
                path=session_root.path,
            )
            return SkillResult.crashed(
                exception=RuntimeError(
                    f"Session path {session_root.path!r} does not exist. "
                    f"The /dev/shm directory may have been reclaimed."
                ),
                skill_command=state.resolved_command,
                session_id=session_id,
                order_id=state.effective_order_id,
            ).to_json()
        state.skill_add_dirs.append(session_root)

    if state._stored_contract_entry is not None and state._explorer_parent_identity is not None:
        restored_add_dir = state.skill_add_dirs[0]
        restored_session_home = Path(restored_add_dir.session_home)
        if not restored_session_home.is_dir():
            return SkillResult.crashed(
                exception=RuntimeError(
                    f"Restored session home {str(restored_session_home)!r} does not exist."
                ),
                skill_command=state.resolved_command,
                session_id=state.resume_session_id,
                order_id=state.effective_order_id,
            ).to_json()
        if state.projection_context is None:
            raise SkillContractError("Projection context was not prepared")
        _explorer_binding_env = _te_pkg._issue_explorer_binding_env(
            state.tool_ctx,
            session_id=state.resume_session_id,
            projection_context=state.projection_context,
            identity=state._explorer_parent_identity,
            authority_home=restored_session_home,
        )
        if _explorer_binding_env is not None:
            assert state.resume_session_id is not None
            bound_backend = _te_pkg._record_explorer_launch_lease(
                state,
                bound_session_id=state.resume_session_id,
                session_home=restored_session_home,
                operation="resume",
            )
            bound_backend.refresh_explorer_binding_env(
                restored_session_home,
                _explorer_binding_env,
            )

    # Both fresh and rehydrated invocations extend scope from their
    # validated closure, independent of whether a snapshot was replayed.
    if state.invocation is not None:
        state.write_watch_dirs.extend(
            _te_pkg.resolve_closure_write_dirs(
                state.invocation.closure,
                state.cwd,
                state.write_watch_dirs,
            )
        )
        root_boundary = state.invocation.root.write_paths
        if state.output_dir and root_boundary is not None:
            declared_dirs = _te_pkg.resolve_closure_write_dirs((state.invocation.root,), state.cwd)
            if not state.write_watch_dirs:
                return json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error="run_skill output_dir produced no resolvable write directory",
                        stage="validate_args:run_skill",
                        retriable=False,
                    )
                )
            requested = destination_location(state.write_watch_dirs[0])
            if not any(requested.is_relative_to(directory) for directory in declared_dirs):
                return json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error="run_skill output_dir is outside the skill's declared write_paths",
                        stage="validate_args:run_skill",
                        retriable=False,
                    )
                )

    # _run_skill_dispatch.py's `if state.invocation is None or state.projection_context
    # is None: raise` guard (run before _admit_recipe_execution) already guarantees
    # this is bound on every path that reaches here.
    assert state.projection_context is not None
    state._capability_contract = build_validated_skill_dispatch_contract(
        state.projection_context,
        state.skill_add_dirs,
        state._stored_contract,
    )
    if state._stored_contract is not None:
        state._execution_identity = state._stored_contract.execution_identity
    else:
        state._execution_identity = _build_requested_execution_identity(
            projection_context=state.projection_context,
            target_name=state.target_name,
            skill_add_dirs=state.skill_add_dirs,
            effective_backend=state._effective_backend_obj,
            effective_model=state.effective_model,
            explicit_resolution=state._explicit_resolution,
        )
    if state.invocation is not None and state._stored_contract is None:
        if not state.skill_add_dirs:
            raise SkillContractError("Fresh execution requires a materialized skill snapshot")
        if state.projection_context is None:
            raise SkillContractError("Projection context was not prepared")
        state._session_contract, state._session_snapshot = _build_skill_session_contract(
            session_root=state.skill_add_dirs[0],
            invocation=state.invocation,
            projection_context=state.projection_context,
            member_names=state._capability_contract.member_names,
            resolved_command=state.resolved_command,
            expected_output_patterns=tuple(state.expected_output_patterns),
            write_behavior=state.write_spec or WriteBehaviorSpec(),
            read_only=state.is_read_only,
            scope_discipline=state.scope_discipline_skill,
            completion_required=state.completion_required,
            skill_contract_json=_serialize_skill_contract(state._skill_contract),
            execution_identity=state._execution_identity,
        )

    state._lineage_store = state.tool_ctx.managed_headless_session_lineage_store
    state._lineage_preparation = prepare_skill_native_shell_lineage(
        store=state._lineage_store,
        backend=state._effective_backend_obj,
        lineage_anchor=Path(state._capability_contract.cwd),
        stored_reference=getattr(state._stored_contract_entry, "managed_lineage_ref", None),
        resume_session_id=state.resume_session_id,
        requested_mode=state.native_shell_capture_mode,
        is_resume=state._stored_contract_entry is not None,
    )
    state._native_shell_capture_decision = state._lineage_preparation.decision
    state._managed_lineage_ref = state._lineage_preparation.reference
    if state._stored_contract_entry is None:
        if state._session_contract is None or state._session_snapshot is None:
            raise SkillContractError(
                "Fresh execution did not produce a provisional skill contract"
            )
        state.contract_lifecycle.correlation_key = state._contract_store.create_provisional(
            contract=state._session_contract,
            snapshot=state._session_snapshot,
            managed_lineage_ref=state._managed_lineage_ref,
        )
    state.allowed_write_prefix = ""
    state.allowed_write_prefixes = ()
    if state.write_watch_dirs:
        state.allowed_write_prefix, state.allowed_write_prefixes = _compute_write_prefixes(
            state.write_watch_dirs, state.cwd, state.skill_command
        )
    elif state.is_read_only:
        state._skill_temp_name = state.target_name or ""
        if state._skill_temp_name:
            state.allowed_write_prefix = os.path.join(
                state.cwd, ".autoskillit", "temp", state._skill_temp_name, ""
            )
        else:
            logger.warning(
                "read_only_skill_no_target_name",
                skill_command=state.skill_command[:SKILL_COMMAND_DISPLAY_MAX],
            )
    # Preflight: for WORKTREE_SKILLS dispatches, the computed scope must cover cwd
    # so the session can write to its own tracked tree. Fail-fast BEFORE spawning
    # a session — otherwise the session locks itself out and burns N turns.
    if (
        state.allowed_write_prefixes
        and state.target_name
        and state.target_name in WORKTREE_SKILLS
        and state.cwd
    ):
        if not _scope_covers_cwd(state.allowed_write_prefixes, state.cwd):
            return gate_error_result(
                f"Write scope does not cover target worktree: "
                f"cwd={state.cwd!r} not under any allowed prefix "
                f"{state.allowed_write_prefixes!r}. "
                f"Likely missing output_dir or malformed dispatch."
            )

    state._sn_token = _current_step_name.set(_canonical_step_name(state.step_name))
    state._oid_token = _current_order_id.set(state.effective_order_id)

    state._marker_dir = (
        state.tool_ctx.backend.session_locator().project_log_dir(str(state.tool_ctx.project_dir))
        if state.tool_ctx.backend is not None
        else None
    )
    if (terminal := _resolve_caller_session(state)) is not None:
        return terminal

    # Propagate AUTOSKILLIT_SESSION_DEADLINE to L1 sessions.
    state.provider_extras = propagate_session_deadline(
        state._invocation_deadline_epoch,
        state.provider_extras,
    )
    return None
