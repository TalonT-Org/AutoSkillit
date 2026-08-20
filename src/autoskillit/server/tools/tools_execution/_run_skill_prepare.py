"""run_skill prepare phase: order_id/model/provider resolution, backend
selection, closure/write-scope metadata, and the backend-compatibility gate.

Returns the terminal MCP response string when an early exit is warranted;
``None`` otherwise, in which case dispatch continues to the next phase.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    SkillContractError,
    SkillResult,
    closure_authority_spec_from_args,
    get_logger,
    parse_plan_paths,
    render_target_skill_command,
)
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.server._explorer_projection import (
    _resolve_exploration_applicabilities,
    _resolve_exploration_profile,
)
from autoskillit.server._guards import _check_dry_walkthrough, _check_input_contracts
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._execution_helpers import (
    aggregate_sandbox_overrides as _aggregate_sandbox_overrides,
)
from autoskillit.server.tools._execution_helpers import (
    bind_projection_backend,
    resolve_skill_dispatch_metadata,
)
from autoskillit.server.tools._types import ToolFailureEnvelope

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _ExplorerLaunchLease:
    """Cleanup authority for one bound explorer launch."""

    session_id: str
    session_home: Path
    backend: CodingAgentBackend | None


def _record_explorer_launch_lease(
    state: _RunSkillDispatchState,
    *,
    bound_session_id: str,
    session_home: Path,
    operation: str,
) -> CodingAgentBackend:
    """Transfer cleanup ownership before validating backend injection."""
    backend = state._effective_backend_obj
    state._explorer_launch_lease = _ExplorerLaunchLease(
        session_id=bound_session_id,
        session_home=session_home,
        backend=backend,
    )
    if backend is None:
        raise SkillContractError(f"Explorer {operation} requires the bound Codex backend")
    return backend


async def _prepare_dispatch_backend(state: _RunSkillDispatchState) -> str | None:
    await _te_pkg._notify(
        state.ctx,
        "info",
        f"run_skill: {state.skill_command[:80]}",
        "autoskillit.run_skill",
        extra={"cwd": state.cwd, "model": state.model or "default"},
    )

    from autoskillit.server import _get_config  # circular-break

    # Auto-enrich order_id from the fleet dispatcher's env variable when the
    # caller did not pass an explicit value. AUTOSKILLIT_DISPATCH_ID is injected
    # by fleet/_api.py into every L2 food truck session environment and inherited by all
    # sub-sessions, ensuring token log entries carry the correct order_id without
    # requiring recipe authors to thread it through every run_skill call.
    state.effective_order_id = state.order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    if (
        not state.resume_session_id
        and state._installed_execution is None
        and state.skill_inputs is None
    ):
        if (
            input_error := _check_input_contracts(
                state.skill_command, state.cwd, state.tool_ctx.input_contract_resolver
            )
        ) is not None:
            return input_error

    if _get_config().safety.require_dry_walkthrough and state._installed_execution is None:
        if (gate_error := _check_dry_walkthrough(state.skill_command, state.cwd)) is not None:
            return gate_error

    if state.tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})

    state.provider_extras = None
    state.profile_name_out = ""
    state.effective_model = state.model

    state._cfg = _get_config()
    state._in_fleet_dispatch = bool(os.environ.get(DISPATCH_ID_ENV_VAR))
    state._inspector_model = (
        os.environ.get(FLEET_INSPECTOR_MODEL_ENV_VAR) or state._cfg.fleet.inspector_model
        if state._in_fleet_dispatch
        else ""
    )

    # step_provider's execution-tuning fallback lives here (pre-gate,
    # profile-interplay semantics) rather than in the post-gate
    # fallback loop — see _EXECUTION_TUNING_EXTERNALLY_RESOLVED.
    if (
        not state.step_provider
        and state.step_name
        and state.tool_ctx.active_recipe_steps is not None
    ):
        _recipe_step_pre = state.tool_ctx.active_recipe_steps.get(state.step_name)
        if _recipe_step_pre is not None and _recipe_step_pre.provider:
            state.step_provider = _recipe_step_pre.provider
            logger.warning(
                "step_provider_resolved_from_recipe",
                step=state.step_name,
                provider=state.step_provider,
            )

    if _te_pkg.is_feature_enabled(
        "providers", state._cfg.features, experimental_enabled=state._cfg.experimental_enabled
    ):
        from autoskillit.server._guards import (  # circular-break
            _resolve_model_as_profile,
            _resolve_provider_profile,
        )

        state._profile, state._env_dict = _resolve_provider_profile(
            state.step_name or "",
            state.tool_ctx.recipe_name or "",
            state._cfg.providers,
            step_provider=state.step_provider or "",
        )
        if state._profile != "anthropic":
            state.provider_extras = state._env_dict
            state.profile_name_out = state._profile
        else:
            state.effective_model, prof_name, prof_extras = _resolve_model_as_profile(
                state.model, state._cfg.providers
            )
            if prof_extras is not None:
                state.provider_extras = prof_extras
                state.profile_name_out = prof_name

    if state._cfg.model.model_override:
        state.effective_model = state._cfg.model.model_override
    else:
        if state.tool_ctx.recipe_name:
            state._mo_recipe_map = state._cfg.providers.model_overrides.get(
                state.tool_ctx.recipe_name
            )
            if state._mo_recipe_map:
                state._step_mo = (
                    state._mo_recipe_map.get(state.step_name) if state.step_name else None
                )
                if state._step_mo is None:
                    state._step_mo = state._mo_recipe_map.get("*")
                if state._step_mo:
                    state.effective_model = state._step_mo

    # The fresh branch resolved the complete effective invocation before any
    # notification or provider/executor work. Backend-specific rendering waits
    # until capability-driven backend selection is complete.
    state._stored_contract = (
        state._stored_contract_entry.contract if state._stored_contract_entry is not None else None
    )
    state.resolved_command = (
        state._stored_contract.resolved_command
        if state._stored_contract is not None
        else state.child_skill_command
    )
    state._effective_skill_contract = (
        state.invocation if state.invocation is not None else state._stored_contract
    )

    # Config pins and the global configured backend are the only fresh
    # launch authorities. Provider/model/capability metadata is never
    # permitted to select a backend.
    from autoskillit.server._guards import _resolve_backend_override  # circular-break

    state._explicit_resolution = _resolve_backend_override(
        state.step_name or "",
        state.tool_ctx.recipe_name or "",
        state._cfg.agent_backend,
    )
    state._skill_caps = (
        state.invocation.capability_union
        if state.invocation is not None
        else state._stored_contract.capability_union
        if state._stored_contract is not None
        else frozenset()
    )
    state._sandbox_overrides = _aggregate_sandbox_overrides(state._skill_caps)
    state._network_access = (
        "sandbox_workspace_write.network_access=true" in state._sandbox_overrides
    )
    if state._stored_contract is not None:
        if state._resume_backend_authority is None or state._resume_backend_obj is None:
            raise SkillContractError("Resume launch authority is unavailable")
        state._backend_authority = state._resume_backend_authority
        state._effective_backend_obj = state._resume_backend_obj
    elif state._explicit_resolution is not None:
        authority_kind = state._explicit_resolution.kind
        if authority_kind is None:
            raise SkillContractError("Explicit backend resolution lacks typed authority")
        authority_tier = (
            BackendAuthorityTier.RECIPE
            if authority_kind is BackendAuthorityKind.RECIPE
            else BackendAuthorityTier.STEP
        )
        state._backend_authority = BackendAuthority(
            backend=state._explicit_resolution.backend,
            kind=authority_kind,
            tier=authority_tier,
            key_path=state._explicit_resolution.key_path,
        )
        state._effective_backend_obj = state.tool_ctx.launch_resolver.backend_for_authority(
            state._backend_authority
        )
    else:
        if state.tool_ctx.backend is None:
            raise SkillContractError("Global launch backend is unavailable")
        state._backend_authority = BackendAuthority(
            backend=state.tool_ctx.backend.name,
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path="agent_backend.backend",
        )
        state._effective_backend_obj = state.tool_ctx.launch_resolver.backend_for_authority(
            state._backend_authority
        )

    if state._explicit_resolution is not None:
        state._explicit_binary = state._effective_backend_obj.capabilities.process_name
        if state._explicit_binary and shutil.which(state._explicit_binary) is None:
            return SkillResult.crashed(
                exception=RuntimeError(
                    f"Step explicitly pinned to backend "
                    f"{state._explicit_resolution.backend!r} but required binary "
                    f"{state._explicit_binary!r} is not found on PATH."
                ),
                skill_command=state.resolved_command,
                order_id=state.effective_order_id,
            ).to_json()
    if state._stored_contract is None:
        if state.projection_context is None:
            raise SkillContractError("Fresh execution lacks projection authority")
        state._fresh_parent_sandbox_mode = (
            "read-only"
            if state.tool_ctx.read_only_resolver
            and state.tool_ctx.read_only_resolver(state.skill_command)
            else "workspace-write"
        )
        state._active_exploration_applicabilities = _resolve_exploration_applicabilities(
            state.projection_context,
            skill_inputs=state.skill_inputs,
            output_dir=state.output_dir,
        )
        state.projection_context = bind_projection_backend(
            state.projection_context,
            state._effective_backend_obj,
            resolution=state._explicit_resolution,
            parent_sandbox_mode=state._fresh_parent_sandbox_mode,
            resolved_exploration_profile=_resolve_exploration_profile(
                state.tool_ctx,
                state.projection_context,
                active_applicabilities=state._active_exploration_applicabilities,
            ),
            active_exploration_applicabilities=state._active_exploration_applicabilities,
        )
    state._explorer_parent_identity = _te_pkg._explorer_launch_identity(state.invocation)
    if state.invocation is not None and state._stored_contract is None:
        if state.invocation.root.source_ref is None:
            raise SkillContractError("Effective skill source identity is missing")
        state.resolved_command = render_target_skill_command(
            state.child_skill_command,
            state.invocation.root.source_ref,
            (
                state._effective_backend_obj.conventions
                if state._effective_backend_obj is not None
                else None
            ),
        )

    if state._backend_authority.kind is not BackendAuthorityKind.GLOBAL:
        logger.info(
            "backend_override_activated",
            reason=state._backend_authority.key_path,
            skill=state.skill_command,
            original_backend=state.tool_ctx.backend.name if state.tool_ctx.backend else "none",
            target_backend=state._backend_authority.backend,
        )

    state.expected_output_patterns, state.write_spec, state._skill_contract = (
        resolve_skill_dispatch_metadata(
            state.tool_ctx,
            state.skill_command,
            state._stored_contract,
            audit_output_mode=state._audit_output_mode,
        )
    )

    # Resolve closure spec from explicit MCP tool parameters.
    # Closure args are first-class parameters (not embedded in skill_command text)
    # because the skill_command string is prompt text consumed by the LLM session,
    # not parsed by Python code.
    state.closure_spec = closure_authority_spec_from_args(
        path=state.closure_authority_path or None,
        hash_=state.closure_authority_hash or None,
        plan_paths=parse_plan_paths(state.closure_plan_paths) if state.closure_plan_paths else (),
        base_sha=state.closure_base_sha,
        diff_sha=state.closure_diff_sha,
        target_sha=state.closure_target_sha,
    )

    # Build validated add_dirs via DefaultSessionSkillManager
    from uuid import uuid4

    # Backend compatibility gate — fail-closed, fires before replay and live session paths.
    if compat_error := _te_pkg._check_backend_compat(
        skill_command=state.skill_command,
        resolved_command=state.resolved_command,
        effective_order_id=state.effective_order_id,
        target_name=state.target_name,
        skill_info=state._effective_skill_contract,
        effective_backend_obj=state._effective_backend_obj,
        skill_resolver=(
            state._effective_skill_resolver
            if state._effective_skill_resolver is not None
            else state._stored_contract_entry
        ),
    ):
        return compat_error

    # Server-side recipe step parameter resolution.
    # When a step_name is provided and the recipe's step definition is cached,
    # auto-fill parameters the LLM may have omitted.
    if state.step_name and state.tool_ctx.active_recipe_steps is not None:
        _recipe_step = state.tool_ctx.active_recipe_steps.get(state.step_name)
        if _recipe_step is not None:
            if not state.output_dir and "output_dir" in _recipe_step.with_args:
                _recipe_output_dir = _recipe_step.with_args["output_dir"]
                # Skip values containing unresolved template references —
                # load() returns raw YAML without ingredient resolution,
                # so ${{ context.* }} placeholders may survive.
                if "${{" not in _recipe_output_dir:
                    state.output_dir = _recipe_output_dir
                    logger.warning(
                        "output_dir_resolved_from_recipe",
                        step=state.step_name,
                        output_dir=state.output_dir,
                    )

            # Use each field's vacancy sentinel; zero is a valid explicit timeout.
            if (
                state.effective_model == ""
                and _recipe_step.model
                and "${{" not in _recipe_step.model
            ):
                # Skip values containing unresolved template references —
                # load() returns raw YAML without ingredient resolution,
                # so ${{ inputs.* }}/${{ context.* }} placeholders may
                # survive (see the output_dir fallback above for the
                # same guard). A raw template string is never a valid
                # --model value.
                state.effective_model = _recipe_step.model
                logger.warning(
                    "model_resolved_from_recipe",
                    step=state.step_name,
                    value=state.effective_model,
                )

            if state.stale_threshold is None and _recipe_step.stale_threshold is not None:
                state.stale_threshold = _recipe_step.stale_threshold
                logger.warning(
                    "stale_threshold_resolved_from_recipe",
                    step=state.step_name,
                    value=state.stale_threshold,
                )

            if state.idle_output_timeout is None and _recipe_step.idle_output_timeout is not None:
                state.idle_output_timeout = _recipe_step.idle_output_timeout
                logger.warning(
                    "idle_output_timeout_resolved_from_recipe",
                    step=state.step_name,
                    value=state.idle_output_timeout,
                )

    state.closure_report_root = None
    if state.output_dir and state.closure_spec:
        state._closure_root = Path(state.output_dir)
        if not state._closure_root.is_absolute():
            state._closure_root = Path(state.cwd) / state.output_dir
        state.closure_report_root = state._closure_root
    elif state.closure_spec and not state.output_dir:
        return json.dumps(
            ToolFailureEnvelope(
                success=False,
                error=(
                    "closure_spec requires output_dir to locate"
                    " the closure report, but output_dir is empty"
                ),
                stage="validate_args:run_skill",
                retriable=False,
            )
        )

    state.write_watch_dirs = []
    if state.output_dir:
        resolved_dir = Path(state.output_dir)
        if not resolved_dir.is_absolute():
            resolved_dir = Path(state.cwd) / state.output_dir
        state.write_watch_dirs.append(resolved_dir)

    if not state.write_watch_dirs:
        state._default_temp = _resolve_skill_temp_dir(state.cwd, state.skill_command)
        if state._default_temp:
            state.write_watch_dirs.append(state._default_temp)

    if state._stored_contract is not None:
        state.is_read_only = state._stored_contract.read_only
        state.scope_discipline_skill = state._stored_contract.scope_discipline
        state.completion_required = state._stored_contract.completion_required
    else:
        if state.projection_context is None:
            raise SkillContractError("Projection context was not prepared")
        state.is_read_only = state.projection_context.parent_sandbox_mode == "read-only"
        state.scope_discipline_skill = bool(
            state._skill_contract and state._skill_contract.scope_discipline
        )
        state.completion_required = bool(
            state.tool_ctx.completion_required_resolver
            and state.tool_ctx.completion_required_resolver(state.skill_command)
        )
    state.invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"
    return None
