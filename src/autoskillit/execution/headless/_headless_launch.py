"""Plugin-bound command construction and launch-attempt lifecycle."""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CmdSpec,
    CodingAgentBackend,
    HeadlessSkillDispatchPreparation,
    OutputFormat,
    PluginArtifactAuthority,
    PluginLaunchBinding,
    PluginLoadMode,
    RetryReason,
    SessionCheckpoint,
    SkillContractView,
    SkillResult,
    SkillSessionConfig,
    StreamParser,
    ValidatedAddDir,
    get_logger,
)
from autoskillit.execution.headless._headless_helpers import (
    _resolve_pty_mode,
    _resolve_session_log_dir,
    assert_headless_cmd,
)
from autoskillit.execution.headless._headless_outcome import validated_dispatch_cwd
from autoskillit.execution.headless._headless_recovery import (
    _EnumHint,
    _extract_missing_token_hints,
    _merge_token_usage,
)
from autoskillit.execution.session import _check_expected_patterns

if TYPE_CHECKING:
    from autoskillit.core import ResultParser, SubprocessResult, SubprocessRunner

logger = get_logger(__name__)

_NUDGE_TIMEOUT: float = 60.0

_BuildSpec = Callable[
    [PluginLaunchBinding | None, Mapping[str, str] | None],
    CmdSpec,
]


def _headless_plugin_load_mode(
    backend: CodingAgentBackend,
    *,
    add_dirs: Sequence[ValidatedAddDir] = (),
) -> PluginLoadMode:
    """Resolve how this concrete backend launch obtains its skill tree."""
    capabilities = backend.capabilities
    if not capabilities.skill_injection_capable:
        return PluginLoadMode.NONE
    if capabilities.plugin_install_capable:
        return PluginLoadMode.EXPLICIT_PLUGIN_DIR
    if add_dirs:
        return PluginLoadMode.GENERATED_HOME
    return PluginLoadMode.PROJECTED_HOME


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
    network_access: bool,
) -> _BuildSpec:
    """Bind stable skill-command inputs while leaving attempt identity late-bound."""

    def build(
        plugin_binding: PluginLaunchBinding | None,
        provider_extras: Mapping[str, str] | None,
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
            network_access=network_access,
        )
        return backend.build_skill_session_cmd(skill_command, cwd, config)

    return build


def _food_truck_launch_spec_builder(
    *,
    backend: CodingAgentBackend,
    orchestrator_prompt: str,
    cwd: str,
    capability_preparation: HeadlessSkillDispatchPreparation | None,
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
) -> _BuildSpec:
    """Bind food-truck inputs while finalizing semantic capability per binding."""

    def build(
        plugin_binding: PluginLaunchBinding | None,
        provider_extras: Mapping[str, str] | None,
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
        )

    return build


@contextmanager
def _plugin_launch_binding(
    *,
    authority: PluginArtifactAuthority | None,
    backend: CodingAgentBackend,
    load_mode: PluginLoadMode,
) -> Iterator[PluginLaunchBinding | None]:
    """Own one exact artifact binding for the complete child attempt."""
    binding: PluginLaunchBinding | None = None
    if load_mode.consumes_artifact:
        if authority is None:
            raise RuntimeError(f"{load_mode.value} launch requires plugin artifact authority")
        binding = authority.acquire_launch_binding(
            backend=backend,
            load_mode=load_mode,
        )
    try:
        yield binding
    finally:
        if binding is not None:
            primary_error = sys.exc_info()[1]
            try:
                binding.close()
            except BaseException:
                if primary_error is None:
                    raise
                logger.warning(
                    "plugin_launch_binding_close_failed",
                    primary_error=repr(primary_error),
                    exc_info=True,
                )


async def _run_headless_attempt(
    build_spec: _BuildSpec,
    *,
    cwd: str,
    runner: SubprocessRunner,
    backend: CodingAgentBackend,
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
) -> tuple[SubprocessResult, CmdSpec]:
    """Build and execute one provider attempt under one owned plugin binding."""
    with _plugin_launch_binding(
        authority=plugin_authority,
        backend=backend,
        load_mode=plugin_load_mode,
    ) as binding:
        spec = build_spec(binding, provider_extras)
        if spec.cmd:
            binary = Path(spec.cmd[0]).stem
            expected = backend.capabilities.process_name
            if isinstance(expected, str) and expected and binary != expected:
                from autoskillit.execution.backends import BACKEND_REGISTRY

                known = {item().capabilities.process_name for item in BACKEND_REGISTRY.values()}
                if binary in known:
                    raise RuntimeError(
                        f"Backend coherence violation: expected process_name="
                        f"{expected!r} but binary is {binary!r}"
                    )
        assert_headless_cmd(spec)
        effective_idle = idle_output_timeout
        if spec.process_idle_timeout_ms > 0:
            spec_idle = spec.process_idle_timeout_ms / 1000.0
            if effective_idle is None or spec_idle < effective_idle:
                effective_idle = spec_idle
        result = await runner(
            list(spec.cmd),
            cwd=Path(cwd),
            timeout=timeout,
            env=spec.env,
            pty_mode=(pty_override if pty_override is not None else _resolve_pty_mode(backend)),
            session_log_dir=_resolve_session_log_dir(cwd, backend),
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
) -> SkillResult | None:
    """Resume once to recover omitted structured tokens or the completion marker."""
    if backend is None or not backend.capabilities.session_resume_capable:
        return None
    if result_parser is None:
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
            spec = backend.build_resume_cmd(
                resume_session_id=skill_result.session_id,
                prompt=prompt,
                output_format=OutputFormat.JSON,
                plugin_binding=binding,
                env_extras=effective_extras or None,
            )
            assert_headless_cmd(spec)
            nudge_result = await runner(
                list(spec.cmd),
                cwd=Path(cwd),
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
