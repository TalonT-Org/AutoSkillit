from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_ENV_VAR,
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
    ClaudeDirectoryConventions,
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
    SkillSessionConfig,
    ValidatedAddDir,
    build_agent_env,
    extract_skill_name,
    fast_loads,
    pkg_root,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_ENV_HARDENING,
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    _SESSION_BASELINE_ENV,
    _apply_output_format,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    _extract_write_artifacts,
    _inject_completion_directive,
    _inject_completion_reminder,
    _inject_cwd_anchor,
    _inject_narration_suppression,
    _marker_is_standalone,
)
from autoskillit.execution.session import parse_session_result

__all__ = [
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
]


@dataclass(frozen=True, slots=True)
class ClaudeEnvPolicy:
    def build_env(
        self,
        base_env: Mapping[str, str],
        *,
        extras: Mapping[str, str] | None = None,
        required: frozenset[str] | None = None,
    ) -> dict[str, str]:
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
            subtype = obj.get("subtype", "")
            session_id = obj.get("session_id", "")
            if subtype == "api_retry":
                return SessionEvent(
                    kind=BackendEventKind.API_RETRY,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=ClaudeEventData(
                        record_type="system",
                        subtype="api_retry",
                        session_id=session_id,
                        raw=obj,
                    ),
                )
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=session_id if subtype == "init" else None,
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

    def parse_stdout(self, stdout: str, *, exit_code: int = 0) -> AgentSessionResult:
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

    def stream_parser(self, completion_marker: str = "") -> ClaudeStreamParser:
        return ClaudeStreamParser(completion_marker=completion_marker)

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
        env = dict(build_agent_env(base=base, extras=env_extras))
        env.update(_HEADLESS_ENV_HARDENING)
        return CmdSpec(cmd=tuple(cmd), env=env)

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        plugin_source: PluginSource | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        resume_spec: ResumeSpec = NoResume(),
        system_prompt: str | None = None,
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
        system_prompt
            Optional system prompt text. When provided and resume_spec is NoResume,
            appended as ``--append-system-prompt <value>``. Suppressed on resume
            sessions (BareResume or NamedResume) because ``--append-system-prompt``
            is incompatible with ``--resume``.
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
        if system_prompt is not None and isinstance(resume_spec, NoResume):
            cmd += [ClaudeFlags.APPEND_SYSTEM_PROMPT, system_prompt]
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
        env = dict(build_agent_env(base={}, extras=merged))
        env.update(_HEADLESS_ENV_HARDENING)
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
            return self._build_skill_session_cmd_impl(
                skill_command,
                cwd=cwd,
                completion_marker=config.completion_marker,
                model=config.model,
                plugin_source=config.plugin_source,
                output_format=config.output_format,
                add_dirs=config.add_dirs,
                exit_after_stop_delay_ms=config.exit_after_stop_delay_ms,
                stream_idle_timeout_ms=config.stream_idle_timeout_ms,
                scenario_step_name=config.scenario_step_name,
                temp_dir_relpath=config.temp_dir_relpath,
                allowed_write_prefix=config.allowed_write_prefix,
                provider_extras=config.provider_extras,
                profile_name=config.profile_name,
                resume_session_id=config.resume_session_id,
                resume_checkpoint=config.resume_checkpoint,
                resume_message=config.resume_message,
            )
        return self._build_skill_session_cmd_impl(
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

    def _build_skill_session_cmd_impl(
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
            AGENT_BACKEND_ENV_VAR: AGENT_BACKEND_CLAUDE_CODE,
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
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
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
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
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

    def validate_session_layout(self, session_dir: Path) -> list[str]:
        errors: list[str] = []
        skills_dir = session_dir / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        if not skills_dir.is_dir():
            errors.append(f"skills directory does not exist: {skills_dir}")
        else:
            skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]
            if not skill_dirs:
                errors.append(f"skills directory is empty: {skills_dir}")

            bundled_dir = pkg_root() / "skills"
            if bundled_dir.is_dir():
                bundled_names = {
                    d.name
                    for d in bundled_dir.iterdir()
                    if d.is_dir() and (d / "SKILL.md").is_file()
                }
                for sd in skill_dirs:
                    if sd.name in bundled_names:
                        errors.append(
                            f"BUNDLED skill {sd.name!r} should not be in ephemeral dir "
                            f"(served via --plugin-dir)"
                        )

        return errors
