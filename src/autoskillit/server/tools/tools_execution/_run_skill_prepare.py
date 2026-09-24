"""run_skill prepare phase: order_id/model/provider resolution, backend
selection, closure/write-scope metadata, and the backend-compatibility gate.

Returns the terminal MCP response string when an early exit is warranted;
``None`` otherwise, in which case dispatch continues to the next phase.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    MANAGED_JOIN_PARENT_ID_ENV_VAR,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    BackendPinResolution,
    ModelPinResolution,
    ProviderBinding,
    SkillContractError,
    closure_authority_spec_from_args,
    get_logger,
    new_managed_launch_id,
    parse_plan_paths,
    render_target_skill_command,
)
from autoskillit.server._explorer_projection import (
    _resolve_exploration_applicabilities,
    _resolve_exploration_profile,
)
from autoskillit.server.lifecycle._guards import (
    _check_dry_walkthrough,
    _check_input_contracts,
    _profile_to_env,
)
from autoskillit.server.managed_join_prelaunch import (
    ManagedJoinIssuanceRefusal,
    acquire_managed_join_evidence,
)
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._backend_compat import _candidate_backend_rejection_reason
from autoskillit.server.tools._execution_helpers import (
    aggregate_sandbox_overrides as _aggregate_sandbox_overrides,
)
from autoskillit.server.tools._execution_helpers import (
    bind_projection_backend,
    build_fresh_projection_context,
    resolve_skill_dispatch_metadata,
)
from autoskillit.server.tools.tools_execution._candidate_policy import (
    candidate_authority,
    resolve_candidate_policy,
)

if TYPE_CHECKING:
    from autoskillit.config import ExecutionCandidateSpec
    from autoskillit.core import CodingAgentBackend
from autoskillit.server.tools.tools_execution._state import _RunSkillDispatchState

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _ExplorerLaunchLease:
    """Cleanup authority for one bound explorer launch."""

    session_id: str
    session_home: Path
    backend: CodingAgentBackend | None


def _server_get_config():
    from autoskillit.server import _get_config  # circular-break: server composition root

    return _get_config()


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


def _resolve_provider_binding(
    state: _RunSkillDispatchState,
    candidate: ExecutionCandidateSpec | None,
    ordinal: int,
    step_model: str,
) -> bool:
    """Bind the stored provider contract or resolve one for a fresh candidate."""
    assert state._cfg is not None
    assert state._backend_authority is not None
    if state._stored_contract is not None:
        contract = state._resume_launch_contract
        if contract is None or contract.backend_authority != state._backend_authority:
            raise SkillContractError("Resume launch authority changed")
        state._env_dict = {}
        if contract.profile and contract.profile != "default":
            definition = state._cfg.providers.resolved_profiles.get(contract.profile)
            if definition is None:
                raise SkillContractError("Resume provider profile is unavailable")
            if definition.api_key_env and not os.environ.get(definition.api_key_env):
                raise SkillContractError("Resume provider credential is unavailable")
            state._env_dict = _profile_to_env(definition)
            if (definition.base_url or "") != contract.normalized_endpoint:
                raise SkillContractError("Resume provider endpoint changed")
        secret_keys = tuple(
            key
            for key in state._env_dict
            if any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        )
        state.provider_binding = (
            ProviderBinding(
                provider=contract.provider,
                profile=contract.profile,
                required_backend=contract.backend_authority.backend,
                normalized_endpoint=contract.normalized_endpoint,
                key_path=contract.provider_source.key_path,
                provider_source=contract.provider_source,
                profile_source=contract.profile_source,
                endpoint_source=contract.endpoint_source,
                environment={
                    key: value for key, value in state._env_dict.items() if key not in secret_keys
                },
                secret_environment_keys=secret_keys,
            )
            if contract.provider and contract.profile
            else None
        )
        state.model_pin = ModelPinResolution(
            contract.configured_model or "", contract.configured_model_source
        )
    else:
        try:
            state.provider_binding, state.model_pin, state._env_dict = resolve_candidate_policy(
                state._cfg,
                authority=state._backend_authority,
                candidate=candidate,
                ordinal=ordinal,
                step_name=state.step_name or "",
                recipe_name=state.tool_ctx.recipe_name or "",
                step_provider=state.step_provider or "",
                requested_model=step_model,
                providers_enabled=_te_pkg.is_feature_enabled(
                    "providers",
                    state._cfg.features,
                    experimental_enabled=state._cfg.experimental_enabled,
                ),
            )
        except SkillContractError as exc:
            state._candidate_rejection_reason = str(exc)
            return False
    state.effective_model = state.model_pin.model
    state._profile = state.provider_binding.provider if state.provider_binding is not None else ""
    state.profile_name_out = (
        state.provider_binding.profile
        if state.provider_binding is not None and state.provider_binding.profile != "default"
        else ""
    )
    state.provider_extras = state._env_dict or None
    return True


async def _prepare_dispatch_backend(
    state: _RunSkillDispatchState,
    candidate: ExecutionCandidateSpec | None = None,
    ordinal: int = 0,
) -> str | None:
    await _te_pkg._notify(
        state.ctx,
        "info",
        f"run_skill: {state.skill_command[:80]}",
        "autoskillit.run_skill",
        extra={"cwd": state.cwd, "model": state.model or "default"},
    )

    if (terminal := _check_dispatch_preconditions(state)) is not None:
        return terminal

    step_model = _prepare_config_and_step_fallback(state, ordinal)

    _resolve_dispatch_backend_authority(state, candidate, ordinal)
    assert state._effective_backend_obj is not None
    assert state.resolved_command is not None

    if not _resolve_provider_binding(state, candidate, ordinal, step_model):
        return None

    state.expected_output_patterns, state.write_spec, state._skill_contract = (
        resolve_skill_dispatch_metadata(
            state.tool_ctx,
            state.skill_command,
            state._stored_contract,
            audit_output_mode=state._audit_output_mode,
        )
    )
    state._fresh_parent_sandbox_mode = (
        "read-only"
        if state.tool_ctx.read_only_resolver
        and state.tool_ctx.read_only_resolver(state.skill_command)
        else "workspace-write"
    )
    if state._stored_contract is None:
        binary = state._effective_backend_obj.capabilities.process_name
        state._candidate_rejection_reason = _candidate_backend_rejection_reason(
            skill_info=state._effective_skill_contract,
            effective_backend_obj=state._effective_backend_obj,
            parent_sandbox_mode=state._fresh_parent_sandbox_mode,
            write_spec=state.write_spec,
            binary_available=not binary or shutil.which(binary) is not None,
        )
        if state._candidate_rejection_reason is not None:
            return None

    _prepare_managed_parent_projection(state)

    _bind_dispatch_projection(state)

    # Build validated add_dirs via DefaultSessionSkillManager
    from uuid import uuid4

    # Backend compatibility gate — fail-closed, fires before replay and live session paths.
    if state._stored_contract is not None and (
        compat_error := _te_pkg._check_backend_compat(
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
        )
    ):
        return compat_error

    _apply_step_tuning_fallback(state)

    if (terminal := _te_pkg._resolve_dispatch_paths(state, base_cwd=Path(state.cwd))) is not None:
        return terminal

    _resolve_dispatch_scope(state)
    state.invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"
    return None


def _apply_step_tuning_fallback(state: _RunSkillDispatchState) -> None:
    # Server-side recipe step parameter resolution.
    # When a step_name is provided and the recipe's step definition is cached,
    # auto-fill parameters the LLM may have omitted.
    if state.step_name and state.tool_ctx.active_recipe_steps is not None:
        _recipe_step = state.tool_ctx.active_recipe_steps.get(state.step_name)
        if _recipe_step is not None:
            if not state.output_dir and "output_dir" in _recipe_step.with_args:
                _recipe_output_dir = _recipe_step.with_args["output_dir"]
                # Skip values containing unresolved template references —
                # a finalized projection may retain ${{ context.* }} placeholders.
                if isinstance(_recipe_output_dir, str) and "${{" not in _recipe_output_dir:
                    state.output_dir = _recipe_output_dir
                    logger.warning(
                        "output_dir_resolved_from_recipe",
                        step=state.step_name,
                        output_dir=state.output_dir,
                    )

            # Use each field's vacancy sentinel; zero is a valid explicit timeout.
            # Under attestation this fallback only ever sees a genuine vacancy —
            # an explicit caller value for these fields is denied upstream by the
            # runtime gate before reaching here. For unattested calls, an explicit
            # caller value survives untouched, as intended.
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


def _prepare_managed_parent_projection(state: _RunSkillDispatchState) -> None:
    backend = state._effective_backend_obj
    if backend is not None and backend.capabilities.managed_fixed_batch_route_capable:
        managed_join_parent_id = state._managed_join_parent_id
        if not managed_join_parent_id:
            stored_entry = state._stored_contract_entry
            stored_lineage = stored_entry.managed_lineage_ref if stored_entry is not None else None
            managed_join_parent_id = (
                stored_lineage.launch_id if stored_lineage is not None else new_managed_launch_id()
            )
        state._managed_join_parent_id = managed_join_parent_id

        def _log_refusal(refusal: ManagedJoinIssuanceRefusal) -> None:
            logger.warning("managed_join_issuance_refused", reason=refusal.reason)

        evidence = acquire_managed_join_evidence(
            backend=backend,
            configured_model=state.effective_model,
            state_root=state.tool_ctx.project_dir,
            parent_id=managed_join_parent_id,
            launch_context="direct",
            on_refusal=_log_refusal,
        )
        if evidence is None:
            state._managed_join_parent_id = ""
        else:
            if state.projection_context is None:
                raise SkillContractError("Managed execution lacks projection authority")
            state.projection_context = replace(
                state.projection_context,
                adaptation_context=evidence.context,
                managed_codex_route="parent",
            )
            state.provider_extras = {
                **(state.provider_extras or {}),
                MANAGED_JOIN_PARENT_ID_ENV_VAR: managed_join_parent_id,
            }


def _resolve_dispatch_backend_authority(
    state: _RunSkillDispatchState, candidate: ExecutionCandidateSpec | None, ordinal: int
) -> None:
    assert state._cfg is not None
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
    from autoskillit.server.lifecycle._guards import _resolve_backend_override  # circular-break

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

    if candidate is not None:
        state._backend_authority = candidate_authority(
            state._backend_authority, candidate, ordinal
        )
        state._explicit_resolution = BackendPinResolution(
            candidate.backend,
            "execution_candidate",
            state._backend_authority.key_path,
            BackendAuthorityKind.GLOBAL,
        )
        state._effective_backend_obj = state.tool_ctx.launch_resolver.backend_for_authority(
            state._backend_authority
        )


def _prepare_config_and_step_fallback(state: _RunSkillDispatchState, ordinal: int) -> str:
    state._candidate_rejection_reason = None
    if ordinal and state._stored_contract_entry is None:
        if state.invocation is None:
            raise SkillContractError("Candidate selection lacks an invocation")
        state.projection_context = build_fresh_projection_context(state.cwd, state.invocation)

    if ordinal == 0:
        state.requested_step_provider = state.step_provider
    state.provider_extras = None
    state.provider_binding = None
    state.model_pin = None
    state.profile_name_out = ""
    state.effective_model = state.model

    state._cfg = _server_get_config()
    state._in_fleet_dispatch = bool(os.environ.get(DISPATCH_ID_ENV_VAR))
    state._inspector_model = (
        os.environ.get(FLEET_INSPECTOR_MODEL_ENV_VAR) or state._cfg.fleet.inspector_model
        if state._in_fleet_dispatch
        else ""
    )

    # step_provider's execution-tuning fallback lives here (pre-gate,
    # profile-interplay semantics) rather than in the post-gate
    # fallback loop — see core.EXECUTION_TUNING_EXTERNALLY_RESOLVED.
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

    step_model = state.model
    if not step_model and state.step_name and state.tool_ctx.active_recipe_steps is not None:
        _recipe_step = state.tool_ctx.active_recipe_steps.get(state.step_name)
        if _recipe_step is not None and _recipe_step.model and "${{" not in _recipe_step.model:
            step_model = _recipe_step.model

    return step_model


def _resolve_dispatch_scope(state: _RunSkillDispatchState) -> None:
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


def _bind_dispatch_projection(state: _RunSkillDispatchState) -> None:
    assert state._fresh_parent_sandbox_mode is not None
    assert state._backend_authority is not None
    if state._stored_contract is None:
        if state.projection_context is None:
            raise SkillContractError("Fresh execution lacks projection authority")
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


def _check_dispatch_preconditions(state: _RunSkillDispatchState) -> str | None:
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

    if _server_get_config().safety.require_dry_walkthrough and state._installed_execution is None:
        if (gate_error := _check_dry_walkthrough(state.skill_command, state.cwd)) is not None:
            return gate_error

    if state.tool_ctx.executor is None:
        return json.dumps({"success": False, "error": "Executor not configured"})

    return None
