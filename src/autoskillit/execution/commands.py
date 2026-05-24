"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import (
    CmdSpec,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    ValidatedAddDir,
)
from autoskillit.execution.backends.claude import (
    _HEADLESS_EXCLUSIVE_VARS,  # noqa: F401 — re-export for downstream consumers
    _MAX_MCP_OUTPUT_TOKENS_VALUE,  # noqa: F401 — re-export for downstream consumers
    _SESSION_BASELINE_ENV,  # noqa: F401 — re-export for downstream consumers
    ClaudeCodeBackend,
    _apply_output_format,  # noqa: F401 — re-export for downstream consumers
    _build_resume_context,  # noqa: F401 — re-export for downstream consumers
    _compose_resume_prompt,  # noqa: F401 — re-export for downstream consumers
    _ensure_skill_prefix,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_directive,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_reminder,  # noqa: F401 — re-export for downstream consumers
    _inject_cwd_anchor,  # noqa: F401 — re-export for downstream consumers
    _inject_narration_suppression,  # noqa: F401 — re-export for downstream consumers
)


@dataclass(frozen=True, slots=True)
class ClaudeInteractiveCmd:
    """Resolved argv + env for a claude interactive subprocess.

    ``env`` is the fully resolved environment returned by
    :func:`build_agent_env` — pass directly to ``subprocess.run(env=...)``.
    Callers must NOT merge in ``os.environ`` again; the sanitization layer
    has already applied the denylist and the auto-connect suppressor.
    """

    cmd: list[str]
    env: Mapping[str, str] = field(default_factory=dict)


ClaudeHeadlessCmd = CmdSpec


def build_interactive_cmd(
    *,
    initial_prompt: str | None = None,
    model: str | None = None,
    plugin_source: PluginSource | None = None,
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
    resume_spec: ResumeSpec = NoResume(),
    system_prompt: str | None = None,
    env_extras: Mapping[str, str] | None = None,
    required_env: frozenset[str] | None = None,
) -> ClaudeInteractiveCmd:
    """Build a Claude interactive session command."""
    spec = ClaudeCodeBackend().build_interactive_cmd(
        initial_prompt=initial_prompt,
        model=model,
        plugin_source=plugin_source,
        add_dirs=add_dirs,
        resume_spec=resume_spec,
        system_prompt=system_prompt,
        env_extras=env_extras,
        required_env=required_env,
    )
    return ClaudeInteractiveCmd(cmd=list(spec.cmd), env=spec.env)


def build_headless_cmd(
    prompt: str,
    *,
    model: str | None = None,
    env_extras: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> CmdSpec:
    """Build a Claude headless session command for skill execution."""
    spec = ClaudeCodeBackend().build_headless_cmd(
        prompt,
        model=model,
        env_extras=env_extras,
        base=base,
    )
    return spec


def build_headless_resume_cmd(
    *,
    resume_session_id: str,
    prompt: str,
    output_format: OutputFormat = OutputFormat.JSON,
    plugin_source: PluginSource | None = None,
    env_extras: Mapping[str, str] | None = None,
) -> CmdSpec:
    """Build a headless resume command for contract recovery nudge."""
    spec = ClaudeCodeBackend().build_resume_cmd(
        resume_session_id=resume_session_id,
        prompt=prompt,
        output_format=output_format,
        plugin_source=plugin_source,
        env_extras=env_extras,
    )
    return spec


def build_skill_session_cmd(
    skill_command: str,
    *,
    cwd: str,
    completion_marker: str,
    model: str | None,
    plugin_source: PluginSource | None,
    output_format: OutputFormat,
    add_dirs: Sequence[ValidatedAddDir] = (),
    exit_after_stop_delay_ms: int = 0,
    stream_idle_timeout_ms: int = 0,
    scenario_step_name: str = "",
    temp_dir_relpath: str | None = None,
    allowed_write_prefix: str = "",
    provider_extras: Mapping[str, str] | None = None,
    profile_name: str = "",
    resume_session_id: str = "",
    resume_checkpoint: SessionCheckpoint | None = None,
    resume_message: str | None = None,
) -> CmdSpec:
    """Build the complete headless command spec for a skill session."""
    spec = ClaudeCodeBackend().build_skill_session_cmd(
        skill_command,
        cwd=cwd,
        completion_marker=completion_marker,
        model=model,
        plugin_source=plugin_source,
        output_format=output_format,
        add_dirs=add_dirs,
        exit_after_stop_delay_ms=exit_after_stop_delay_ms,
        stream_idle_timeout_ms=stream_idle_timeout_ms,
        scenario_step_name=scenario_step_name,
        temp_dir_relpath=temp_dir_relpath,
        allowed_write_prefix=allowed_write_prefix,
        provider_extras=provider_extras,
        profile_name=profile_name,
        resume_session_id=resume_session_id,
        resume_checkpoint=resume_checkpoint,
        resume_message=resume_message,
    )
    return spec


def build_food_truck_cmd(
    *,
    orchestrator_prompt: str,
    plugin_source: PluginSource,
    cwd: str,
    completion_marker: str,
    resume_session_id: str | None = None,
    resume_checkpoint: SessionCheckpoint | None = None,
    model: str | None = None,
    env_extras: Mapping[str, str] | None = None,
    output_format: OutputFormat = OutputFormat.STREAM_JSON,
    exit_after_stop_delay_ms: int = 0,
    stream_idle_timeout_ms: int = 0,
    scenario_step_name: str = "",
    temp_dir_relpath: str | None = None,
    allowed_write_prefix: str = "",
    sentinel_contract: str = "",
    resume_message: str | None = None,
) -> CmdSpec:
    """Build the complete headless command spec for an L2 food truck session."""
    spec = ClaudeCodeBackend().build_food_truck_cmd(
        orchestrator_prompt=orchestrator_prompt,
        plugin_source=plugin_source,
        cwd=cwd,
        completion_marker=completion_marker,
        resume_session_id=resume_session_id,
        resume_checkpoint=resume_checkpoint,
        model=model,
        env_extras=env_extras,
        output_format=output_format,
        exit_after_stop_delay_ms=exit_after_stop_delay_ms,
        stream_idle_timeout_ms=stream_idle_timeout_ms,
        scenario_step_name=scenario_step_name,
        temp_dir_relpath=temp_dir_relpath,
        allowed_write_prefix=allowed_write_prefix,
        sentinel_contract=sentinel_contract,
        resume_message=resume_message,
    )
    return spec
