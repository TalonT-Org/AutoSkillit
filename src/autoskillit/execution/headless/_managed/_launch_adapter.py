"""Typed adapter and command builders for managed headless launches."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping

from autoskillit.core import (
    CmdSpec,
    CodingAgentBackend,
    LaunchAdapterResult,
    LaunchPreparation,
    LaunchValueSource,
    LaunchValueSourceKind,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    OutputFormat,
    PluginLaunchBinding,
    PluginLoadMode,
    SessionCheckpoint,
    SkillProjectionPreparation,
    SkillSessionConfig,
    ValidatedAddDir,
)
from autoskillit.execution.headless._headless_outcome import validated_dispatch_cwd
from autoskillit.execution.headless._managed._attempt import (
    _build_attempt_spec,
    _BuildSpec,
    _ManagedLineageObserver,
)


def _is_secret_environment_key(key: str) -> bool:
    upper = key.upper()
    return any(
        token in upper
        for token in ("API_KEY", "ACCESS_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    )


def _binding_identity(binding: PluginLaunchBinding | None) -> Mapping[str, str]:
    if binding is None:
        return {"load_mode": PluginLoadMode.NONE.value}
    identity = binding.identity
    return {
        "load_mode": binding.load_mode.value,
        "semantic_key": identity.semantic_key,
        "incarnation_id": identity.incarnation_id,
        "manifest_schema_version": str(identity.manifest_schema_version),
        "artifact_digest": identity.artifact_digest,
        "managed_path": str(identity.managed_path),
        "manifest_path": str(identity.manifest_path),
    }


class _HeadlessLaunchAdapter:
    """One-shot adapter around the selected backend's physical command builder."""

    def __init__(
        self,
        *,
        build_spec: _BuildSpec,
        binding: PluginLaunchBinding | None,
        provider_extras: Mapping[str, str] | None,
        observer: _ManagedLineageObserver | None,
        managed_attempt_id: str | None,
    ) -> None:
        self._build_spec = build_spec
        self._binding = binding
        self._provider_extras = provider_extras
        self._observer = observer
        self._managed_attempt_id = managed_attempt_id
        self.secret_environment: Mapping[str, str] = {}
        self.inherited_fds: tuple[int, ...] = ()

    def build(self, preparation: LaunchPreparation) -> LaunchAdapterResult:
        spec = _build_attempt_spec(
            self._build_spec,
            binding=self._binding,
            provider_extras=self._provider_extras,
            observer=self._observer,
            managed_attempt_id=self._managed_attempt_id,
        )
        if spec.cwd != preparation.cwd:
            if spec.cwd:
                raise RuntimeError(
                    "backend command cwd drifted from the resolved launch preparation"
                )
            spec = dataclasses.replace(spec, cwd=preparation.cwd)
        secret_keys = tuple(sorted(key for key in spec.env if _is_secret_environment_key(key)))
        secret_environment = {key: spec.env[key] for key in secret_keys}
        nonsecret_environment = {
            key: value for key, value in spec.env.items() if key not in secret_environment
        }
        self.secret_environment = secret_environment
        self.inherited_fds = spec.inherited_fds
        adapter_payload = {
            "argv": spec.cmd,
            "cwd": spec.cwd,
            "origin": repr(spec.origin),
            "nonsecret_env": nonsecret_environment,
            "secret_keys": secret_keys,
            "process_idle_timeout_ms": spec.process_idle_timeout_ms,
            "inherited_fd_count": len(spec.inherited_fds),
        }
        adapter_digest = hashlib.sha256(
            json.dumps(adapter_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return LaunchAdapterResult(
            backend=preparation.selected_backend,
            provider=preparation.provider,
            profile=preparation.profile,
            normalized_endpoint=preparation.normalized_endpoint,
            physical_model=preparation.configured_model,
            physical_model_source=LaunchValueSource(
                LaunchValueSourceKind.ADAPTER,
                f"backend.{preparation.selected_backend}.model",
            ),
            effort=preparation.effort,
            effort_source=preparation.effort_source,
            semantic_digest=preparation.semantic_plan.semantic_digest,
            adapter_digest=adapter_digest,
            projection_digest=preparation.semantic_plan.projection_digest,
            cwd=preparation.cwd,
            command=preparation.command,
            arguments=preparation.arguments,
            branch_identity=preparation.branch_identity,
            worktree_identity=preparation.worktree_identity,
            executable_identity=preparation.executable_identity,
            plugin_identity=preparation.plugin_identity,
            projection_identity=preparation.projection_identity,
            artifact_paths=preparation.artifact_paths,
            nonsecret_env=nonsecret_environment,
            cmd_spec=spec,
            secret_environment_keys=secret_keys,
            secret_profile_identity=preparation.secret_profile_identity,
            skill_projection_binding=preparation.skill_projection_binding,
        )


def _skill_launch_spec_builder(
    *,
    backend: CodingAgentBackend,
    skill_command: str,
    cwd: str,
    completion_marker: str,
    configured_model: str | None,
    output_format: OutputFormat,
    add_dirs: tuple[ValidatedAddDir, ...],
    exit_after_stop_delay_ms: int,
    stream_idle_timeout_ms: int,
    step_name: str,
    temp_dir_relpath: str,
    allowed_write_prefix: str,
    allowed_write_prefixes: tuple[str, ...],
    profile_name: str,
    resume_session_id: str,
    resume_checkpoint: SessionCheckpoint | None,
    resume_message: str | None,
    readonly_skill: bool,
    scope_discipline_skill: bool,
    network_access: bool,
    native_shell_capture_decision: NativeShellCaptureDecision | None,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
) -> _BuildSpec:
    """Bind stable skill-command inputs while leaving attempt identity late-bound."""

    def build(
        plugin_binding: PluginLaunchBinding | None,
        provider_extras: Mapping[str, str] | None,
        managed_attempt_id: str | None = None,
    ) -> CmdSpec:
        config = SkillSessionConfig(
            completion_marker=completion_marker,
            model=configured_model,
            plugin_binding=plugin_binding,
            output_format=output_format,
            add_dirs=add_dirs,
            exit_after_stop_delay_ms=exit_after_stop_delay_ms,
            stream_idle_timeout_ms=stream_idle_timeout_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_relpath,
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            provider_extras=provider_extras,
            profile_name=profile_name,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            resume_message=resume_message,
            sandbox_mode=(
                "read-only" if readonly_skill else backend.capabilities.default_skill_sandbox_mode
            ),
            include_scope_discipline=scope_discipline_skill,
            network_access=network_access,
            native_shell_capture_decision=native_shell_capture_decision,
            managed_lineage_ref=managed_lineage_ref,
            managed_attempt_id=managed_attempt_id,
        )
        return backend.build_skill_session_cmd(skill_command, cwd, config)

    return build


def _food_truck_launch_spec_builder(
    *,
    backend: CodingAgentBackend,
    orchestrator_prompt: str,
    cwd: str,
    capability_preparation: SkillProjectionPreparation | None,
    completion_marker: str,
    resume_session_id: str | None,
    resume_checkpoint: SessionCheckpoint | None,
    configured_model: str | None,
    output_format: OutputFormat,
    exit_after_stop_delay_ms: int,
    stream_idle_timeout_ms: int,
    step_name: str,
    temp_dir_relpath: str,
    allowed_write_prefix: str,
    allowed_write_prefixes: tuple[str, ...],
    sentinel_contract: str,
    resume_message: str | None,
    native_shell_capture_decision: NativeShellCaptureDecision | None,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
) -> _BuildSpec:
    """Bind food-truck inputs while finalizing semantic capability per binding."""

    def build(
        plugin_binding: PluginLaunchBinding | None,
        provider_extras: Mapping[str, str] | None,
        managed_attempt_id: str | None = None,
    ) -> CmdSpec:
        attempt_cwd = cwd
        if capability_preparation is not None:
            if plugin_binding is None:
                raise RuntimeError("semantic food-truck dispatch requires a plugin launch binding")
            capability_contract = capability_preparation.finalize(
                backend=backend,
                binding=plugin_binding,
            )
            attempt_cwd = validated_dispatch_cwd(
                capability_contract,
                resolved_command=orchestrator_prompt,
                cwd=cwd,
            )
        return backend.build_food_truck_cmd(
            orchestrator_prompt=orchestrator_prompt,
            plugin_binding=plugin_binding,
            cwd=attempt_cwd,
            completion_marker=completion_marker,
            resume_session_id=resume_session_id,
            resume_checkpoint=resume_checkpoint,
            model=configured_model,
            env_extras=provider_extras,
            output_format=output_format,
            exit_after_stop_delay_ms=exit_after_stop_delay_ms,
            stream_idle_timeout_ms=stream_idle_timeout_ms,
            scenario_step_name=step_name,
            temp_dir_relpath=temp_dir_relpath,
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            sentinel_contract=sentinel_contract,
            resume_message=resume_message,
            native_shell_capture_decision=native_shell_capture_decision,
            managed_lineage_ref=managed_lineage_ref,
            managed_attempt_id=managed_attempt_id,
        )

    return build


__all__ = [
    "_HeadlessLaunchAdapter",
    "_binding_identity",
    "_food_truck_launch_spec_builder",
    "_skill_launch_spec_builder",
]
