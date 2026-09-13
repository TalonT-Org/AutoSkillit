"""Claude skill-session and orchestrator command construction."""

from __future__ import annotations

import os
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    CAMPAIGN_ID_ENV_VAR,
    CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    BackendCapabilities,
    ClaudeFlags,
    CmdSpec,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    OutputFormat,
    PluginLaunchBinding,
    SessionCheckpoint,
    SkillSessionConfig,
    ValidatedAddDir,
    extract_skill_name,
)
from autoskillit.execution.backends._backend_cmd_builder_base import BackendCmdBuilderBase
from autoskillit.execution.backends._claude.environment import _claude_host_attestation_env
from autoskillit.execution.backends._claude_prompt import (
    _CLAUDE_SKILL_SESSION_HARDENING,
    _HEADLESS_EXCLUSIVE_VARS,
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _SKILL_SESSION_EXTRAS_DENYLIST,
    PromptBuildContext,
    _apply_output_format,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    apply_prompt_injector_chain,
)


class ClaudeSessionCommandMixin(BackendCmdBuilderBase):
    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Return concrete Claude backend capabilities."""

    @abstractmethod
    def translate_model(self, model: str) -> str:
        """Resolve a configured Claude model alias."""

    @abstractmethod
    def build_headless_cmd(self, *args: Any, **kwargs: Any) -> CmdSpec:
        """Build the concrete Claude headless command."""

    def build_skill_session_cmd(
        self,
        skill_command: str,
        cwd: str = "",
        config: SkillSessionConfig | None = None,
        *,
        completion_marker: str = "",
        model: str | None = None,
        plugin_binding: PluginLaunchBinding | None = None,
        output_format: OutputFormat = OutputFormat.JSON,
        add_dirs: Sequence[ValidatedAddDir] = (),
        exit_after_stop_delay_ms: int = 0,
        stream_idle_timeout_ms: int = 0,
        mcp_tool_timeout_sec: float = 0.0,
        scenario_step_name: str = "",
        child_outcome_log_dir: str = "",
        temp_dir_relpath: str | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_extras: Mapping[str, str] | None = None,
        profile_name: str = "",
        resume_session_id: str = "",
        resume_checkpoint: SessionCheckpoint | None = None,
        resume_message: str | None = None,
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
    ) -> CmdSpec:
        if config is not None:
            cfg = self._apply_config(config)
            completion_marker = cfg["completion_marker"]
            model = cfg["model"]
            plugin_binding = cfg["plugin_binding"]
            output_format = cfg["output_format"]
            add_dirs = cfg["add_dirs"]
            exit_after_stop_delay_ms = cfg["exit_after_stop_delay_ms"]
            stream_idle_timeout_ms = cfg["stream_idle_timeout_ms"]
            mcp_tool_timeout_sec = cfg["mcp_tool_timeout_sec"]
            scenario_step_name = cfg["scenario_step_name"]
            child_outcome_log_dir = cfg["child_outcome_log_dir"]
            temp_dir_relpath = cfg["temp_dir_relpath"]
            allowed_write_prefix = cfg["allowed_write_prefix"]
            allowed_write_prefixes = cfg["allowed_write_prefixes"]
            provider_extras = cfg["provider_extras"]
            profile_name = cfg["profile_name"]
            resume_session_id = cfg["resume_session_id"]
            resume_checkpoint = cfg["resume_checkpoint"]
            resume_message = cfg["resume_message"]
            sandbox_mode = cfg["sandbox_mode"]  # noqa: F841
            force_inactive_agent_teams = cfg["force_inactive_agent_teams"]

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
                include_scope_discipline=False,
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
            child_outcome_log_dir=child_outcome_log_dir,
        )
        extras.update(_claude_host_attestation_env(None))
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
        if isinstance(mcp_tool_timeout_sec, (int, float)) and mcp_tool_timeout_sec > 0:
            extras[CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR] = str(mcp_tool_timeout_sec)
        extras["AUTOSKILLIT_SKILL_NAME"] = extract_skill_name(skill_command) or ""
        if provider_extras:
            for k, v in provider_extras.items():
                if k not in _SKILL_SESSION_EXTRAS_DENYLIST:
                    extras[k] = v
        extras.update(_CLAUDE_SKILL_SESSION_HARDENING)
        if profile_name:
            extras[PROVIDER_PROFILE_ENV_VAR] = profile_name
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        spec = self.build_headless_cmd(
            prompt,
            model=model,
            env_extras=extras,
            base=filtered_base,
            required=SKILL_SESSION_REQUIRED_ENV | _CLAUDE_SKILL_SESSION_HARDENING.keys(),
            force_inactive_agent_teams=force_inactive_agent_teams,
            project_root=cwd,
        )
        cmd: list[str] = [*spec.cmd]
        if plugin_binding is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_binding.plugin_dir)]
        _apply_output_format(cmd, output_format)
        for validated_dir in add_dirs:
            cmd.extend([ClaudeFlags.ADD_DIR, validated_dir.path])
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(
            cmd=tuple(cmd),
            env=spec.env,
            cwd=cwd,
            is_resume=bool(resume_session_id),
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            force_inactive_agent_teams=force_inactive_agent_teams,
        )

    def build_food_truck_cmd(
        self,
        *,
        orchestrator_prompt: str,
        plugin_binding: PluginLaunchBinding | None,
        cwd: str,
        completion_marker: str,
        resume_session_id: str | None = None,
        resume_checkpoint: SessionCheckpoint | None = None,
        model: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        output_format: OutputFormat = OutputFormat.STREAM_JSON,
        exit_after_stop_delay_ms: int = 0,
        stream_idle_timeout_ms: int = 0,
        mcp_tool_timeout_sec: float | None = None,
        scenario_step_name: str = "",
        temp_dir_relpath: str | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        sentinel_contract: str = "",
        resume_message: str | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
        managed_skill_catalog: ValidatedAddDir | None = None,
    ) -> CmdSpec:
        del (
            native_shell_capture_decision,
            managed_lineage_ref,
            managed_attempt_id,
            managed_skill_catalog,
        )
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
                include_scope_discipline=False,
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
        extras.update(_claude_host_attestation_env(None))
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if exit_after_stop_delay_ms > 0:
            extras["CLAUDE_CODE_EXIT_AFTER_STOP_DELAY"] = str(exit_after_stop_delay_ms)
        if stream_idle_timeout_ms > 0:
            extras["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = str(stream_idle_timeout_ms)
        if (
            mcp_tool_timeout_sec is not None
            and isinstance(mcp_tool_timeout_sec, (int, float))
            and mcp_tool_timeout_sec > 0
        ):
            extras[CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR] = str(mcp_tool_timeout_sec)
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
            force_inactive_agent_teams=force_inactive_agent_teams,
            project_root=project_root,
        )

        cmd: list[str] = [*spec.cmd]
        if plugin_binding is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_binding.plugin_dir)]
        _apply_output_format(cmd, output_format)
        cmd += [ClaudeFlags.TOOLS, "AskUserQuestion"]
        if resume_session_id:
            cmd += [ClaudeFlags.RESUME, resume_session_id]

        return CmdSpec(
            cmd=tuple(cmd),
            env=spec.env,
            is_resume=bool(resume_session_id),
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            force_inactive_agent_teams=force_inactive_agent_teams,
        )
