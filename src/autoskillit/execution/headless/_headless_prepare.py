"""Resolve the complete headless launch before execution begins."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CodingAgentBackend,
    LaunchPreparation,
    LaunchResolutionRequest,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    ModelIdentity,
    ModelPinResolution,
    ProviderBinding,
    ResolvedLaunchContract,
    SemanticLaunchPlan,
    SkillProjectionBinding,
    ValidatedAddDir,
    resolve_provider_used,
)
from autoskillit.execution.headless._headless_helpers import (
    _resolve_pty_mode,
    resolve_launch_quota_identity,
)
from autoskillit.execution.headless._headless_model import (
    resolve_model_identity,
    resolve_model_pin,
)

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext


@dataclass(frozen=True, slots=True)
class HeadlessLaunchPreparation:
    """Fully prepared launch context for the headless executor."""

    backend: CodingAgentBackend
    model_identity: ModelIdentity
    launch_preparation: LaunchPreparation
    add_dirs: tuple[ValidatedAddDir, ...]


def _prepare_headless_launch(
    skill_command: str,
    cwd: str,
    ctx: ToolContext,
    *,
    model: str,
    step_name: str,
    recipe_name: str,
    profile_name: str,
    add_dirs: Sequence[ValidatedAddDir],
    backend_authority: BackendAuthority | None,
    provider_extras: Mapping[str, str] | None,
    provider_name: str,
    provider_binding: ProviderBinding | None,
    model_pin: ModelPinResolution | None,
    capability_contract: SkillProjectionBinding | None,
    network_access: bool,
    resume_session_id: str,
    resume_launch_contract: ResolvedLaunchContract | None,
    readonly_skill: bool,
) -> HeadlessLaunchPreparation:
    caller_key_path = "run_skill.model"
    model_pin = model_pin or resolve_model_pin(
        model,
        ctx.config,
        step_name=step_name,
        recipe_name=recipe_name,
        caller_key_path=caller_key_path,
    )
    resolved_profile_name = (
        provider_binding.provider
        if provider_binding is not None
        else model_pin.profile_name or profile_name
    )
    model_identity = resolve_model_identity(model_pin, profile_name=resolved_profile_name)
    add_dirs_tuple = tuple(add_dirs)
    if backend_authority is None:
        if ctx.backend is None:
            raise RuntimeError("global backend authority is not configured")
        backend_authority = BackendAuthority(
            backend=ctx.backend.name,
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path="agent_backend.backend",
        )
    launch_backend = (
        ctx.backend
        if ctx.backend is not None and ctx.backend.name == backend_authority.backend
        else ctx.launch_resolver.backend_for_authority(backend_authority)
    )
    value_source_kind = LaunchValueSourceKind(backend_authority.kind.value)
    authority_source = LaunchValueSource(value_source_kind, backend_authority.key_path)
    default_source = LaunchValueSource(LaunchValueSourceKind.DEFAULT, "run_skill.defaults")
    provider_values = dict(provider_extras or {})
    secret_provider_keys = tuple(
        sorted(
            key
            for key in provider_values
            if any(
                token in key.upper()
                for token in (
                    "API_KEY",
                    "ACCESS_KEY",
                    "TOKEN",
                    "SECRET",
                    "PASSWORD",
                    "CREDENTIAL",
                )
            )
        )
    )
    provider_binding = provider_binding or (
        ProviderBinding(
            provider=provider_name
            or profile_name
            or resolve_provider_used(
                launch_backend.name, launch_backend.capabilities.anthropic_provider_capable
            ),
            profile=profile_name or "default",
            required_backend=backend_authority.backend,
            normalized_endpoint=(
                provider_values.get("ANTHROPIC_BASE_URL")
                or provider_values.get("OPENAI_BASE_URL")
                or ""
            ),
            key_path="run_skill.provider",
            provider_source=authority_source,
            profile_source=authority_source,
            endpoint_source=authority_source,
            environment={},
            secret_environment_keys=secret_provider_keys,
        )
    )
    projection_payload = (
        dict(sorted(capability_contract.projected_digests.items()))
        if capability_contract is not None
        else {"command": skill_command}
    )
    projection_digest = (
        capability_contract.projection_digest
        if capability_contract is not None
        else hashlib.sha256(
            json.dumps(projection_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    semantic_digest = (
        hashlib.sha256(
            json.dumps(
                dict(sorted(capability_contract.semantic_digests.items())),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if capability_contract is not None
        else hashlib.sha256(skill_command.encode()).hexdigest()
    )
    launch_request = LaunchResolutionRequest(
        surface=LaunchSurface.HEADLESS_SKILL,
        authority_candidates=(backend_authority,),
        semantic_plan=SemanticLaunchPlan(
            surface=LaunchSurface.HEADLESS_SKILL,
            semantic_digest=semantic_digest,
            projection_digest=projection_digest,
        ),
        command=skill_command,
        arguments=(),
        cwd=cwd,
        requested_model=model or None,
        requested_model_source=(
            LaunchValueSource(LaunchValueSourceKind.CALLER, caller_key_path)
            if model
            else default_source
        ),
        configured_model=model_identity.configured_model or None,
        configured_model_source=(
            model_pin.source if model_identity.configured_model else default_source
        ),
        effort=None,
        effort_source=default_source,
        sandbox_mode="pending-adapter",
        network_access=network_access,
        pty_required=False,
        inherited_fd_policy="attempt-scoped-plugin-binding",
        branch_identity={},
        worktree_identity={"cwd": cwd},
        executable_identity={"backend": backend_authority.backend},
        plugin_identity={},
        projection_identity={
            "digest": projection_digest,
            "version": str(
                capability_contract.projection_version if capability_contract is not None else 0
            ),
        },
        artifact_paths=(
            capability_contract.artifact_paths if capability_contract is not None else ()
        ),
        quota_identity=resolve_launch_quota_identity(
            backend=launch_backend,
            binding=provider_binding,
            provider_extras=provider_extras,
            config=ctx.config,
        ),
        provider_binding=provider_binding,
        skill_projection_binding=capability_contract,
        non_authority_metadata={"entrypoint": "headless"},
    )
    if resume_launch_contract is not None:
        if not resume_session_id:
            raise RuntimeError("persisted launch contract requires a resume session ID")
        if backend_authority != resume_launch_contract.backend_authority:
            raise RuntimeError("resume backend authority drifted from persisted launch contract")
        launch_preparation = ctx.launch_resolver.prepare_resume(
            resume_launch_contract,
            command=skill_command,
            cwd=cwd,
        )
    else:
        launch_preparation = ctx.launch_resolver.prepare(launch_request)
    command_backend = (
        ctx.backend
        if ctx.backend is not None and ctx.backend.name == launch_preparation.selected_backend
        else ctx.launch_resolver.backend_for(launch_preparation)
    )
    if capability_contract is not None and capability_contract.backend not in {
        None,
        command_backend.name,
    }:
        raise RuntimeError("skill projection backend drifted from launch authority")
    launch_preparation = replace(
        launch_preparation,
        sandbox_mode=(
            "read-only"
            if readonly_skill
            else command_backend.capabilities.default_skill_sandbox_mode
        ),
        pty_required=_resolve_pty_mode(command_backend),
        executable_identity={
            "backend": command_backend.name,
            "process_name": command_backend.capabilities.process_name,
        },
    )
    return HeadlessLaunchPreparation(
        backend=command_backend,
        model_identity=model_identity,
        launch_preparation=launch_preparation,
        add_dirs=add_dirs_tuple,
    )
