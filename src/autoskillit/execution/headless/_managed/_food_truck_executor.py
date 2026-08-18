"""Concrete food-truck launch composition for the headless executor."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

import anyio

from autoskillit.core import (
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CodingAgentBackend,
    LaunchResolutionRequest,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    PluginArtifactAuthority,
    ProviderBinding,
    ResolvedLaunchContract,
    SemanticLaunchPlan,
    SessionCheckpoint,
    SkillProjectionPreparation,
    SkillResult,
    temp_dir_display_str,
)
from autoskillit.execution.headless._headless_helpers import resolve_model_identity
from autoskillit.execution.headless._headless_outcome import validated_dispatch_cwd
from autoskillit.execution.headless._managed._attempt import (
    _headless_plugin_load_mode,
    _ManagedLineageObserver,
)
from autoskillit.execution.headless._managed._executor import _DefaultHeadlessExecutorBase
from autoskillit.execution.headless._managed._launch_adapter import (
    _food_truck_launch_spec_builder,
)


class DefaultHeadlessExecutor(_DefaultHeadlessExecutorBase):
    """Concrete HeadlessExecutor backed by the shared headless lifecycle."""

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        plugin_authority: PluginArtifactAuthority | None = None,
        prior_completion_markers: Sequence[str] | None = None,
        resume_session_id: str | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        caller_session_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int, int], None] | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_name: str = "",
        provider_fallback_env: dict[str, str] | None = None,
        provider_fallback_name: str = "",
        profile_name: str = "",
        sentinel_contract: str = "",
        marker_dir: Path | None = None,
        session_id: str | None = None,
        resume_message: str | None = None,
        backend_authority: BackendAuthority | None = None,
        on_session_id_resolved: Callable[[str], None] | None = None,
        capability_preparation: SkillProjectionPreparation | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    ) -> SkillResult:
        import autoskillit.execution.headless as headless_facade

        cwd = validated_dispatch_cwd(
            None,
            resolved_command=orchestrator_prompt,
            cwd=cwd,
        )
        if backend_authority is None and self._ctx.backend is not None:
            backend_authority = BackendAuthority(
                backend=self._ctx.backend.name,
                kind=BackendAuthorityKind.GLOBAL,
                tier=BackendAuthorityTier.GLOBAL,
                key_path="agent_backend.backend",
            )
        dispatch_backend: CodingAgentBackend | None = None
        if backend_authority is not None:
            dispatch_backend = (
                self._ctx.backend
                if self._ctx.backend is not None
                and self._ctx.backend.name == backend_authority.backend
                else self._ctx.launch_resolver.backend_for_authority(backend_authority)
            )
        if dispatch_backend is not None and not dispatch_backend.capabilities.food_truck_capable:
            raise RuntimeError(
                f"backend does not support food truck dispatch "
                f"(food_truck_capable=False); got {dispatch_backend.name!r}"
            )
        if (
            backend_authority is not None
            and dispatch_backend is not None
            and dispatch_backend.capabilities.mcp_config_capable
            and backend_authority.kind is not BackendAuthorityKind.GLOBAL
        ):
            readiness = dispatch_backend.ensure_pre_launch()
            if readiness.errors:
                raise RuntimeError(
                    f"Pre-launch check failed for dispatch backend "
                    f"{dispatch_backend.name!r}: {'; '.join(readiness.errors)}"
                )
        cfg = self._ctx.config
        model_identity = resolve_model_identity(
            model, cfg, step_name=step_name, profile_name=profile_name
        )
        fleet_cfg = cfg.fleet
        merged_extras: dict[str, str] = dict(env_extras) if env_extras else {}
        if requires_packs:
            if FOOD_TRUCK_TOOL_TAGS_ENV_VAR in merged_extras:
                raise ValueError(
                    f"dispatch_food_truck: requires_packs and env_extras both specify "
                    f"{FOOD_TRUCK_TOOL_TAGS_ENV_VAR} — use requires_packs exclusively"
                )
            merged_extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ",".join(sorted(requires_packs))
        fleet_idle = fleet_cfg.idle_output_timeout
        if idle_output_timeout is not None:
            merged_extras["AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"] = str(idle_output_timeout)
        elif fleet_idle > 0:
            merged_extras.setdefault("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(fleet_idle))
        else:
            idle_cfg_val = cfg.run_skill.idle_output_timeout
            if idle_cfg_val > 0:
                merged_extras.setdefault("AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(idle_cfg_val))
        if dispatch_backend is None or backend_authority is None:
            raise RuntimeError("dispatch_backend must be resolved before execution")
        authority_source = LaunchValueSource(
            LaunchValueSourceKind(backend_authority.kind.value),
            backend_authority.key_path,
        )
        default_source = LaunchValueSource(LaunchValueSourceKind.DEFAULT, "fleet.defaults")
        secret_provider_keys = tuple(
            sorted(
                key
                for key in merged_extras
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
        semantic_digest = hashlib.sha256(orchestrator_prompt.encode()).hexdigest()
        launch_preparation = self._ctx.launch_resolver.prepare(
            LaunchResolutionRequest(
                surface=LaunchSurface.FLEET_OUTER,
                authority_candidates=(backend_authority,),
                semantic_plan=SemanticLaunchPlan(
                    surface=LaunchSurface.FLEET_OUTER,
                    semantic_digest=semantic_digest,
                    projection_digest=semantic_digest,
                ),
                command=orchestrator_prompt,
                arguments=(),
                cwd=cwd,
                requested_model=model or None,
                requested_model_source=authority_source if model else default_source,
                configured_model=model_identity.configured_model or None,
                configured_model_source=(
                    authority_source if model_identity.configured_model else default_source
                ),
                effort=None,
                effort_source=default_source,
                sandbox_mode=dispatch_backend.capabilities.default_skill_sandbox_mode,
                network_access=False,
                pty_required=False,
                inherited_fd_policy="attempt-scoped-plugin-binding",
                branch_identity={},
                worktree_identity={"cwd": cwd},
                executable_identity={
                    "backend": dispatch_backend.name,
                    "process_name": dispatch_backend.capabilities.process_name,
                },
                plugin_identity={},
                projection_identity={"digest": semantic_digest, "version": "0"},
                artifact_paths=(),
                quota_identity={"provider": provider_name or profile_name or "default"},
                provider_binding=(
                    ProviderBinding(
                        provider=provider_name or profile_name or dispatch_backend.name,
                        profile=profile_name or "default",
                        required_backend=dispatch_backend.name,
                        normalized_endpoint=(
                            merged_extras.get("ANTHROPIC_BASE_URL")
                            or merged_extras.get("OPENAI_BASE_URL")
                            or ""
                        ),
                        key_path="fleet.provider",
                        provider_source=authority_source,
                        profile_source=authority_source,
                        endpoint_source=authority_source,
                        environment={},
                        secret_environment_keys=secret_provider_keys,
                    )
                    if provider_name or profile_name or merged_extras
                    else None
                ),
                non_authority_metadata={"entrypoint": "fleet"},
            )
        )
        backend = dispatch_backend
        managed_lineage_observer = _ManagedLineageObserver.create(
            store=self._ctx.managed_headless_session_lineage_store,
            decision=native_shell_capture_decision,
            reference=managed_lineage_ref,
            backend=backend,
            session_kind=ManagedHeadlessSessionKind.FOOD_TRUCK,
        )
        plugin_load_mode = _headless_plugin_load_mode(backend)
        build_spec = _food_truck_launch_spec_builder(
            backend=backend,
            orchestrator_prompt=orchestrator_prompt,
            cwd=cwd,
            capability_preparation=capability_preparation,
            completion_marker=completion_marker,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            configured_model=model_identity.configured_model or None,
            output_format=cfg.run_skill.output_format,
            exit_after_stop_delay_ms=cfg.run_skill.exit_after_stop_delay_ms,
            stream_idle_timeout_ms=cfg.run_skill.stream_idle_timeout_ms,
            step_name=step_name,
            temp_dir_relpath=temp_dir_display_str(cfg.workspace.temp_dir),
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            sentinel_contract=sentinel_contract,
            resume_message=resume_message,
            native_shell_capture_decision=native_shell_capture_decision,
            managed_lineage_ref=managed_lineage_ref,
            force_inactive_agent_teams=cfg.agent_backend.force_inactive_agent_teams,
        )

        effective_timeout = timeout if timeout is not None else fleet_cfg.default_timeout_sec
        effective_stale = (
            stale_threshold if stale_threshold is not None else cfg.run_skill.stale_threshold
        )
        effective_deadline_ext = fleet_cfg.enable_deadline_extension
        effective_max_ext = float(fleet_cfg.max_extension_seconds)
        effective_idle_out: float | None = (
            idle_output_timeout
            if idle_output_timeout is not None
            else float(fleet_idle)
            if fleet_idle > 0
            else None
        )
        effective_marker_dir: Path | None = marker_dir or (
            headless_facade._resolve_session_log_dir(
                cwd, cast(CodingAgentBackend, dispatch_backend)
            )
            if cwd
            else None
        )
        try:
            skill_result = await headless_facade._execute_claude_headless(
                build_spec,
                cwd,
                self._ctx,
                skill_command="",
                step_name=step_name,
                kitchen_id=kitchen_id,
                caller_session_id=caller_session_id,
                order_id=order_id,
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
                project_dir=project_dir,
                timeout=float(effective_timeout),
                stale_threshold=float(effective_stale),
                idle_output_timeout=effective_idle_out,
                completion_marker=completion_marker,
                prior_completion_markers=prior_completion_markers,
                on_spawn=on_spawn,
                skip_clone_guard=True,
                pty_override=False,
                provider_name=provider_name,
                provider_extras=merged_extras or None,
                provider_fallback_env=provider_fallback_env,
                provider_fallback_name=provider_fallback_name,
                enable_deadline_extension=effective_deadline_ext,
                max_extension_seconds=effective_max_ext,
                marker_dir=effective_marker_dir,
                session_id=session_id,
                model_identity=model_identity,
                on_session_id_resolved=on_session_id_resolved,
                launch_resolver=self._ctx.launch_resolver,
                launch_preparation=launch_preparation,
                on_launch_resolved=on_launch_resolved,
                plugin_authority=plugin_authority or self._ctx.plugin_authority,
                plugin_load_mode=plugin_load_mode,
                managed_lineage_observer=managed_lineage_observer,
            )
        except anyio.get_cancelled_exc_class():
            if managed_lineage_observer is not None:
                managed_lineage_observer.close(ManagedHeadlessSessionTerminalState.CANCELLED)
            raise
        except Exception:
            if managed_lineage_observer is not None:
                managed_lineage_observer.close(ManagedHeadlessSessionTerminalState.FAILED)
            raise
        if managed_lineage_observer is not None and not skill_result.needs_retry:
            managed_lineage_observer.close(
                ManagedHeadlessSessionTerminalState.SUCCEEDED
                if skill_result.success
                else ManagedHeadlessSessionTerminalState.FAILED
            )
        return skill_result


__all__ = ["DefaultHeadlessExecutor"]
