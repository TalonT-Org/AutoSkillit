"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import ClaudeFlags, ValidatedAddDir


@dataclass(frozen=True)
class ClaudeInteractiveCmd:
    cmd: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class ClaudeHeadlessCmd:
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)  # always {}


def build_interactive_cmd(
    *,
    initial_prompt: str | None = None,
    model: str | None = None,
    plugin_dir: Path | None = None,
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
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
    """
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    if plugin_dir is not None:
        cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_dir)]
    for d in add_dirs:
        cmd += [ClaudeFlags.ADD_DIR, str(d)]
    if initial_prompt is not None:
        cmd.append(initial_prompt)
    return ClaudeInteractiveCmd(cmd=cmd, env={})


def build_headless_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={})


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


def _inject_cwd_anchor(skill_command: str, cwd: str) -> str:
    """Append a working directory anchor directive to prevent path contamination."""
    if not cwd or not os.path.isabs(cwd):
        return skill_command
    directive = (
        f"\n\nWORKING DIRECTORY ANCHOR: Your working directory is {cwd}. "
        f"All relative paths (temp/, .autoskillit/, etc.) MUST resolve against {cwd}. "
        f"Do NOT use any other directory as a base for relative paths, regardless of "
        f"what paths appear in code-index tool responses or set_project_path results. "
        f"The code-index project path is for READ-ONLY exploration only."
    )
    return skill_command + directive


def build_full_headless_cmd(
    skill_command: str,
    *,
    cwd: str,
    completion_marker: str,
    model: str | None,
    plugin_dir: str | Path,
    output_format_value: str,
    output_format_required_flags: Sequence[str] = (),
    add_dirs: Sequence[ValidatedAddDir] = (),
    exit_after_stop_delay_ms: int = 0,
) -> list[str]:
    """Build the complete headless command list ready for subprocess invocation.

    Applies prompt transformations (skill prefix, completion directive, cwd anchor),
    then constructs the full CLI command including plugin-dir, output-format,
    add-dir entries, and the ``env AUTOSKILLIT_HEADLESS=1`` prefix.

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
        When > 0, ``CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=<ms>`` is prepended.
    """
    prompt = _inject_cwd_anchor(
        _inject_completion_directive(_ensure_skill_prefix(skill_command), completion_marker),
        cwd,
    )
    spec = build_headless_cmd(prompt, model=model)
    cmd: list[str] = spec.cmd + [
        ClaudeFlags.PLUGIN_DIR,
        str(plugin_dir),
        ClaudeFlags.OUTPUT_FORMAT,
        output_format_value,
    ]
    for flag in output_format_required_flags:
        if flag not in cmd:
            cmd.append(flag)
    for validated_dir in add_dirs:
        cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])

    env_vars = ["AUTOSKILLIT_HEADLESS=1"]
    if exit_after_stop_delay_ms > 0:
        env_vars.append(f"CLAUDE_CODE_EXIT_AFTER_STOP_DELAY={exit_after_stop_delay_ms}")
    return ["env"] + env_vars + cmd
