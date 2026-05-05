"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    BareResume,
    ClaudeFlags,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,  # noqa: F401, TC001
    ValidatedAddDir,
    build_claude_env,
    extract_skill_name,
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
    plugin_source: PluginSource | None = None,
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
    resume_spec: ResumeSpec = NoResume(),
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
    plugin_source
        When provided, determines the ``--plugin-dir`` flag. DirectInstall uses
        the plugin_dir path; MarketplaceInstall omits the flag (parent session
        already has it loaded).
    add_dirs
        Each entry is appended as ``--add-dir <path>``.
    resume_spec
        Resume intent discriminated union. ``NoResume`` (default) starts a fresh
        session. ``BareResume`` passes ``--resume`` without an ID (Claude Code's
        interactive picker). ``NamedResume`` passes ``--resume <id>``.
    env_extras
        Optional caller overrides merged into the resolved env after IDE scrubbing.
    """
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    match resume_spec:
        case NamedResume(session_id=sid):
            cmd += [ClaudeFlags.RESUME, sid]
        case BareResume():
            cmd.append(ClaudeFlags.RESUME)
        case NoResume():
            pass
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    match plugin_source:
        case DirectInstall(plugin_dir=p):
            cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
        case MarketplaceInstall():
            pass  # parent session already has the marketplace plugin loaded
        case None:
            pass
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
        "MCP_CONNECTION_NONBLOCKING": "0",
    }
)

# Variables that build_skill_session_cmd controls exclusively. They must not
# leak from the host process environment — the caller opts in via explicit
# parameters (exit_after_stop_delay_ms, scenario_step_name, allowed_write_prefix, etc.).
# Note: CLAUDE_CODE_EXIT_AFTER_STOP_DELAY, SCENARIO_STEP_NAME, and
# MAX_MCP_OUTPUT_TOKENS also overlap with IDE_ENV_DENYLIST in
# core/_claude_env.py. AUTOSKILLIT_SESSION_TYPE, AUTOSKILLIT_CAMPAIGN_ID, and
# AUTOSKILLIT_PROVIDER_PROFILE overlap with AUTOSKILLIT_PRIVATE_ENV_VARS
# (scrubbed by build_claude_env).
# All lists must be kept in sync when adding new exclusive variables.
_HEADLESS_EXCLUSIVE_VARS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_LAUNCH_ID",
        "AUTOSKILLIT_PROVIDER_PROFILE",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_SKILL_NAME",
        "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        "MAX_MCP_OUTPUT_TOKENS",
        "SCENARIO_STEP_NAME",
    }
)


def _apply_output_format(cmd: list[str], output_format: OutputFormat) -> None:
    """Append --output-format and all required CLI flags, deduplicating."""
    cmd += [ClaudeFlags.OUTPUT_FORMAT, output_format.value]
    for flag in output_format.required_cli_flags:
        if flag not in cmd:
            cmd.append(flag)


def build_headless_resume_cmd(
    *,
    resume_session_id: str,
    prompt: str,
    output_format: OutputFormat = OutputFormat.JSON,
    plugin_source: PluginSource | None = None,
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
    ]
    _apply_output_format(cmd, output_format)
    match plugin_source:
        case DirectInstall(plugin_dir=p):
            cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
        case MarketplaceInstall():
            pass  # parent session already has the marketplace plugin loaded
        case None:
            pass
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
        f"Do NOT use any other directory as a base for relative paths."
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
    scenario_step_name: str = "",
    temp_dir_relpath: str | None = None,
    allowed_write_prefix: str = "",
    provider_extras: Mapping[str, str] | None = None,
    profile_name: str = "",
    resume_session_id: str = "",
    resume_checkpoint: SessionCheckpoint | None = None,
) -> ClaudeHeadlessCmd:
    """Build the complete headless command spec for a skill session.

    A skill session is a direct child of an orchestrator: it runs a skill,
    always carries ``AUTOSKILLIT_SESSION_TYPE=skill``, and forwards
    ``AUTOSKILLIT_CAMPAIGN_ID`` from the parent env when present.

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
    plugin_source
        PluginSource determining the ``--plugin-dir`` flag. DirectInstall uses the
        path; MarketplaceInstall omits the flag.
    output_format
        OutputFormat enum; ``--output-format`` and any required flags are self-applied.
    add_dirs
        Each entry is appended as ``--add-dir <path>``.
    exit_after_stop_delay_ms
        When > 0, carried as ``CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=<ms>`` in ``.env``.
    scenario_step_name
        When non-empty, carried as ``SCENARIO_STEP_NAME=<name>`` in ``.env`` for recording.
    temp_dir_relpath
        Relative path to the temp directory injected into the CWD anchor directive.
        Falls back to the canonical default when None.
    allowed_write_prefix
        When non-empty, carried as ``AUTOSKILLIT_ALLOWED_WRITE_PREFIX`` in ``.env``.
    provider_extras
        Provider-profile env vars merged into the session environment after
        session-identity keys are set.  ``AUTOSKILLIT_SESSION_TYPE`` and
        ``AUTOSKILLIT_HEADLESS`` keys are silently ignored.
    profile_name
        When non-empty, carried as ``AUTOSKILLIT_PROVIDER_PROFILE`` in ``.env``.
    """
    if resume_session_id:
        _resume_instruction = (
            "Your previous session was interrupted before completion. "
            "Continue your work from where you left off. "
            "Do NOT restart from scratch — pick up exactly where you stopped."
        )
        if resume_checkpoint and resume_checkpoint.completed_items:
            _resume_instruction += "\n\n" + _build_resume_context(resume_checkpoint)
        prompt = _inject_narration_suppression(
            _inject_cwd_anchor(
                _inject_completion_directive(_resume_instruction, completion_marker),
                cwd,
                temp_dir_relpath=temp_dir_relpath,
            )
        )
    else:
        prompt = _inject_narration_suppression(
            _inject_cwd_anchor(
                _inject_completion_directive(
                    _ensure_skill_prefix(skill_command), completion_marker
                ),
                cwd,
                temp_dir_relpath=temp_dir_relpath,
            )
        )
    extras: dict[str, str] = {
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_SKILL,
        "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
        "MCP_CONNECTION_NONBLOCKING": "0",
    }
    if exit_after_stop_delay_ms > 0:
        extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
    if scenario_step_name:
        extras["SCENARIO_STEP_NAME"] = scenario_step_name
    campaign_id = os.environ.get(CAMPAIGN_ID_ENV_VAR)
    if campaign_id:
        extras[CAMPAIGN_ID_ENV_VAR] = campaign_id
    kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
    if kitchen_session_id:
        extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
    if allowed_write_prefix:
        extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = allowed_write_prefix
    extras["AUTOSKILLIT_SKILL_NAME"] = extract_skill_name(skill_command) or ""
    if provider_extras:
        for k, v in provider_extras.items():
            if k not in ("AUTOSKILLIT_SESSION_TYPE", "AUTOSKILLIT_HEADLESS"):
                extras[k] = v
    if profile_name:
        extras["AUTOSKILLIT_PROVIDER_PROFILE"] = profile_name

    filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
    spec = build_headless_cmd(prompt, model=model, env_extras=extras, base=filtered_base)
    cmd: list[str] = [*spec.cmd]
    match plugin_source:
        case DirectInstall(plugin_dir=p):
            cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
        case MarketplaceInstall():
            pass  # parent session already has the marketplace plugin loaded
        case None:
            pass
    _apply_output_format(cmd, output_format)
    for validated_dir in add_dirs:
        cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])
    if resume_session_id:
        cmd += [ClaudeFlags.RESUME, resume_session_id]

    return ClaudeHeadlessCmd(cmd=cmd, env=spec.env)


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
    scenario_step_name: str = "",
    temp_dir_relpath: str | None = None,
    allowed_write_prefix: str = "",
) -> ClaudeHeadlessCmd:
    """Build the complete headless command spec for an L3 food truck session.

    A food truck session is an L3 orchestrator: it runs a full recipe
    autonomously, always carries ``AUTOSKILLIT_SESSION_TYPE=orchestrator``,
    and restricts Claude native tools to ``--tools AskUserQuestion``.

    Unlike ``build_skill_session_cmd``, this builder:
    - Does NOT call ``_ensure_skill_prefix`` (prompt is a complete orchestrator prompt)
    - Sets ``SESSION_TYPE=orchestrator`` (not ``skill``)
    - Accepts caller-provided ``env_extras`` for campaign-specific variables
      (CAMPAIGN_ID, CAMPAIGN_STATE_PATH, PROJECT_DIR, L3_TOOL_TAGS, etc.)
    - Always emits ``--plugin-dir``: DirectInstall uses plugin_dir, MarketplaceInstall
      uses cache_path (food truck sessions are fresh subprocesses that need explicit
      plugin loading, unlike skill sessions where the parent already has it).

    Parameters
    ----------
    orchestrator_prompt
        Complete system prompt built by the fleet caller.
    plugin_source
        PluginSource determining the ``--plugin-dir`` path. Both DirectInstall and
        MarketplaceInstall produce a ``--plugin-dir`` flag (paths differ).
    cwd
        Absolute path to the working directory. Injected as anchor directive.
    completion_marker
        Marker string appended to the prompt as a completion directive.
    model
        Optional model override.
    env_extras
        Caller-provided env variables layered on top of the baseline.
        Used for CAMPAIGN_ID, CAMPAIGN_STATE_PATH, PROJECT_DIR, L2_TOOL_TAGS,
        IDLE_OUTPUT_TIMEOUT. These override baseline but cannot override
        SESSION_TYPE or HEADLESS (applied last).
    output_format
        OutputFormat enum; ``--output-format`` and any required flags are self-applied.
        Defaults to ``STREAM_JSON``.
    exit_after_stop_delay_ms
        If positive, sets ``CLAUDE_CODE_EXIT_AFTER_STOP_DELAY`` in the subprocess env.
        Zero (default) means the variable is omitted entirely.
    scenario_step_name
        If non-empty, sets ``SCENARIO_STEP_NAME`` in the subprocess env.
    temp_dir_relpath
        Relative path to the temp directory injected into the CWD anchor directive.
        Falls back to the canonical default when None.
    allowed_write_prefix
        If non-empty, sets ``AUTOSKILLIT_ALLOWED_WRITE_PREFIX`` in the subprocess env.
    """
    if resume_session_id:
        _resume_instruction = (
            "Your previous session was interrupted before completion. "
            "Continue your work from where you left off. "
            "Do NOT restart from scratch — pick up exactly where you stopped."
        )
        if resume_checkpoint and resume_checkpoint.completed_items:
            _resume_instruction += "\n\n" + _build_resume_context(resume_checkpoint)
        prompt = _inject_narration_suppression(
            _inject_cwd_anchor(
                _inject_completion_directive(_resume_instruction, completion_marker),
                cwd,
                temp_dir_relpath=temp_dir_relpath,
            )
        )
    else:
        # No _ensure_skill_prefix — orchestrator_prompt is a complete system prompt.
        prompt = _inject_narration_suppression(
            _inject_cwd_anchor(
                _inject_completion_directive(orchestrator_prompt, completion_marker),
                cwd,
                temp_dir_relpath=temp_dir_relpath,
            )
        )

    # Baseline env: headless + orchestrator session type + MCP settings.
    extras: dict[str, str] = {
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_ORCHESTRATOR,
        "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
        "MCP_CONNECTION_NONBLOCKING": "0",
    }
    if exit_after_stop_delay_ms > 0:
        extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
    if scenario_step_name:
        extras["SCENARIO_STEP_NAME"] = scenario_step_name
    kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
    if kitchen_session_id:
        extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
    if allowed_write_prefix:
        extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = allowed_write_prefix
    # Layer caller env_extras (campaign vars) UNDER the mandatory keys.
    # This ensures SESSION_TYPE and HEADLESS cannot be accidentally overridden.
    if env_extras:
        for k, v in env_extras.items():
            if k not in ("AUTOSKILLIT_SESSION_TYPE", "AUTOSKILLIT_HEADLESS"):
                extras[k] = v

    filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
    spec = build_headless_cmd(prompt, model=model, env_extras=extras, base=filtered_base)

    cmd: list[str] = [*spec.cmd]
    # Both install modes require --plugin-dir for food truck sessions (fresh subprocess).
    match plugin_source:
        case DirectInstall(plugin_dir=p):
            cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
        case MarketplaceInstall(cache_path=cp):
            cmd += [ClaudeFlags.PLUGIN_DIR, str(cp)]
    _apply_output_format(cmd, output_format)
    cmd += [ClaudeFlags.TOOLS, "AskUserQuestion"]
    if resume_session_id:
        cmd += [ClaudeFlags.RESUME, resume_session_id]

    return ClaudeHeadlessCmd(cmd=cmd, env=spec.env)
