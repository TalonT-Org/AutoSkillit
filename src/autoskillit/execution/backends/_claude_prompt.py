"""Prompt injection utilities and session constants shared by claude, codex, and commands."""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from autoskillit.core import (
    ClaudeFlags,
    OutputFormat,
    SessionCheckpoint,
    extract_skill_name,
    temp_dir_display_str,
)


def _extract_write_artifacts(tool_uses: list[dict[str, Any]]) -> list[str]:
    return [
        t.get("file_path", "")
        for t in tool_uses
        if t.get("name") in {"Write", "Edit"} and t.get("file_path")
    ]


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
        "MCP_CONNECTION_NONBLOCKING": "0",
    }
)

# Non-negotiable env overrides applied to every headless subprocess launch.
# Injected after build_agent_env() so callers cannot clobber these values via
# extras. Scoped to headless/resume commands only — never applied to interactive
# sessions built by build_interactive_cmd().
_HEADLESS_ENV_HARDENING: dict[str, str] = {
    "TERM": "dumb",
    "NO_COLOR": "1",
}

# Keys excluded from the host env when building the interactive base env.
# Kept separate from _HEADLESS_ENV_HARDENING so that future headless-only
# additions to that set do not silently change interactive env filtering.
_INTERACTIVE_ENV_EXCLUSIONS: frozenset[str] = frozenset(_HEADLESS_ENV_HARDENING)

# Variables that _build_skill_session_cmd_impl controls exclusively. They must not
# leak from the host process environment — the caller opts in via explicit
# parameters (exit_after_stop_delay_ms, scenario_step_name, allowed_write_prefix, etc.).
# Note: CLAUDE_CODE_EXIT_AFTER_STOP_DELAY, SCENARIO_STEP_NAME, and
# MAX_MCP_OUTPUT_TOKENS also overlap with IDE_ENV_DENYLIST in
# core/_claude_env.py. AUTOSKILLIT_SESSION_TYPE, AUTOSKILLIT_CAMPAIGN_ID, and
# AUTOSKILLIT_PROVIDER_PROFILE overlap with AUTOSKILLIT_PRIVATE_ENV_VARS
# (scrubbed by build_agent_env). CLAUDE_CODE_SUBAGENT_MODEL also overlaps with
# IDE_ENV_PREFIX_DENYLIST via the CLAUDE_CODE_SUBAGENT_ prefix in _claude_env.py,
# providing structural immunity against future CLAUDE_CODE_SUBAGENT_* variables.
# All lists must be kept in sync when adding new exclusive variables.
_HEADLESS_EXCLUSIVE_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_CWD",
        "AUTOSKILLIT_COMPLETION_MARKER",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_LAUNCH_ID",
        "AUTOSKILLIT_PROVIDER_PROFILE",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_SKILL_NAME",
        "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
        "MAX_MCP_OUTPUT_TOKENS",
        "AUTOSKILLIT_SESSION_DEADLINE",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "SCENARIO_STEP_NAME",
    }
)


def _apply_output_format(cmd: list[str], output_format: OutputFormat) -> None:
    """Append --output-format and all required CLI flags, deduplicating."""
    cmd += [ClaudeFlags.OUTPUT_FORMAT, output_format.value]
    for flag in output_format.required_cli_flags:
        if flag not in cmd:
            cmd.append(flag)


def _ensure_skill_prefix(skill_command: str, *, provider_profile: str = "") -> str:
    """Prompt-formatting helper: wrap slash-commands for headless session loading.

    Transforms `/foo args` into `Use the /foo skill args` so non-Claude models
    recognize the slash command as a Skill tool invocation rather than a task description.

    This is NOT a validator. Non-slash input passes through unchanged by design —
    runtime validation is enforced by the skill_command_guard PreToolUse hook.
    """
    stripped = skill_command.strip()
    if stripped.startswith("/"):
        parts = stripped.split(None, 1)
        slash_cmd = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        formatted = f"Use the {slash_cmd} skill"
        if rest:
            formatted += f" {rest}"
        if provider_profile:
            skill_name = extract_skill_name(stripped) or slash_cmd.lstrip("/")
            formatted = (
                f"FIRST ACTION: Your first action should be to load the skill instructions "
                f'by calling the Skill tool with skill="{skill_name}". '
                f"If the Skill tool is unavailable or returns an error, read the skill "
                f"instructions from the skill's SKILL.md file instead.\n\n"
                f"{formatted}"
            )
        return formatted
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
        f"Do NOT use any other directory as a base for relative paths."
    )
    return skill_command + directive


def _inject_narration_suppression(skill_command: str, *, has_skill_prefix: bool = False) -> str:
    """Append an efficiency directive to suppress inter-tool narration.

    Targets prose status text and phase announcements emitted between tool
    calls — the primary driver of unnecessary context-length overhead in
    long-running sessions. Does NOT suppress the final response, which is
    where structured output tokens (worktree_path, plan_path, etc.) live.
    """
    opener = "After loading the skill instructions, d" if has_skill_prefix else "D"
    directive = (
        f"\n\nEFFICIENCY DIRECTIVE: {opener}o NOT output prose status text, phase "
        "announcements, or progress summaries between tool calls. Every "
        "non-final assistant turn MUST invoke at least one tool. The only "
        "permitted text-only turn is the final response required by the "
        "ORCHESTRATION DIRECTIVE above."
    )
    return skill_command + directive


def _inject_completion_reminder(prompt: str, marker: str) -> str:
    """Append a short completion marker reminder as the final prompt line.

    Provides recency-priority reinforcement for models that attend more strongly
    to end-of-prompt instructions.
    """
    if not marker:
        return prompt
    return f"{prompt}\nRemember: end your final response with {marker}"


def _compose_resume_prompt(
    *,
    base_prompt: str,
    resume_checkpoint: SessionCheckpoint | None,
    sentinel_contract: str = "",
    resume_message: str | None = None,
) -> str:
    """Compose resume directives ON TOP of the caller's base prompt.

    Never discards ``base_prompt`` — resume adds context layers, not substitutes.
    """
    sections: list[str] = []

    sections.append(
        "RESUME SESSION: Your previous session was interrupted before completion. "
        "Continue your work from where you left off. "
        "Do NOT restart from scratch — pick up exactly where you stopped. "
        "Do NOT re-emit any prior failure, exhaustion, or error sentinels from "
        "your conversation history. Conditions may have changed since the prior "
        "session — re-attempt the next pending operation."
    )

    if resume_message:
        sections.append(f"CALLER CONTEXT: {resume_message}")

    if resume_checkpoint and resume_checkpoint.completed_items:
        sections.append(_build_resume_context(resume_checkpoint))

    if sentinel_contract:
        sections.append(sentinel_contract)

    sections.append(f"ORIGINAL TASK CONTEXT:\n{base_prompt}")

    return "\n\n".join(sections)


def _build_resume_context(checkpoint: SessionCheckpoint) -> str:
    lines = [
        "RESUME CONTEXT: The following items were completed in the previous session "
        "and MUST be skipped. Do NOT redo any of them — continue from where the "
        "previous session left off.",
        "",
    ]
    for item in checkpoint.completed_items:
        lines.append(f"  - COMPLETED: {item}")
    if checkpoint.step_name:
        lines.append(f"\nLast active step: {checkpoint.step_name}")
    return "\n".join(lines)
