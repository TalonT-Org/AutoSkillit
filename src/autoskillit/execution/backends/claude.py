from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CAMPAIGN_ID_ENV_VAR,
    CLAUDE_CODE_CAPABILITIES,
    CONTEXT_EXHAUSTION_MARKER,
    KITCHEN_SESSION_ID_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    AgentSessionResult,
    BackendCapabilities,
    BackendEventKind,
    BareResume,
    ClaudeEventData,
    ClaudeFlags,
    CmdSpec,
    DirectInstall,
    MarketplaceInstall,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    SessionEvent,
    ValidatedAddDir,
    build_agent_env,
    extract_skill_name,
    fast_loads,
    temp_dir_display_str,
)
from autoskillit.execution.session import parse_session_result

__all__ = [
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
]


def _marker_is_standalone(text: str, marker: str) -> bool:
    for text_line in text.splitlines():
        if text_line.strip() == marker:
            return True
    return False


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

# Variables that build_skill_session_cmd controls exclusively. They must not
# leak from the host process environment — the caller opts in via explicit
# parameters (exit_after_stop_delay_ms, scenario_step_name, allowed_write_prefix, etc.).
# Note: CLAUDE_CODE_EXIT_AFTER_STOP_DELAY, SCENARIO_STEP_NAME, and
# MAX_MCP_OUTPUT_TOKENS also overlap with IDE_ENV_DENYLIST in
# core/_claude_env.py. AUTOSKILLIT_SESSION_TYPE, AUTOSKILLIT_CAMPAIGN_ID, and
# AUTOSKILLIT_PROVIDER_PROFILE overlap with AUTOSKILLIT_PRIVATE_ENV_VARS
# (scrubbed by build_agent_env).
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
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
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


@dataclass(frozen=True, slots=True)
class ClaudeEnvPolicy:
    def build_env(self, base_env: Mapping[str, str]) -> dict[str, str]:
        return dict(build_agent_env(base=base_env))


@dataclass(frozen=True, slots=True)
class ClaudeSessionLocator:
    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        base = Path.home() / ".claude" / "projects"
        if not base.exists():
            return None
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class ClaudeStreamParser:
    completion_marker: str = ""

    def parse_line(self, line: str) -> SessionEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            obj = fast_loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None

        record_type = obj.get("type", "")

        if record_type == "system":
            session_id = obj.get("session_id", "")
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=session_id or None,
            )

        if record_type == "result":
            result_field = obj.get("result", "")
            if not (isinstance(result_field, str) and result_field.strip()):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            has_marker = bool(
                self.completion_marker
                and _marker_is_standalone(result_field, self.completion_marker)
            )
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=has_marker,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype=obj.get("subtype", ""),
                    session_id=obj.get("session_id", ""),
                    raw=obj,
                ),
            )

        if record_type == "assistant":
            if "message" not in obj and obj.get("output_tokens", -1) == 0:
                flat_content = obj.get("content", [])
                if isinstance(flat_content, list) and any(
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and CONTEXT_EXHAUSTION_MARKER in block.get("text", "").lower()
                    for block in flat_content
                ):
                    return SessionEvent(
                        kind=BackendEventKind.TOOL_OUTPUT,
                        is_terminal=False,
                        has_marker=False,
                        backend_data=ClaudeEventData(
                            record_type="assistant",
                            subtype="context_exhaustion",
                            session_id="",
                            raw=obj,
                        ),
                    )
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )


@dataclass(frozen=True, slots=True)
class ClaudeResultParser:
    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        session_id: str | None = None
        has_completion = False
        has_marker = False
        last_backend_data: ClaudeEventData | None = None
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
                if event.has_marker:
                    has_marker = True
                if isinstance(event.backend_data, ClaudeEventData):
                    last_backend_data = event.backend_data
        output = ""
        if last_backend_data and last_backend_data.raw:
            output = last_backend_data.raw.get("result", "")
        success = has_completion and has_marker
        return AgentSessionResult(
            success=success,
            exit_code=0 if success else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=session_id,
            output=output if isinstance(output, str) else "",
        )

    def parse_stdout(self, stdout: str) -> AgentSessionResult:
        result = parse_session_result(stdout)
        write_artifacts = _extract_write_artifacts(result.tool_uses)
        return AgentSessionResult(
            success=result.session_complete,
            exit_code=0 if result.session_complete else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=result.session_id or None,
            output=result.result,
            error="\n".join(result.errors) if result.errors else "",
            raw={
                "subtype": result.subtype.value,
                "is_error": result.is_error,
                "token_usage": result.token_usage,
                "write_artifacts": write_artifacts,
                "tool_uses": result.tool_uses,
                "assistant_messages": result.assistant_messages,
                "jsonl_context_exhausted": result.jsonl_context_exhausted,
                "stop_reasons": result.stop_reasons,
                "has_thinking_only_turn": result.has_thinking_only_turn,
                "seen_block_types": list(result.seen_block_types),
            },
        )


@dataclass(frozen=True, slots=True)
class ClaudeCodeBackend:
    @property
    def name(self) -> str:
        return AGENT_BACKEND_CLAUDE_CODE

    @property
    def capabilities(self) -> BackendCapabilities:
        return CLAUDE_CODE_CAPABILITIES

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return CmdSpec(cmd=spec.cmd, env=spec.env, cwd=cwd)

    def stream_parser(self) -> ClaudeStreamParser:
        return ClaudeStreamParser()

    def result_parser(self) -> ClaudeResultParser:
        return ClaudeResultParser()

    def env_policy(self) -> ClaudeEnvPolicy:
        return ClaudeEnvPolicy()

    def session_locator(self) -> ClaudeSessionLocator:
        return ClaudeSessionLocator()

    def write_tool_names(self) -> frozenset[str]:
        return frozenset({"Write", "Edit"})

    def binary_name(self) -> str:
        return "claude"

    def version_cmd(self) -> tuple[str, ...]:
        return ("claude", "--version")

    def build_headless_cmd(
        self,
        prompt: str,
        *,
        model: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        base: Mapping[str, str] | None = None,
    ) -> CmdSpec:
        cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
        if model:
            cmd += [ClaudeFlags.MODEL, model]
        return CmdSpec(cmd=tuple(cmd), env=build_agent_env(base=base, extras=env_extras))

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        plugin_source: PluginSource | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        resume_spec: ResumeSpec = NoResume(),
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
    ) -> CmdSpec:
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
        required_env
            Optional set of env var keys that must be present in the final env.
            Raise ``ValueError`` if any are missing.

        Orchestration level
        -------------------
        An interactive session operates at L1 (cook, ``autoskillit cook``) by default.
        It becomes an L2 orchestrator (order, ``autoskillit order``) when the user
        calls ``open_kitchen``, granting full kitchen access and the ability to dispatch
        L1 ``run_skill`` workers. Unlike headless sessions, the orchestration level of
        an interactive session is determined at runtime by kitchen state, not by a
        ``SESSION_TYPE`` env variable.
        """
        cmd: list[str] = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
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
                pass
            case None:
                pass
        for d in add_dirs:
            cmd += [ClaudeFlags.ADD_DIR, str(d)]
        if initial_prompt is not None:
            cmd.append(initial_prompt)
        merged: dict[str, str] = dict(_SESSION_BASELINE_ENV)
        if env_extras:
            merged.update(env_extras)
        return CmdSpec(
            cmd=tuple(cmd),
            env=build_agent_env(extras=merged, required=required_env),
        )

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_source: PluginSource | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec:
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
                pass
            case None:
                pass
        merged: dict[str, str] = dict(_SESSION_BASELINE_ENV)
        if env_extras:
            merged.update(env_extras)
        return CmdSpec(
            cmd=tuple(cmd),
            env=build_agent_env(base={}, extras=merged),
        )

    def build_skill_session_cmd(
        self,
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
        _has_prefix = bool(profile_name) and skill_command.strip().startswith("/")

        if resume_session_id:
            effective_prompt = _compose_resume_prompt(
                base_prompt=_ensure_skill_prefix(
                    skill_command, provider_profile=profile_name or ""
                ),
                resume_checkpoint=resume_checkpoint,
                resume_message=resume_message,
            )
        else:
            effective_prompt = _ensure_skill_prefix(
                skill_command, provider_profile=profile_name or ""
            )

        prompt = _inject_completion_reminder(
            _inject_narration_suppression(
                _inject_cwd_anchor(
                    _inject_completion_directive(effective_prompt, completion_marker),
                    cwd,
                    temp_dir_relpath=temp_dir_relpath,
                ),
                has_skill_prefix=_has_prefix,
            ),
            completion_marker,
        )
        extras: dict[str, str] = {
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_SKILL,
            "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
            "MCP_CONNECTION_NONBLOCKING": "0",
        }
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
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
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        spec = self.build_headless_cmd(prompt, model=model, env_extras=extras, base=filtered_base)
        cmd: list[str] = [*spec.cmd]
        match plugin_source:
            case DirectInstall(plugin_dir=p):
                cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
            case MarketplaceInstall():
                pass
            case None:
                pass
        _apply_output_format(cmd, output_format)
        for validated_dir in add_dirs:
            cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(cmd=tuple(cmd), env=spec.env)

    def build_food_truck_cmd(
        self,
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
        if resume_session_id:
            effective_prompt = _compose_resume_prompt(
                base_prompt=orchestrator_prompt,
                resume_checkpoint=resume_checkpoint,
                sentinel_contract=sentinel_contract,
                resume_message=resume_message,
            )
        else:
            effective_prompt = orchestrator_prompt

        prompt = _inject_completion_reminder(
            _inject_narration_suppression(
                _inject_cwd_anchor(
                    _inject_completion_directive(effective_prompt, completion_marker),
                    cwd,
                    temp_dir_relpath=temp_dir_relpath,
                )
            ),
            completion_marker,
        )

        extras: dict[str, str] = {
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_ORCHESTRATOR,
            "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
            "MCP_CONNECTION_NONBLOCKING": "0",
        }
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
        if scenario_step_name:
            extras["SCENARIO_STEP_NAME"] = scenario_step_name
        kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
        if kitchen_session_id:
            extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
        if allowed_write_prefix:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = allowed_write_prefix
        if env_extras:
            for k, v in env_extras.items():
                if k not in ("AUTOSKILLIT_SESSION_TYPE", "AUTOSKILLIT_HEADLESS"):
                    extras[k] = v

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        spec = self.build_headless_cmd(prompt, model=model, env_extras=extras, base=filtered_base)

        cmd: list[str] = [*spec.cmd]
        match plugin_source:
            case DirectInstall(plugin_dir=p):
                cmd += [ClaudeFlags.PLUGIN_DIR, str(p)]
            case MarketplaceInstall(cache_path=cp):
                cmd += [ClaudeFlags.PLUGIN_DIR, str(cp)]
        _apply_output_format(cmd, output_format)
        cmd += [ClaudeFlags.TOOLS, "AskUserQuestion"]
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(cmd=tuple(cmd), env=spec.env)
