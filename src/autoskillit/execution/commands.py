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
    ValidatedAddDir,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,  # noqa: F401 — re-export for downstream consumers
    _MAX_MCP_OUTPUT_TOKENS_VALUE,  # noqa: F401 — re-export for downstream consumers
    _SESSION_BASELINE_ENV,  # noqa: F401 — re-export for downstream consumers
    _apply_output_format,  # noqa: F401 — re-export for downstream consumers
    _build_resume_context,  # noqa: F401 — re-export for downstream consumers
    _compose_resume_prompt,  # noqa: F401 — re-export for downstream consumers
    _ensure_skill_prefix,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_directive,  # noqa: F401 — re-export for downstream consumers
    _inject_completion_reminder,  # noqa: F401 — re-export for downstream consumers
    _inject_cwd_anchor,  # noqa: F401 — re-export for downstream consumers
    _inject_narration_suppression,  # noqa: F401 — re-export for downstream consumers
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend


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
    tools: Sequence[str] = (),
) -> ClaudeInteractiveCmd:
    """Deprecated shim. Use ClaudeCodeBackend().build_interactive_cmd() directly."""
    spec = ClaudeCodeBackend().build_interactive_cmd(
        initial_prompt=initial_prompt,
        model=model,
        plugin_source=plugin_source,
        add_dirs=add_dirs,
        resume_spec=resume_spec,
        system_prompt=system_prompt,
        env_extras=env_extras,
        required_env=required_env,
        tools=tools,
    )
    return ClaudeInteractiveCmd(cmd=list(spec.cmd), env=spec.env)


def build_headless_cmd(
    prompt: str,
    *,
    model: str | None = None,
    env_extras: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> CmdSpec:
    """Deprecated shim. Use ClaudeCodeBackend().build_headless_cmd() directly."""
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
    """Deprecated shim. Use ClaudeCodeBackend().build_resume_cmd() directly."""
    spec = ClaudeCodeBackend().build_resume_cmd(
        resume_session_id=resume_session_id,
        prompt=prompt,
        output_format=output_format,
        plugin_source=plugin_source,
        env_extras=env_extras,
    )
    return spec
