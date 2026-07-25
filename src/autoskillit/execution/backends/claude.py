from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    CAMPAIGN_ID_ENV_VAR,
    CLAUDE_CODE_CAPABILITIES,
    CONTEXT_EXHAUSTION_MARKER,
    NON_VARIADIC_CLAUDE_FLAGS,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    VARIADIC_CLAUDE_FLAGS,
    AgentSessionResult,
    BackendCapabilities,
    BackendConventions,
    BackendEventKind,
    BareResume,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    ClaudeEventData,
    ClaudeFlags,
    CmdSpec,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    SessionEvent,
    SessionLocator,
    SkillSessionConfig,
    ValidatedAddDir,
    YAMLError,
    build_agent_env,
    claude_code_log_path,
    claude_code_project_dir,
    extract_skill_name,
    fast_loads,
    load_yaml,
    pkg_root,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
    FlagVocabulary,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_ENV_HARDENING,
    _HEADLESS_EXCLUSIVE_VARS,
    _INTERACTIVE_ENV_EXCLUSIONS,
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _SKILL_SESSION_EXTRAS_DENYLIST,
    PromptBuildContext,
    _apply_output_format,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    _extract_write_artifacts,
    apply_prompt_injector_chain,
)
from autoskillit.execution.backends._cmd_builder import CmdBuilder
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import parse_session_result

log = logging.getLogger(__name__)  # noqa: TID251 — stdlib fallback: used before configure_logging(); structlog proxy would emit to stderr via import-time WriteLoggerFactory

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
        return dict(build_agent_env(base=base_env, extras=extras, required=required))


@dataclass(frozen=True, slots=True)
class ClaudeSessionLocator(SessionLocator):
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

    def project_log_dir(self, cwd: str) -> Path:
        return claude_code_project_dir(cwd)

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        return claude_code_log_path(cwd, session_id)


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
class ClaudeCodeBackend(BackendCmdBuilderBase):
    def _binary(self) -> str:
        return "claude"

    def _sandbox_default(self) -> str:
        return "workspace-write"

    def _env_policy(self) -> ClaudeEnvPolicy:
        return ClaudeEnvPolicy()

    def _flag_vocabulary(self) -> FlagVocabulary:
        return FlagVocabulary(
            variadic_flags=VARIADIC_CLAUDE_FLAGS,
            non_variadic_flags=NON_VARIADIC_CLAUDE_FLAGS,
            model_flag=ClaudeFlags.MODEL,
            add_dir_flag=ClaudeFlags.ADD_DIR,
            resume_flag=ClaudeFlags.RESUME,
            config_override_flag="",
        )

    @property
    def name(self) -> str:
        return AGENT_BACKEND_CLAUDE_CODE

    @property
    def capabilities(self) -> BackendCapabilities:
        return CLAUDE_CODE_CAPABILITIES

    @property
    def conventions(self) -> BackendConventions:
        return BackendConventions(
            skills_subdir=ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR,
            project_local_skill_search_dirs=(
                ".claude/skills",
                ".autoskillit/skills",
                ".agents/skills",
            ),
            skill_sigil=self.capabilities.skill_sigil,
        )

    def setup_session_dir(self, session_dir: Path) -> None:
        pass

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

    def translate_model(self, model: str) -> str:
        from autoskillit.core import (
            CLAUDE_MODEL_ALIASES,
            strip_context_window_suffix,
        )

        base = strip_context_window_suffix(model)
        resolved = CLAUDE_MODEL_ALIASES.get(base, base)
        if self.capabilities.supports_context_window_suffix:
            suffix = model[len(base) :]
            return resolved + suffix
        return resolved

    def model_config_overrides(self, model: str) -> tuple[str, ...]:
        return ()

    def version_cmd(self) -> tuple[str, ...]:
        return ("claude", "--version")

    def build_headless_cmd(
        self,
        prompt: str,
        *,
        model: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        base: Mapping[str, str] | None = None,
        required: frozenset[str] | None = None,
    ) -> CmdSpec:
        cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
        if model:
            cmd += [ClaudeFlags.MODEL, self.translate_model(model)]
        env = dict(build_agent_env(base=base, extras=env_extras, required=required))
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
        tools: Sequence[str] = (),
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
            When provided, emits ``--plugin-dir``. The type guarantees the path is
            a sanitized projection. ``None`` omits the flag — that is how "the
            parent session already has the plugin loaded" is expressed.
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
        builder = CmdBuilder("claude")
        builder.mode_flag(ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS)
        match resume_spec:
            case NamedResume(session_id=sid):
                builder.kv_flag(ClaudeFlags.RESUME, sid)
            case BareResume():
                builder.mode_flag(ClaudeFlags.RESUME)
            case NoResume():
                pass
        if system_prompt is not None and isinstance(resume_spec, NoResume):
            builder.kv_flag(ClaudeFlags.APPEND_SYSTEM_PROMPT, system_prompt)
        if model:
            builder.kv_flag(ClaudeFlags.MODEL, self.translate_model(model))
        if plugin_source is not None:
            builder.kv_flag(ClaudeFlags.PLUGIN_DIR, str(plugin_source.plugin_dir))
        if initial_prompt is not None:
            builder.positional(initial_prompt)
        for d in add_dirs:
            builder.variadic_pair(ClaudeFlags.ADD_DIR, str(d))
        for t in tools:
            builder.variadic_pair(ClaudeFlags.TOOLS, t)
        merged: dict[str, str] = dict(SHARED_BASELINE_ENV)
        merged[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        merged[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if env_extras:
            merged.update(env_extras)
        interactive_base = {
            k: v for k, v in os.environ.items() if k not in _INTERACTIVE_ENV_EXCLUSIONS
        }
        partial = builder.build()
        return CmdSpec(
            cmd=partial.cmd,
            env=build_agent_env(base=interactive_base, extras=merged, required=required_env),
            origin=partial.origin,
            is_resume=isinstance(resume_spec, (NamedResume, BareResume)),
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
        if plugin_source is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_source.plugin_dir)]
        merged: dict[str, str] = dict(SHARED_BASELINE_ENV)
        merged[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        merged[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if env_extras:
            merged.update(env_extras)
        env = dict(build_agent_env(base={}, extras=merged))
        env.update(_HEADLESS_ENV_HARDENING)
        return CmdSpec(cmd=tuple(cmd), env=env, is_resume=True)

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
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_extras: Mapping[str, str] | None = None,
        profile_name: str = "",
        resume_session_id: str = "",
        resume_checkpoint: SessionCheckpoint | None = None,
        resume_message: str | None = None,
    ) -> CmdSpec:
        if config is not None:
            cfg = self._apply_config(config)
            completion_marker = cfg["completion_marker"]
            model = cfg["model"]
            plugin_source = cfg["plugin_source"]
            output_format = cfg["output_format"]
            add_dirs = cfg["add_dirs"]
            exit_after_stop_delay_ms = cfg["exit_after_stop_delay_ms"]
            stream_idle_timeout_ms = cfg["stream_idle_timeout_ms"]
            scenario_step_name = cfg["scenario_step_name"]
            temp_dir_relpath = cfg["temp_dir_relpath"]
            allowed_write_prefix = cfg["allowed_write_prefix"]
            allowed_write_prefixes = cfg["allowed_write_prefixes"]
            provider_extras = cfg["provider_extras"]
            profile_name = cfg["profile_name"]
            resume_session_id = cfg["resume_session_id"]
            resume_checkpoint = cfg["resume_checkpoint"]
            resume_message = cfg["resume_message"]
            sandbox_mode = cfg["sandbox_mode"]  # noqa: F841

        _has_prefix = (
            bool(profile_name)
            and skill_command.strip().startswith("/")
            and self.capabilities.skill_sigil == "/"
        )

        if resume_session_id:
            effective_prompt = _compose_resume_prompt(
                base_prompt=_ensure_skill_prefix(
                    skill_command,
                    provider_profile=profile_name or "",
                    skill_sigil=self.capabilities.skill_sigil,
                ),
                resume_checkpoint=resume_checkpoint,
                resume_message=resume_message,
            )
        else:
            effective_prompt = _ensure_skill_prefix(
                skill_command,
                provider_profile=profile_name or "",
                skill_sigil=self.capabilities.skill_sigil,
            )

        prompt = apply_prompt_injector_chain(
            effective_prompt,
            PromptBuildContext(
                completion_marker=completion_marker,
                cwd=cwd,
                temp_dir_relpath=temp_dir_relpath,
                has_skill_prefix=_has_prefix,
                profile_name=profile_name,
                include_output_discipline=False,
                include_intake_discipline=False,
            ),
        )
        extras = self._assemble_shared_env_extras(
            session_type=SESSION_TYPE_SKILL,
            applicable_guards=self.capabilities.applicable_guards,
            write_guard_tool_names=self.capabilities.write_guard_tool_names,
            write_prefix=allowed_write_prefix,
            write_prefixes=allowed_write_prefixes,
            cwd=cwd,
            scenario_step_name=scenario_step_name,
        )
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
        extras["AUTOSKILLIT_SKILL_NAME"] = extract_skill_name(skill_command) or ""
        if provider_extras:
            for k, v in provider_extras.items():
                if k not in _SKILL_SESSION_EXTRAS_DENYLIST:
                    extras[k] = v
        if profile_name:
            extras[PROVIDER_PROFILE_ENV_VAR] = profile_name
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        spec = self.build_headless_cmd(
            prompt,
            model=model,
            env_extras=extras,
            base=filtered_base,
            required=SKILL_SESSION_REQUIRED_ENV,
        )
        cmd: list[str] = [*spec.cmd]
        if plugin_source is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_source.plugin_dir)]
        _apply_output_format(cmd, output_format)
        for validated_dir in add_dirs:
            cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(cmd=tuple(cmd), env=spec.env, is_resume=bool(resume_session_id))

    def build_food_truck_cmd(
        self,
        *,
        orchestrator_prompt: str,
        plugin_source: PluginSource | None,
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
        allowed_write_prefixes: tuple[str, ...] = (),
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

        prompt = apply_prompt_injector_chain(
            effective_prompt,
            PromptBuildContext(
                completion_marker=completion_marker,
                cwd=cwd,
                temp_dir_relpath=temp_dir_relpath,
                has_skill_prefix=False,
                profile_name="",
                include_output_discipline=False,
                include_intake_discipline=False,
            ),
        )

        extras = self._assemble_shared_env_extras(
            session_type=SESSION_TYPE_ORCHESTRATOR,
            applicable_guards=self.capabilities.applicable_guards,
            write_guard_tool_names=self.capabilities.write_guard_tool_names,
            write_prefix=allowed_write_prefix,
            write_prefixes=allowed_write_prefixes,
            cwd=cwd,
            scenario_step_name=scenario_step_name,
        )
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
        extras.pop(CAMPAIGN_ID_ENV_VAR, None)  # food truck does not propagate campaign ID
        if env_extras:
            for k, v in env_extras.items():
                if k not in _PROVIDER_EXTRAS_BASE_DENYLIST:
                    extras[k] = v

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        spec = self.build_headless_cmd(
            prompt,
            model=model,
            env_extras=extras,
            base=filtered_base,
            required=ORCHESTRATOR_SESSION_REQUIRED_ENV,
        )

        cmd: list[str] = [*spec.cmd]
        if plugin_source is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_source.plugin_dir)]
        _apply_output_format(cmd, output_format)
        cmd += [ClaudeFlags.TOOLS, "AskUserQuestion"]
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(cmd=tuple(cmd), env=spec.env, is_resume=bool(resume_session_id))

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

    def validate_skill_content(self, content: str) -> list[str]:
        if not content.startswith("---"):
            return ["Invalid frontmatter: no opening --- delimiter found"]
        parts = content.split("---", maxsplit=2)
        if len(parts) < 3:
            return ["Invalid frontmatter: no closing --- delimiter found"]
        yaml_block = parts[1]
        try:
            data = load_yaml(yaml_block)
        except YAMLError as exc:
            return [f"Invalid frontmatter: YAML parse error: {exc}"]
        if not isinstance(data, dict):
            data = {}
        return [
            f"Missing required frontmatter field: '{f}'"
            for f in self.capabilities.required_skill_fields
            if f not in data
        ]

    def version(self) -> str:
        exec_path = os.environ.get("CLAUDE_CODE_EXECPATH") or self.version_cmd()[0]
        cmd = (exec_path,) + self.version_cmd()[1:]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return ""
        except Exception:
            log.warning("version() failed", exc_info=True)
            return ""

    def list_plugins(self) -> list[dict[str, Any]]:
        try:
            path = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
            if not path.exists():
                return []
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return []
            plugins = data.get("plugins", {})
            if not isinstance(plugins, dict):
                return []
            result: list[dict[str, Any]] = []
            for ref, installs in plugins.items():
                if not isinstance(installs, list) or not installs:
                    continue
                first = installs[0] if isinstance(installs[0], dict) else {}
                entry: dict[str, Any] = {"ref": ref}
                if "version" in first:
                    entry["version"] = first["version"]
                result.append(entry)
            return result
        except Exception:
            log.warning("list_plugins() failed", exc_info=True)
            return []

    def ensure_pre_launch(self) -> list[str]:
        return []

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        if not self.capabilities.inspector_capable:
            raise CapabilityNotSupportedError("inspector_capable", self.name)
        msg = "inspector_capable is True but build_inspector_cmd has no implementation"
        raise AssertionError(msg)
