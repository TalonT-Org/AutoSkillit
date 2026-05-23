"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    SESSION_TYPE_SKILL,
    AgentSessionResult,
    BackendCapabilities,
    BackendEventKind,
    CanonicalTokenUsage,
    CliSubtype,
    CmdSpec,
    CodexEventData,
    OutputFormat,
    PluginSource,
    SessionCheckpoint,
    SessionEvent,
    SkillSessionConfig,
    ValidatedAddDir,
    extract_skill_name,
    fast_loads,
)
from autoskillit.execution.backends.claude import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    _inject_completion_directive,
    _inject_completion_reminder,
    _inject_cwd_anchor,
    _inject_narration_suppression,
)
from autoskillit.execution.process import _marker_is_standalone

__all__ = [
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexResultParser",
    "CodexSessionLocator",
    "CodexStreamParser",
]

CODEX_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
    }
)

CODEX_ENV_PREFIX_DENYLIST: tuple[str, ...] = ("CLAUDE_CODE_",)


@unique
class CodexFlags(StrEnum):
    JSON = "--json"
    SANDBOX = "--sandbox"
    ASK_FOR_APPROVAL = "--ask-for-approval"
    ASK_FOR_APPROVAL_SHORT = "-a"
    MODEL = "--model"
    MODEL_SHORT = "-m"
    ADD_DIR = "--add-dir"
    IGNORE_USER_CONFIG = "--ignore-user-config"
    EPHEMERAL = "--ephemeral"
    RESUME_SUBCOMMAND = "resume"
    LAST = "--last"


@dataclass
class _CodexParseAccumulator:
    session_id: str = ""
    agent_messages: list[str] = field(default_factory=list)
    command_executions: list[dict[str, Any]] = field(default_factory=list)
    mcp_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    last_usage: dict[str, Any] | None = None
    saw_failure: bool = False
    success: bool = False
    error_message: str = ""


def _scan_codex_ndjson(stdout: str) -> _CodexParseAccumulator:
    if not stdout.strip():
        return _CodexParseAccumulator()
    acc = _CodexParseAccumulator()
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        event_type = obj.get("type", "")
        if event_type == "thread.started":
            acc.session_id = obj.get("thread_id", "")
        elif event_type == "item.completed":
            item = obj.get("item", {})
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type == "message":
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            acc.agent_messages.append(text)
            elif item_type == "function_call":
                acc.command_executions.append(item)
            elif item_type == "mcp_tool_call":
                acc.mcp_tool_calls.append(item)
            elif item_type == "file_change":
                path = item.get("path")
                if path:
                    acc.file_changes.append(path)
        elif event_type == "turn.completed":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                acc.last_usage = usage
            if not acc.saw_failure:
                acc.success = True
        elif event_type == "turn.failed":
            error = obj.get("error", {})
            if isinstance(error, dict):
                acc.error_message = error.get("message", "")
            else:
                acc.error_message = str(error) if error else ""
            acc.saw_failure = True
            acc.success = False
    return acc


@dataclass(frozen=True, slots=True)
class CodexResultParser:
    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        if not events:
            return AgentSessionResult(
                success=False,
                exit_code=1,
                backend_name=AGENT_BACKEND_CODEX,
                elapsed_seconds=0.0,
                error="empty events sequence",
            )
        session_id: str | None = None
        has_completion = False
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
        return AgentSessionResult(
            success=has_completion,
            exit_code=0 if has_completion else 1,
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=session_id,
        )

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult:
        acc = _scan_codex_ndjson(stdout)
        if acc.success:
            subtype = CliSubtype.SUCCESS.value
        elif acc.error_message:
            subtype = CliSubtype.ERROR_DURING_EXECUTION.value
        elif not stdout.strip():
            subtype = CliSubtype.EMPTY_OUTPUT.value
        else:
            subtype = CliSubtype.UNPARSEABLE.value
        is_error = subtype != CliSubtype.SUCCESS.value
        canonical_dict = None
        if acc.last_usage is not None:
            canonical = CanonicalTokenUsage.from_codex_dict(acc.last_usage)
            canonical_dict = canonical.to_dict()
        return AgentSessionResult(
            success=not is_error,
            exit_code=0 if not is_error else (exit_code or 1),
            backend_name=AGENT_BACKEND_CODEX,
            elapsed_seconds=0.0,
            session_id=acc.session_id or None,
            output="\n".join(acc.agent_messages),
            error=acc.error_message,
            raw={
                "subtype": subtype,
                "is_error": is_error,
                "token_usage": acc.last_usage,
                "canonical_token_usage": canonical_dict,
                "agent_messages": acc.agent_messages,
                "command_executions": acc.command_executions,
                "mcp_tool_calls": acc.mcp_tool_calls,
                "file_changes": acc.file_changes,
            },
        )


@dataclass(slots=True)
class CodexStreamParser:
    """Stateful NDJSON stream parser for Codex CLI output.

    One instance per session — accumulates marker detection state across
    parse_line() calls. Not reusable across sessions.
    """

    completion_marker: str = ""
    _saw_marker: bool = field(default=False, init=False, repr=False)

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

        event_type = obj.get("type", "")

        if event_type == "thread.started":
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("thread_id", "") or None,
            )

        if event_type == "item.completed":
            item = obj.get("item", {})
            if not isinstance(item, dict):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            item_type = item.get("type", "")

            if item_type == "message":
                for block in item.get("content", []):
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "text"
                        and self.completion_marker
                        and _marker_is_standalone(block.get("text", ""), self.completion_marker)
                    ):
                        self._saw_marker = True
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="message",
                        raw=obj,
                    ),
                )

            if item_type in ("file_change", "function_call"):
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type=item_type,
                        raw=obj,
                    ),
                )

            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == "turn.completed":
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=self._saw_marker,
                backend_data=CodexEventData(
                    record_type="turn.completed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                    usage=obj.get("usage"),
                ),
            )

        if event_type == "turn.failed":
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="turn.failed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        if event_type == "error":
            return SessionEvent(
                kind=BackendEventKind.ERROR,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="error",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )


@dataclass(frozen=True, slots=True)
class CodexEnvPolicy:
    def build_env(
        self,
        base_env: Mapping[str, str],
        *,
        extras: Mapping[str, str] | None = None,
        required: frozenset[str] | None = None,
    ) -> dict[str, str]:
        out: dict[str, str] = {
            k: v
            for k, v in base_env.items()
            if k not in CODEX_ENV_DENYLIST
            and k not in AUTOSKILLIT_PRIVATE_ENV_VARS
            and not any(k.startswith(p) for p in CODEX_ENV_PREFIX_DENYLIST)
        }
        if extras is not None:
            out.update(extras)
        if required is not None:
            missing = required - frozenset(out)
            if missing:
                raise ValueError(f"Required env vars missing from session env: {sorted(missing)}")
        return out


@dataclass(frozen=True, slots=True)
class CodexSessionLocator:
    def locate_session(self, session_id: str) -> Path | None:
        return None


@dataclass(frozen=True, slots=True)
class CodexBackend:
    @property
    def name(self) -> str:
        return AGENT_BACKEND_CODEX

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            channel_b_capable=False,
            pty_required=False,
            session_resume_capable=True,
            skill_injection_capable=False,
            supports_thinking_blocks=False,
            supports_claude_format_stdout=False,
            exit_code_is_terminal=True,
            mcp_config_capable=False,
            completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
            session_record_types=frozenset(),
        )

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return CmdSpec(cmd=spec.cmd, env=spec.env, cwd=cwd)

    def stream_parser(self, completion_marker: str = "") -> CodexStreamParser:
        return CodexStreamParser(completion_marker=completion_marker)

    def result_parser(self) -> CodexResultParser:
        return CodexResultParser()

    def env_policy(self) -> CodexEnvPolicy:
        return CodexEnvPolicy()

    def session_locator(self) -> CodexSessionLocator:
        return CodexSessionLocator()

    def write_tool_names(self) -> frozenset[str]:
        return frozenset()

    def binary_name(self) -> str:
        return "codex"

    def version_cmd(self) -> tuple[str, ...]:
        return ("codex", "--version")

    def build_headless_cmd(
        self,
        prompt: str,
        *,
        model: str | None = None,
        add_dirs: Sequence[str] = (),
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec:
        cmd: list[str] = [
            "codex",
            "exec",
            CodexFlags.JSON,
            CodexFlags.SANDBOX,
            "workspace-write",
        ]
        if model:
            cmd += [CodexFlags.MODEL, model]
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, d]
        cmd.append(prompt)
        base: dict[str, str] = dict(os.environ)
        if env_extras:
            base.update(env_extras)
        env = self.env_policy().build_env(base)
        return CmdSpec(cmd=tuple(cmd), env=env)

    def build_skill_session_cmd(
        self,
        skill_command: str,
        cwd: str = "",
        config: SkillSessionConfig | None = None,
        *,
        completion_marker: str = "",
        model: str | None = None,
        plugin_source: PluginSource | None = None,
        output_format: OutputFormat = OutputFormat.JSON,
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
        if config is not None:
            completion_marker = config.completion_marker
            model = config.model
            plugin_source = config.plugin_source  # noqa: F841  # no-op: Codex has no --plugin-dir equivalent
            output_format = config.output_format  # noqa: F841  # no-op: --json is unconditional for Codex
            add_dirs = config.add_dirs
            exit_after_stop_delay_ms = config.exit_after_stop_delay_ms  # noqa: F841  # no-op: Claude-only
            stream_idle_timeout_ms = config.stream_idle_timeout_ms  # noqa: F841  # no-op: Claude-only
            scenario_step_name = config.scenario_step_name
            temp_dir_relpath = config.temp_dir_relpath
            allowed_write_prefix = config.allowed_write_prefix
            provider_extras = config.provider_extras
            profile_name = config.profile_name
            resume_session_id = config.resume_session_id
            resume_checkpoint = config.resume_checkpoint
            resume_message = config.resume_message
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
        }
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
                if k not in (
                    "AUTOSKILLIT_SESSION_TYPE",
                    "AUTOSKILLIT_HEADLESS",
                    "MAX_MCP_OUTPUT_TOKENS",
                    "AUTOSKILLIT_SKILL_NAME",
                ):
                    extras[k] = v
        if profile_name:
            extras["AUTOSKILLIT_PROVIDER_PROFILE"] = profile_name
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(filtered_base, extras=extras)

        cmd: list[str] = [
            "codex",
            "exec",
            CodexFlags.JSON,
            CodexFlags.SANDBOX,
            "workspace-write",
            CodexFlags.ASK_FOR_APPROVAL_SHORT,
            "never",
        ]
        if model:
            cmd += [CodexFlags.MODEL, model]
        for validated_dir in add_dirs:
            cmd += [CodexFlags.ADD_DIR, validated_dir.path]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(cmd=tuple(cmd), env=env, cwd=cwd)

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
        raise NotImplementedError(
            "Codex CLI does not support L2 orchestrator (food truck) sessions"
        )

    def build_interactive_cmd(self, **kwargs: object) -> CmdSpec:
        raise NotImplementedError("Codex CLI does not support interactive mode")

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_source: PluginSource | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec:
        if not resume_session_id.strip():
            msg = "resume_session_id must be a non-empty string"
            raise ValueError(msg)
        cmd: list[str] = ["codex", "exec"]
        if output_format == OutputFormat.JSON:
            cmd.append(CodexFlags.JSON)
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(prompt)
        base: dict[str, str] = dict(os.environ)
        if env_extras:
            base.update(env_extras)
        env = self.env_policy().build_env(base)
        return CmdSpec(cmd=tuple(cmd), env=env)
