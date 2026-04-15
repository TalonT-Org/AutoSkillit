"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import (
    ClaudeFlags,
    ValidatedAddDir,
    build_claude_env,
    temp_dir_display_str,
)


@dataclass(frozen=True)
class ClaudeInteractiveCmd:
    """Resolved argv + env for a claude interactive subprocess.

    ``env`` is the fully resolved environment returned by
    :func:`build_claude_env` — pass directly to ``subprocess.run(env=...)``.
    Callers must NOT merge in ``os.environ`` again; the sanitization layer
    has already applied the denylist and the auto-connect suppressor.
    """

    cmd: list[str]
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaudeHeadlessCmd:
    """Resolved argv + env for a claude headless subprocess.

    ``env`` is the fully resolved environment returned by
    :func:`build_claude_env`, including any headless-only extras such as
    ``AUTOSKILLIT_HEADLESS=1``. Pass directly to the subprocess runner.
    """

    cmd: list[str]
    env: Mapping[str, str] = field(default_factory=dict)


def build_interactive_cmd(
    *,
    initial_prompt: str | None = None,
    model: str | None = None,
    plugin_dir: Path | None = None,
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
    resume_session_id: str | None = None,
    env_extras: Mapping[str, str] | None = None,
) -> ClaudeInteractiveCmd:
    """Build a Claude interactive session command.

    Parameters
    ----------
    initial_prompt
        When provided, appended as a positional argument. Claude Code treats
        positional arguments as the user's first message, auto-submitted on
        session start.
    model
        Optional model override.
    plugin_dir
        When provided, appended as ``--plugin-dir <path>``.
    add_dirs
        Each entry is appended as ``--add-dir <path>``.
    resume_session_id
        When provided, appended as ``--resume <id>`` before any positional prompt.
    env_extras
        Optional caller overrides merged into the resolved env after IDE scrubbing.
    """
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if resume_session_id is not None:
        cmd += [ClaudeFlags.RESUME, resume_session_id]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    if plugin_dir is not None:
        cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_dir)]
    for d in add_dirs:
        cmd += [ClaudeFlags.ADD_DIR, str(d)]
    if initial_prompt is not None:
        cmd.append(initial_prompt)
    merged: dict[str, str] = dict(_SESSION_BASELINE_ENV)
    if env_extras:
        merged.update(env_extras)
    return ClaudeInteractiveCmd(cmd=cmd, env=build_claude_env(extras=merged))


def build_headless_cmd(
    prompt: str,
    *,
    model: str | None = None,
    env_extras: Mapping[str, str] | None = None,
    base: Mapping[str, str] | None = None,
) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env=build_claude_env(base=base, extras=env_extras))


# Injected into every AutoSkillit-launched headless and cook session.
# Raises the Claude Code client-side MCP tool result size gate from the
# default 25,000 tokens to 50,000, preventing open_kitchen() responses
# from being persisted to a file instead of returned inline.
_MAX_MCP_OUTPUT_TOKENS_VALUE: str = "50000"

# Baseline env vars injected into EVERY AutoSkillit-launched Claude session
# (both interactive and headless). Callers can override via env_extras.
# Analogous to IDE_ENV_ALWAYS_EXTRAS in _claude_env.py but scoped to
# session-level concerns rather than IDE scrubbing.
_SESSION_BASELINE_ENV: Mapping[str, str] = MappingProxyType(
    {
        "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
    }
)

# Variables that build_full_headless_cmd controls exclusively. They must not
# leak from the host process environment — the caller opts in via explicit
# parameters (exit_after_stop_delay_ms, scenario_step_name).
# Note: these overlap with IDE_ENV_DENYLIST in core/_claude_env.py, which
# strips the same keys as part of the broader IDE env scrubbing layer.
# Both lists must be kept in sync when adding new exclusive variables.
_HEADLESS_EXCLUSIVE_VARS: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        "SCENARIO_STEP_NAME",
        "MAX_MCP_OUTPUT_TOKENS",
    }
)


def build_headless_resume_cmd(
    *,
    resume_session_id: str,
    prompt: str,
    output_format: str = "json",
    plugin_dir: Path | None = None,
    env_extras: Mapping[str, str] | None = None,
) -> ClaudeHeadlessCmd:
    """Build a headless resume command for contract recovery nudge.

    Resumes an existing session with a short feedback prompt, asking the model
    to emit missing structured output tokens. Uses ``--output-format json``
    (not stream-json) because the response is tiny and needs no assistant records.
    """
    cmd: list[str] = [
        "claude",
        ClaudeFlags.PRINT,
        prompt,
        ClaudeFlags.RESUME,
        resume_session_id,
        ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS,
        ClaudeFlags.OUTPUT_FORMAT,
        output_format,
    ]
    if plugin_dir is not None:
        cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_dir)]
    merged: dict[str, str] = dict(_SESSION_BASELINE_ENV)
    if env_extras:
        merged.update(env_extras)
    return ClaudeHeadlessCmd(cmd=cmd, env=build_claude_env(extras=merged))


def _ensure_skill_prefix(skill_command: str) -> str:
    """Prompt-formatting helper: prepend 'Use ' to slash-commands for headless session loading.

    This is NOT a validator. Non-slash input passes through unchanged by design —
    runtime validation is enforced by the skill_command_guard PreToolUse hook.
    """
    stripped = skill_command.strip()
    if stripped.startswith("/"):
        return f"Use {stripped}"
    return skill_command


def _inject_completion_directive(skill_command: str, marker: str) -> str:
    """Append an orchestration directive to make the session write a completion marker."""
    directive = (
        f"\n\nORCHESTRATION DIRECTIVE: When your task is complete, "
        f"your final text output MUST end with: {marker}\n"
        f"CRITICAL: Append {marker} at the very end of your substantive response, "
        f"in the SAME message. Do NOT output {marker} as a separate standalone message."
    )
    return skill_command + directive


def _inject_cwd_anchor(skill_command: str, cwd: str, temp_dir_relpath: str | None = None) -> str:
    """Append a working directory anchor directive to prevent path contamination."""
    if not cwd or not os.path.isabs(cwd):
        return skill_command
    relpath = temp_dir_relpath if temp_dir_relpath is not None else temp_dir_display_str(None)
    directive = (
        f"\n\nWORKING DIRECTORY ANCHOR: Your working directory is {cwd}. "
        f"All relative paths ({relpath}/, .autoskillit/, etc.) "
        f"MUST resolve against {cwd}. "
        f"Do NOT use any other directory as a base for relative paths, regardless of "
        f"what paths appear in code-index tool responses or set_project_path results. "
        f"The code-index project path is for READ-ONLY exploration only."
    )
    return skill_command + directive


def _inject_narration_suppression(skill_command: str) -> str:
    """Append an efficiency directive to suppress inter-tool narration.

    Targets prose status text and phase announcements emitted between tool
    calls — the primary driver of unnecessary context-length overhead in
    long-running sessions. Does NOT suppress the final response, which is
    where structured output tokens (worktree_path, plan_path, etc.) live.
    """
    directive = (
        "\n\nEFFICIENCY DIRECTIVE: Do NOT output prose status text, phase "
        "announcements, or progress summaries between tool calls. Every "
        "non-final assistant turn MUST invoke at least one tool. The only "
        "permitted text-only turn is the final response required by the "
        "ORCHESTRATION DIRECTIVE above."
    )
    return skill_command + directive


def build_full_headless_cmd(
    skill_command: str,
    *,
    cwd: str,
    completion_marker: str,
    model: str | None,
    plugin_dir: str | Path | None,
    output_format_value: str,
    output_format_required_flags: Sequence[str] = (),
    add_dirs: Sequence[ValidatedAddDir] = (),
    exit_after_stop_delay_ms: int = 0,
    scenario_step_name: str = "",
    temp_dir_relpath: str | None = None,
) -> ClaudeHeadlessCmd:
    """Build the complete headless command spec ready for subprocess invocation.

    Applies prompt transformations (skill prefix, completion directive, cwd anchor,
    narration suppression), then constructs the full CLI command including plugin-dir,
    output-format, and add-dir entries. The environment carries ``AUTOSKILLIT_HEADLESS=1``
    and any scenario / exit-delay extras on ``.env`` — it is NOT serialized as an argv
    prefix.

    Parameters
    ----------
    skill_command
        Raw slash-command string (e.g. ``/autoskillit:investigate foo``).
    cwd
        Absolute path to the working directory. Injected as anchor directive.
    completion_marker
        Marker string appended to the prompt as a completion directive.
    model
        Optional model override; passed through to ``build_headless_cmd``.
    plugin_dir
        Path passed as ``--plugin-dir`` flag.
    output_format_value
        String value passed as ``--output-format`` flag.
    output_format_required_flags
        Additional CLI flags required by the output format; deduplicated.
    add_dirs
        Each entry is appended as ``--add-dir <path>``.
    exit_after_stop_delay_ms
        When > 0, carried as ``CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=<ms>`` in ``.env``.
    scenario_step_name
        When non-empty, carried as ``SCENARIO_STEP_NAME=<name>`` in ``.env`` for recording.
    """
    prompt = _inject_narration_suppression(
        _inject_cwd_anchor(
            _inject_completion_directive(_ensure_skill_prefix(skill_command), completion_marker),
            cwd,
            temp_dir_relpath=temp_dir_relpath,
        )
    )
    extras: dict[str, str] = {
        "AUTOSKILLIT_HEADLESS": "1",
        "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
    }
    if exit_after_stop_delay_ms > 0:
        extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
    if scenario_step_name:
        extras["SCENARIO_STEP_NAME"] = scenario_step_name

    filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
    spec = build_headless_cmd(prompt, model=model, env_extras=extras, base=filtered_base)
    cmd: list[str] = [*spec.cmd]
    if plugin_dir is not None:
        cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_dir)]
    cmd += [ClaudeFlags.OUTPUT_FORMAT, output_format_value]
    for flag in output_format_required_flags:
        if flag not in cmd:
            cmd.append(flag)
    for validated_dir in add_dirs:
        cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])

    return ClaudeHeadlessCmd(cmd=cmd, env=spec.env)
