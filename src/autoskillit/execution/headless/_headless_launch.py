"""Plugin-bound command construction and launch-attempt lifecycle."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CmdSpec,
    CodingAgentBackend,
    ExecutionIdentity,
    LaunchPreparation,
    LaunchResolver,
    OutputFormat,
    PluginArtifactAuthority,
    PluginLaunchBinding,
    PluginLoadMode,
    ResolvedLaunchContract,
    RetryReason,
    SkillContractView,
    SkillResult,
    StreamParser,
    get_logger,
    plugin_launch_binding_scope,
)
from autoskillit.execution.headless._headless_helpers import (
    _resolve_pty_mode,
    _resolve_session_log_dir,
)
from autoskillit.execution.headless._headless_recovery import (
    _EnumHint,
    _extract_missing_token_hints,
    _merge_token_usage,
)
from autoskillit.execution.headless._managed import (
    _BuildSpec,
    _headless_plugin_load_mode,
    _ManagedLineageObserver,
)
from autoskillit.execution.headless._managed._launch_adapter import (
    _binding_identity,
    _food_truck_launch_spec_builder,
    _HeadlessLaunchAdapter,
    _skill_launch_spec_builder,
)
from autoskillit.execution.session import _check_expected_patterns

if TYPE_CHECKING:
    from autoskillit.core import ResultParser, SubprocessResult, SubprocessRunner

logger = get_logger(__name__)

_NUDGE_TIMEOUT: float = 60.0


def _bind_effective_execution_identity(
    skill_result: SkillResult,
    backend: CodingAgentBackend,
    requested: ExecutionIdentity,
) -> SkillResult:
    effective = requested
    if requested.children and skill_result.session_id:
        try:
            effective = backend.resolve_effective_execution_identity(
                requested=requested,
                session_id=skill_result.session_id,
            )
        except (OSError, ValueError):
            logger.warning(
                "effective_execution_identity_resolution_failed",
                session_id=skill_result.session_id,
                exc_info=True,
            )
    return dataclasses.replace(skill_result, execution_identity=effective)


def _report_plugin_binding_close_failure(
    primary_error: BaseException,
    _cleanup_error: BaseException,
) -> None:
    logger.warning(
        "plugin_launch_binding_close_failed",
        primary_error=repr(primary_error),
        exc_info=True,
    )


def _plugin_launch_binding(
    *,
    authority: PluginArtifactAuthority | None,
    backend: CodingAgentBackend,
    load_mode: PluginLoadMode,
) -> AbstractContextManager[PluginLaunchBinding | None]:
    """Own one exact artifact binding for the complete child attempt."""
    return plugin_launch_binding_scope(
        authority=authority,
        backend=backend,
        load_mode=load_mode,
        on_suppressed_close_error=_report_plugin_binding_close_failure,
    )


async def _run_headless_attempt(
    build_spec: _BuildSpec,
    *,
    runner: SubprocessRunner,
    backend: CodingAgentBackend,
    launch_resolver: LaunchResolver,
    launch_preparation: LaunchPreparation,
    expected_launch_contract: ResolvedLaunchContract | None,
    plugin_authority: PluginArtifactAuthority | None,
    plugin_load_mode: PluginLoadMode,
    provider_extras: Mapping[str, str] | None,
    timeout: float,
    pty_override: bool | None,
    completion_marker: str,
    stale_threshold: float,
    completion_drain_timeout: float,
    linux_tracing_config: Any,
    idle_output_timeout: float | None,
    max_suppression_seconds: float,
    child_deferral_ceiling: float,
    on_spawn: Callable[[int, int], None] | None,
    enable_deadline_extension: bool,
    max_extension_seconds: float,
    marker_dir: Path | None,
    session_id: str | None,
    on_session_id_resolved: Callable[[str], None] | None,
    stream_parser: StreamParser,
    backend_resume_session_id: str,
    lifecycle_observation_enabled: bool,
    on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    managed_lineage_observer: _ManagedLineageObserver | None = None,
    managed_attempt_id: str | None = None,
) -> tuple[SubprocessResult, CmdSpec]:
    """Build and execute one provider attempt under one owned plugin binding."""
    with _plugin_launch_binding(
        authority=plugin_authority,
        backend=backend,
        load_mode=plugin_load_mode,
    ) as binding:
        plugin_identity = _binding_identity(binding)
        artifact_paths = tuple(
            [*launch_preparation.artifact_paths]
            + ([str(binding.plugin_dir)] if binding is not None else [])
        )
        attempt_preparation = dataclasses.replace(
            launch_preparation,
            plugin_identity=plugin_identity,
            artifact_paths=artifact_paths,
        )
        adapter = _HeadlessLaunchAdapter(
            build_spec=build_spec,
            binding=binding,
            provider_extras=provider_extras,
            observer=managed_lineage_observer,
            managed_attempt_id=managed_attempt_id,
        )
        launch_contract = launch_resolver.finalize(attempt_preparation, adapter)
        if expected_launch_contract is not None:
            launch_resolver.validate_resume(expected_launch_contract, launch_contract)
        if managed_lineage_observer is not None:
            managed_lineage_observer.bind_launch_contract_digest(launch_contract.digest)
        if on_launch_resolved is not None:
            on_launch_resolved(launch_contract)
        spec = launch_resolver.rehydrate_secret_environment(
            launch_contract,
            adapter.secret_environment,
            inherited_fds=adapter.inherited_fds,
        )
        effective_idle = idle_output_timeout
        if spec.process_idle_timeout_ms > 0:
            spec_idle = spec.process_idle_timeout_ms / 1000.0
            if effective_idle is None or spec_idle < effective_idle:
                effective_idle = spec_idle
        result = await runner(
            list(spec.cmd),
            cwd=Path(spec.cwd),
            timeout=timeout,
            env=spec.env,
            pty_mode=(pty_override if pty_override is not None else _resolve_pty_mode(backend)),
            session_log_dir=_resolve_session_log_dir(spec.cwd, backend),
            completion_marker=completion_marker,
            stale_threshold=stale_threshold,
            completion_drain_timeout=completion_drain_timeout,
            linux_tracing_config=linux_tracing_config,
            idle_output_timeout=effective_idle,
            max_suppression_seconds=max_suppression_seconds,
            child_deferral_ceiling=child_deferral_ceiling,
            on_pid_resolved=on_spawn,
            enable_deadline_extension=enable_deadline_extension,
            max_extension_seconds=max_extension_seconds,
            marker_dir=marker_dir,
            session_id=session_id,
            on_session_id_resolved=on_session_id_resolved,
            stream_parser=stream_parser,
            completion_record_types=backend.capabilities.completion_record_types,
            session_record_types=backend.capabilities.session_record_types,
            inspector_callback=None,
            workload_basenames=backend.capabilities.process_name_aliases or None,
            pass_fds=spec.inherited_fds,
            backend_resume_session_id=backend_resume_session_id,
            lifecycle_observation_enabled=lifecycle_observation_enabled,
        )
        return result, spec


async def _attempt_contract_nudge(
    skill_result: SkillResult,
    subprocess_result: SubprocessResult,
    expected_output_patterns: Sequence[str],
    completion_marker: str,
    cwd: str,
    runner: SubprocessRunner,
    *,
    backend: CodingAgentBackend | None = None,
    result_parser: ResultParser | None = None,
    provider_extras: Mapping[str, str] | None = None,
    retry_reason: RetryReason = RetryReason.CONTRACT_RECOVERY,
    pty_override: bool | None = None,
    skill_contract: SkillContractView | None = None,
    plugin_authority: PluginArtifactAuthority | None = None,
    plugin_load_mode: PluginLoadMode = PluginLoadMode.NONE,
    session_env: Mapping[str, str] | None = None,
    managed_lineage_observer: _ManagedLineageObserver | None = None,
    launch_resolver: LaunchResolver | None = None,
    launch_preparation: LaunchPreparation | None = None,
    expected_launch_contract: ResolvedLaunchContract | None = None,
    on_launch_resolved: Callable[[ResolvedLaunchContract], None] | None = None,
    force_inactive_agent_teams: bool = False,
) -> SkillResult | None:
    """Resume once to recover omitted structured tokens or the completion marker."""
    if backend is None or not backend.capabilities.session_resume_capable:
        return None
    if result_parser is None:
        return None
    if launch_resolver is None or launch_preparation is None:
        return None

    if retry_reason == RetryReason.EARLY_STOP:
        prompt = (
            "Your response was complete but you omitted the required completion marker. "
            f"Please emit ONLY the following text (nothing else):\n"
            f"{completion_marker}"
        )
        patterns_to_check: Sequence[str] = list(expected_output_patterns)
    else:
        hints = _extract_missing_token_hints(
            subprocess_result.stdout,
            expected_output_patterns,
            result_parser,
            backend.write_tool_names(),
            skill_contract=skill_contract,
        )
        if not hints:
            logger.debug("nudge_skip_no_hints")
            return None
        hint_lines: list[str] = []
        for hint in hints:
            if isinstance(hint, _EnumHint):
                logger.info(
                    "nudge_enum_hint",
                    field_name=hint.token,
                    allowed_values=list(hint.allowed_values),
                )
                hint_lines.append(
                    f"Emit `{hint.token} = <value>` where <value> is one of: "
                    f"{' | '.join(hint.allowed_values)} — choose the value matching "
                    "what you actually did."
                )
            else:
                hint_lines.append(f"{hint.token} = {hint.path}")
        token_lines = "\n".join(hint_lines)
        prompt = (
            "You completed your task and wrote the output file, but you omitted the "
            "required structured output token in your final text response.\n\n"
            f"Please emit ONLY the following (no other text):\n"
            f"{token_lines}\n"
            f"{completion_marker}"
        )
        patterns_to_check = list(expected_output_patterns)

    effective_extras = dict(provider_extras or {})
    if (
        plugin_load_mode is PluginLoadMode.GENERATED_HOME
        and session_env is not None
        and (generated_home := session_env.get("CODEX_HOME"))
    ):
        effective_extras["CODEX_HOME"] = generated_home
    if plugin_load_mode.consumes_artifact and plugin_authority is None:
        logger.warning("nudge_skip_missing_plugin_authority")
        return None
    try:
        with _plugin_launch_binding(
            authority=plugin_authority,
            backend=backend,
            load_mode=plugin_load_mode,
        ) as binding:
            managed_attempt_id = (
                managed_lineage_observer.allocate_attempt()
                if managed_lineage_observer is not None
                else None
            )

            def build_nudge_spec(
                plugin_binding: PluginLaunchBinding | None,
                extras: Mapping[str, str] | None,
                attempt_id: str | None = None,
            ) -> CmdSpec:
                return backend.build_resume_cmd(
                    resume_session_id=skill_result.session_id,
                    prompt=prompt,
                    output_format=OutputFormat.JSON,
                    plugin_binding=plugin_binding,
                    env_extras=extras,
                    native_shell_capture_decision=(
                        managed_lineage_observer.decision
                        if managed_lineage_observer is not None
                        else None
                    ),
                    managed_lineage_ref=(
                        managed_lineage_observer.reference
                        if managed_lineage_observer is not None
                        else None
                    ),
                    managed_attempt_id=attempt_id,
                    skill_session=True,
                    force_inactive_agent_teams=force_inactive_agent_teams,
                )

            plugin_identity = _binding_identity(binding)
            nudge_preparation = dataclasses.replace(
                launch_preparation,
                command=prompt,
                arguments=(),
                cwd=cwd,
                plugin_identity=plugin_identity,
                artifact_paths=tuple(
                    [*launch_preparation.artifact_paths]
                    + (
                        [str(binding.plugin_dir)]
                        if binding is not None and binding.plugin_dir
                        else []
                    )
                ),
            )
            adapter = _HeadlessLaunchAdapter(
                build_spec=build_nudge_spec,
                binding=binding,
                provider_extras=effective_extras or None,
                observer=managed_lineage_observer,
                managed_attempt_id=managed_attempt_id,
            )
            launch_contract = launch_resolver.finalize(nudge_preparation, adapter)
            if expected_launch_contract is not None:
                launch_resolver.validate_resume(
                    expected_launch_contract,
                    launch_contract,
                )
            if managed_lineage_observer is not None:
                managed_lineage_observer.bind_launch_contract_digest(launch_contract.digest)
            if on_launch_resolved is not None:
                on_launch_resolved(launch_contract)
            spec = launch_resolver.rehydrate_secret_environment(
                launch_contract,
                adapter.secret_environment,
                inherited_fds=adapter.inherited_fds,
            )
            nudge_result = await runner(
                list(spec.cmd),
                cwd=Path(spec.cwd),
                timeout=_NUDGE_TIMEOUT,
                env=spec.env,
                pty_mode=(
                    pty_override if pty_override is not None else _resolve_pty_mode(backend)
                ),
                pass_fds=spec.inherited_fds,
            )
    except OSError:
        logger.debug("nudge_runner_failed", exc_info=True)
        return None
    except Exception:
        logger.warning("nudge_runner_failed_unexpected", exc_info=True)
        return None

    try:
        nudge_session = result_parser.parse_stdout(nudge_result.stdout)
    except Exception:
        logger.warning("nudge_parse_stdout_failed", exc_info=True)
        return None
    if managed_lineage_observer is not None and nudge_session.session_id:
        managed_lineage_observer.bind_candidate(nudge_session.session_id)
    combined_result = skill_result.result + "\n" + nudge_session.output
    nudge_usage = nudge_session.raw.get("token_usage")

    if retry_reason == RetryReason.EARLY_STOP:
        if completion_marker in nudge_session.output:
            if patterns_to_check and not _check_expected_patterns(
                combined_result, patterns_to_check
            ):
                logger.debug("nudge_early_stop_patterns_not_in_combined")
                return None
            logger.info(
                "nudge_recovery_success",
                session_id=skill_result.session_id,
                nudge_output_count=nudge_usage.get("output_tokens", 0) if nudge_usage else 0,
            )
            return dataclasses.replace(
                skill_result,
                success=True,
                result=combined_result,
                subtype="success",
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                token_usage=_merge_token_usage(skill_result.token_usage, nudge_usage),
            )
        logger.debug(
            "nudge_early_stop_marker_not_found",
            nudge_result_len=len(nudge_session.output),
        )
        return None

    if not _check_expected_patterns(combined_result, patterns_to_check):
        logger.debug(
            "nudge_patterns_not_found",
            nudge_result_len=len(nudge_session.output),
        )
        return None

    logger.info(
        "nudge_recovery_success",
        session_id=skill_result.session_id,
        nudge_output_count=nudge_usage.get("output_tokens", 0) if nudge_usage else 0,
    )
    return dataclasses.replace(
        skill_result,
        success=True,
        result=combined_result,
        subtype="success",
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        token_usage=_merge_token_usage(skill_result.token_usage, nudge_usage),
    )


__all__ = [
    "_NUDGE_TIMEOUT",
    "_attempt_contract_nudge",
    "_food_truck_launch_spec_builder",
    "_headless_plugin_load_mode",
    "_run_headless_attempt",
    "_skill_launch_spec_builder",
]
