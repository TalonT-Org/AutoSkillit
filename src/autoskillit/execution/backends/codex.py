"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    BUNDLED_EXPLORER_ROLES,
    CODEX_COOK_RESERVED_ENV_VARS,
    CODEX_EFFORT_MAPPING,
    CODEX_INTERACTIVE_REQUIRED_ENV,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MODEL_ALIASES,
    CODEX_SESSIONS_SUBDIR,
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    RESUME_SESSION_BASELINE_KEYS,
    SESSION_ADD_DIR_SUBDIR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    AgentDef,
    BackendCapabilities,
    BackendConventions,
    BareResume,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    CmdSpec,
    CookSessionHandle,
    ExecutableLaunchBinding,
    ExecutionIdentity,
    ExplorationDispatchRenderer,
    HookTrustPolicy,
    ManagedHeadlessSessionLineageRef,
    NamedResume,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NoResume,
    OutputFormat,
    PluginLaunchBinding,
    PreLaunchReadiness,
    ResumeSpec,
    SessionCheckpoint,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticPlan,
    SkillSessionConfig,
    ValidatedAddDir,
    atomic_write,
    default_log_dir,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.backends import _codex_config as _codex_cfg
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
    FlagVocabulary,
    _managed_native_shell_env,
    _merge_caller_env_extras,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _SKILL_SESSION_EXTRAS_DENYLIST,
    PromptBuildContext,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    apply_prompt_injector_chain,
    codex_discipline_suffix,
)
from autoskillit.execution.backends._cmd_builder import CmdBuilder
from autoskillit.execution.backends._codex.explorer_projection import (
    _canonical_explorer_mcp_transport,
    _render_parent_explorer_config,
    _validate_injected_explorer_parent_policy,
    _validated_explorer_binding_envs,
)
from autoskillit.execution.backends._codex_cmd_builders import (
    _IMAGE_GENERATION_DISABLED,
    CODEX_ENV_PREFIX_DENYLIST,
    CODEX_EXEC_FLAGS,
    CODEX_TOP_LEVEL_ONLY_FLAGS,
    NON_VARIADIC_CODEX_FLAGS,
    VARIADIC_CODEX_FLAGS,
    CodexEnvPolicy,
    CodexFlags,
    CodexSessionLocator,
    CodexStateReadinessProbe,
    _codex_exec_base,
    _codex_exec_extras,
    _should_bypass_hook_trust,
)
from autoskillit.execution.backends._codex_config import (
    CODEX_RECIPE_DELIVERY_BUDGET,
    CODEX_SPAWNABLE_BUILT_IN_AGENT_NAMES,
    _format_toml_value,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_execution_identity import (
    extract_codex_execution_identity,
)
from autoskillit.execution.backends._codex_explorer_projection import (
    _bundled_agent_definitions,
    _canonical_codex_model_effort,
    _generate_agent_tomls,
    _materialize_profile_skills,
    _preflight_agent_projection,
    _register_agent_tomls,
    _render_cli_auth_store,
    _render_parent_sandbox_config,
    clear_explorer_binding_env,
    refresh_explorer_binding_env,
)
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser
from autoskillit.execution.backends._codex_prelaunch import codex_prelaunch_transaction
from autoskillit.execution.backends._codex_probes import (
    _validate_global_codex_home,
    _validate_inert_rollout_paths,
    _validate_mcp_probe,
)
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore
from autoskillit.execution.backends._explorer_dispatch import (
    CODEX_EXPLORATION_DISPATCH_RENDERER,
)


def _codex_home_from_plugin_binding(
    plugin_binding: PluginLaunchBinding | None,
) -> str | None:
    if plugin_binding is None:
        return None
    return str(plugin_binding.plugin_dir)


_CODEX_HOME_ENV_VAR = "CODEX_HOME"
_CODEX_SQLITE_HOME_ENV_VAR = "CODEX_SQLITE_HOME"


__all__ = [
    "CODEX_EXEC_FLAGS",
    "CODEX_SPAWNABLE_BUILT_IN_AGENT_NAMES",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexSessionLocator",
    "CodexStateReadinessProbe",
    "NON_VARIADIC_CODEX_FLAGS",
    "clear_explorer_binding_env",
    "refresh_explorer_binding_env",
    "VARIADIC_CODEX_FLAGS",
    "ensure_codex_mcp_registered",
]

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CodexBackend(BackendCmdBuilderBase):
    source_codex_home: Path | None = None

    def __post_init__(self) -> None:
        source_home = (
            Path.home() / ".codex"
            if self.source_codex_home is None
            else Path(self.source_codex_home)
        )
        object.__setattr__(
            self,
            "source_codex_home",
            source_home.expanduser().resolve(strict=False),
        )

    def _binary(self) -> str:
        return "codex"

    def _sandbox_default(self) -> str:
        return "workspace-write"

    def _env_policy(self) -> CodexEnvPolicy:
        return CodexEnvPolicy()

    def _flag_vocabulary(self) -> FlagVocabulary:
        return FlagVocabulary(
            variadic_flags=VARIADIC_CODEX_FLAGS,
            non_variadic_flags=NON_VARIADIC_CODEX_FLAGS,
            model_flag=CodexFlags.MODEL,
            add_dir_flag=CodexFlags.ADD_DIR,
            resume_flag=CodexFlags.RESUME_SUBCOMMAND,
            config_override_flag=CodexFlags.CONFIG_OVERRIDE,
        )

    @property
    def name(self) -> str:
        return AGENT_BACKEND_CODEX

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            channel_b_capable=False,
            supports_task_lifecycle_events=False,
            pty_required=False,
            session_resume_capable=True,
            skill_injection_capable=True,
            supports_thinking_blocks=False,
            supports_claude_format_stdout=False,
            exit_code_is_terminal=True,
            mcp_config_capable=True,
            food_truck_capable=True,
            completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
            session_record_types=frozenset({"item.completed"}),
            triage_capable=False,
            supports_context_exhaustion_detection=False,
            supports_model_capacity_error_detection=True,
            supports_tool_list_changed=False,
            required_skill_fields=frozenset({"name", "description"}),
            required_session_files=frozenset({"config.toml"}),
            session_dir_symlinks=frozenset({"sessions", "archived_sessions"}),
            applicable_guards=frozenset({"write_guard"}),  # run_cmd, not Write/Edit
            write_guard_tool_names=frozenset({"apply_patch", "Bash", "run_cmd"}),
            env_denylist_prefixes=CODEX_ENV_PREFIX_DENYLIST,
            min_version="0.130.0",
            version_check_command="codex --version",
            process_name="codex",
            process_name_aliases=frozenset({"codex", "node"}),
            skills_subdir="skills",
            hook_config_format="toml_nested",
            write_detection_strategy="file_changes",
            patch_format="codex_star_update",
            default_skill_sandbox_mode="workspace-write",
            mcp_env_forward_vars=CODEX_MCP_ENV_FORWARD_VARS,
            replay_capable=True,
            record_capable=False,
            anthropic_provider_capable=False,
            plugin_install_capable=False,
            claude_marketplace_tool_prefix_capable=False,
            inspector_capable=False,
            supports_context_window_suffix=False,
            has_unguarded_filesystem_access=True,
            github_api_callable=False,
            skill_sigil="$",
            session_dir_persistent=True,
            cook_startup_observer_capable=True,
            explicit_path_env_var="",
            cook_exact_binding_probe_required=False,
            supports_model_invocation_gating=False,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            unnegotiated_tool_result_token_limit=(
                CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
            ),
            protected_recipe_delivery_capable=False,
            recipe_delivery_budget=CODEX_RECIPE_DELIVERY_BUDGET,
            hook_trust_policy=HookTrustPolicy.REVIEW_EACH_SESSION,
        )

    @property
    def conventions(self) -> BackendConventions:
        return BackendConventions(
            skills_subdir=ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR,
            project_local_skill_search_dirs=(".codex/skills", ".agents/skills"),
            persistent_session_root_subdir=Path(CODEX_SESSIONS_SUBDIR),
            skill_sigil=self.capabilities.skill_sigil,
        )

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer:
        return CODEX_EXPLORATION_DISPATCH_RENDERER

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return CmdSpec(
            cmd=spec.cmd,
            env=spec.env,
            cwd=cwd,
            inherited_fds=spec.inherited_fds,
        )

    def stream_parser(self, completion_marker: str = "") -> CodexStreamParser:
        return CodexStreamParser(completion_marker=completion_marker)

    def result_parser(self) -> CodexResultParser:
        return CodexResultParser()

    def env_policy(self) -> CodexEnvPolicy:
        return CodexEnvPolicy(denylist_prefixes=self.capabilities.env_denylist_prefixes)

    def session_locator(self) -> CodexSessionLocator:
        return CodexSessionLocator(
            store_root=default_log_dir(),
        )

    def resolve_effective_execution_identity(
        self,
        *,
        requested: ExecutionIdentity,
        session_id: str,
    ) -> ExecutionIdentity:
        """Resolve effective parent and child identity from Codex rollout records."""
        if not requested.children or not session_id:
            return requested
        locator = self.session_locator()
        parent_rollout = locator.locate_session(session_id)
        if parent_rollout is None:
            return requested
        return extract_codex_execution_identity(
            parent_rollout,
            requested=requested,
            child_rollout_resolver=locator.locate_session,
        )

    def write_tool_names(self) -> frozenset[str]:
        return frozenset({"file_change"})

    def binary_name(self) -> str:
        return "codex"

    def translate_model(self, model: str) -> str:
        from autoskillit.core import (
            strip_context_window_suffix,
        )

        base = strip_context_window_suffix(model)
        return CODEX_MODEL_ALIASES.get(base, base)

    def model_config_overrides(self, model: str) -> tuple[str, ...]:
        from autoskillit.core import strip_context_window_suffix

        base = strip_context_window_suffix(model)
        effort = CODEX_EFFORT_MAPPING.get(base)
        if effort:
            return (f"model_reasoning_effort={effort}",)
        return ()

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
        cmd = _codex_exec_base(sandbox="workspace-write")
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, d]
        cmd.append(prompt)
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        headless_extras = _codex_exec_extras(session_type="")
        _merge_caller_env_extras(headless_extras, env_extras)
        env = self.env_policy().build_env(filtered_base, extras=headless_extras)
        return CmdSpec(cmd=tuple(cmd), env=env)

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
        scenario_step_name: str = "",
        temp_dir_relpath: str | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        provider_extras: Mapping[str, str] | None = None,
        profile_name: str = "",
        resume_session_id: str = "",
        resume_checkpoint: SessionCheckpoint | None = None,
        resume_message: str | None = None,
        sandbox_mode: str = "workspace-write",
        network_access: bool = False,
        include_scope_discipline: bool = False,
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
            scenario_step_name = cfg["scenario_step_name"]
            temp_dir_relpath = cfg["temp_dir_relpath"]
            allowed_write_prefix = cfg["allowed_write_prefix"]
            allowed_write_prefixes = cfg["allowed_write_prefixes"]
            provider_extras = cfg["provider_extras"]
            profile_name = cfg["profile_name"]
            resume_session_id = cfg["resume_session_id"]
            resume_checkpoint = cfg["resume_checkpoint"]
            resume_message = cfg["resume_message"]
            sandbox_mode = cfg["sandbox_mode"]
            network_access = cfg.get("network_access", False)
            include_scope_discipline = cfg["include_scope_discipline"]
            native_shell_capture_decision = cfg["native_shell_capture_decision"]
            managed_lineage_ref = cfg["managed_lineage_ref"]
            managed_attempt_id = cfg["managed_attempt_id"]
        else:
            native_shell_capture_decision = None
            managed_lineage_ref = None
            managed_attempt_id = None
        projected_codex_home = _codex_home_from_plugin_binding(plugin_binding)
        if output_format != OutputFormat.JSON:
            logger.warning("codex_output_format_coerced")
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
                include_output_discipline=True,
                include_intake_discipline=True,
                include_scope_discipline=include_scope_discipline,
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
        extras["AUTOSKILLIT_HEADLESS_AUTO_GATE"] = "1"
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[FLEET_INSPECTOR_MODEL_ENV_VAR] = ""
        extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ""
        extras.setdefault(LAUNCH_ID_ENV_VAR, "")
        extras.setdefault(AUTOSKILLIT_STATE_ROOT_ENV_VAR, cwd)
        extras["AUTOSKILLIT_SKILL_NAME"] = extract_skill_name(skill_command) or ""
        _merge_caller_env_extras(
            extras,
            provider_extras,
            denylist=_SKILL_SESSION_EXTRAS_DENYLIST,
        )
        if profile_name:
            extras[PROVIDER_PROFILE_ENV_VAR] = profile_name
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker
        if add_dirs:
            extras["CODEX_HOME"] = add_dirs[0].path
        elif projected_codex_home is not None:
            extras["CODEX_HOME"] = projected_codex_home
        if exit_after_stop_delay_ms:
            extras.setdefault(
                "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(exit_after_stop_delay_ms / 1000)
            )
        if stream_idle_timeout_ms:
            extras.setdefault(
                "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(stream_idle_timeout_ms / 1000)
            )
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=SKILL_SESSION_REQUIRED_ENV | {MCP_CLIENT_BACKEND_ENV_VAR},
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )

        _net_overrides: list[str] = []
        if network_access:
            _net_overrides.append("sandbox_workspace_write.network_access=true")
        cmd = _codex_exec_base(
            sandbox=sandbox_mode if sandbox_mode == "read-only" else None,
            bypass_hook_trust=_should_bypass_hook_trust(
                self.capabilities.hook_trust_policy,
                automated_session=True,
            ),
            extra_overrides=_net_overrides,
        )
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=cwd,
            is_resume=bool(resume_session_id),
            process_idle_timeout_ms=stream_idle_timeout_ms,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
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
        scenario_step_name: str = "",
        temp_dir_relpath: str | None = None,
        allowed_write_prefix: str = "",
        allowed_write_prefixes: tuple[str, ...] = (),
        sentinel_contract: str = "",
        resume_message: str | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
    ) -> CmdSpec:
        projected_codex_home = _codex_home_from_plugin_binding(plugin_binding)
        if output_format != OutputFormat.STREAM_JSON:
            logger.warning("codex_output_format_coerced")

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
                include_output_discipline=True,
                include_intake_discipline=True,
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
        extras["AUTOSKILLIT_HEADLESS_AUTO_GATE"] = "1"
        extras[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        extras[FLEET_INSPECTOR_MODEL_ENV_VAR] = ""
        extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ""
        extras.setdefault(LAUNCH_ID_ENV_VAR, "")
        extras.setdefault(AUTOSKILLIT_STATE_ROOT_ENV_VAR, cwd)
        if completion_marker:
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker
        _merge_caller_env_extras(
            extras,
            env_extras,
            denylist=_PROVIDER_EXTRAS_BASE_DENYLIST,
        )
        if projected_codex_home is not None:
            extras["CODEX_HOME"] = projected_codex_home
        if exit_after_stop_delay_ms:
            extras.setdefault(
                "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(exit_after_stop_delay_ms / 1000)
            )
        if stream_idle_timeout_ms:
            extras.setdefault(
                "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT", str(stream_idle_timeout_ms / 1000)
            )
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=ORCHESTRATOR_SESSION_REQUIRED_ENV | {MCP_CLIENT_BACKEND_ENV_VAR},
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )

        cmd = _codex_exec_base(
            sandbox="read-only",
            extra_overrides=["web_search=disabled"],
            bypass_hook_trust=_should_bypass_hook_trust(
                self.capabilities.hook_trust_policy,
                automated_session=True,
            ),
        )
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=cwd,
            is_resume=bool(resume_session_id),
            process_idle_timeout_ms=stream_idle_timeout_ms,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
        )

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_binding: PluginLaunchBinding | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        generated_home: Path | None = None,
        resume_spec: ResumeSpec = NoResume(),
        system_prompt: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
        tools: Sequence[str] = (),
    ) -> CmdSpec:
        if tools:
            logger.warning(
                "codex_tools_ignored",
                extra={"tools": list(tools)},
            )
        builder = CmdBuilder(str(executable.path) if executable is not None else "codex")
        if _should_bypass_hook_trust(
            self.capabilities.hook_trust_policy,
            automated_session=False,
        ):
            builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS_HOOK_TRUST)
        selected_profile = (env_extras or {}).get(PROVIDER_PROFILE_ENV_VAR, "")
        if selected_profile:
            builder.kv_flag(CodexFlags.PROFILE, selected_profile)
        match resume_spec:
            case NoResume():
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
            case NamedResume(session_id=sid):
                builder.mode_flag(CodexFlags.RESUME_SUBCOMMAND)
                builder.positional(sid)
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
            case BareResume():
                builder.mode_flag(CodexFlags.RESUME_SUBCOMMAND)
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
        if model:
            builder.kv_flag(CodexFlags.MODEL, self.translate_model(model))
            for override in self.model_config_overrides(model):
                builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, override)
        builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, _IMAGE_GENERATION_DISABLED)
        if isinstance(resume_spec, NoResume):
            # Interactive TUI tasks are unknown at launch (including manual runs), so
            # they retain full scope coverage without a dispatch-time skill identity
            # that could select narrower skill-session delivery.
            _interactive_suffix = codex_discipline_suffix(include_scope=True)
            developer_instructions = (
                f"{system_prompt}\n\n{_interactive_suffix}"
                if system_prompt is not None
                else _interactive_suffix
            )
            builder.kv_flag(
                CodexFlags.CONFIG_OVERRIDE,
                f"developer_instructions={_format_toml_value(developer_instructions)}",
            )
        if generated_home is not None:
            supplied_home = Path(generated_home)
            if not supplied_home.is_absolute():
                raise ValueError("generated_home must be absolute")
            generated_home = supplied_home.expanduser().resolve(strict=False)
            if supplied_home != generated_home:
                raise ValueError("generated_home must already be canonical")
            builder.kv_flag(
                CodexFlags.CONFIG_OVERRIDE,
                f"sqlite_home={_format_toml_value(str(generated_home))}",
            )
        if initial_prompt is not None:
            builder.positional(initial_prompt)
        for d in add_dirs:
            builder.variadic_pair(CodexFlags.ADD_DIR, str(d))
        base_env = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        merged_extras: dict[str, str] = dict(SHARED_BASELINE_ENV)
        merged_extras.update(
            {
                "AUTOSKILLIT_HEADLESS": "",
                "AUTOSKILLIT_HEADLESS_AUTO_GATE": "",
                "AUTOSKILLIT_SESSION_TYPE": "",
                AGENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
                AGENT_BACKEND_DYNACONF_ENV_VAR: AGENT_BACKEND_CODEX,
                MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
                FLEET_INSPECTOR_MODEL_ENV_VAR: "",
                FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
            }
        )
        merged_extras.setdefault(LAUNCH_ID_ENV_VAR, "")
        merged_extras.setdefault(AUTOSKILLIT_STATE_ROOT_ENV_VAR, "")
        _merge_caller_env_extras(merged_extras, env_extras)
        if generated_home is not None:
            for reserved_key in CODEX_COOK_RESERVED_ENV_VARS:
                merged_extras[reserved_key] = str(generated_home)
        else:
            projected_codex_home = _codex_home_from_plugin_binding(plugin_binding)
            if projected_codex_home is not None:
                merged_extras.setdefault("CODEX_HOME", projected_codex_home)
        effective_required = CODEX_INTERACTIVE_REQUIRED_ENV | (required_env or frozenset())
        if generated_home is not None:
            effective_required |= CODEX_COOK_RESERVED_ENV_VARS
        env = CodexEnvPolicy().build_env(
            base_env, extras=merged_extras, required=effective_required
        )
        # build_env strips this key, so inject it after the call like other builders.
        env.update({NATIVE_SHELL_CAPTURE_MODE_ENV_VAR: NativeShellCaptureMode.CAPTURE.value})
        if executable is not None and dict(env) != dict(executable.launch_environment):
            raise ValueError("interactive environment changed after executable binding")
        partial = builder.build()
        return CmdSpec(
            cmd=partial.cmd,
            env=executable.launch_environment if executable is not None else env,
            origin=partial.origin,
            is_resume=isinstance(resume_spec, (NamedResume, BareResume)),
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
        )

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_binding: PluginLaunchBinding | None = None,
        env_extras: Mapping[str, str] | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
        include_scope_discipline: bool = False,
        skill_session: bool = False,
    ) -> CmdSpec:
        del skill_session
        if not resume_session_id.strip():
            msg = "resume_session_id must be a non-empty string"
            raise ValueError(msg)
        cmd = _codex_exec_base(sandbox="read-only", json=(output_format == OutputFormat.JSON))
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(
            f"{codex_discipline_suffix(include_scope=include_scope_discipline)}\n\n{prompt}"
        )
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        resume_extras = _codex_exec_extras(
            session_type="", include_session_baseline=True, include_agent_backend_flat=True
        )
        _merge_caller_env_extras(resume_extras, env_extras)
        projected_codex_home = _codex_home_from_plugin_binding(plugin_binding)
        if projected_codex_home is not None:
            resume_extras["CODEX_HOME"] = projected_codex_home
        env = self.env_policy().build_env(
            filtered_base,
            extras=resume_extras,
            required=RESUME_SESSION_BASELINE_KEYS | {MCP_CLIENT_BACKEND_ENV_VAR},
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )
        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            is_resume=True,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
        )

    def validate_session_layout(
        self,
        session_dir: Path,
        *,
        project_dir: Path | None = None,
    ) -> list[str]:
        del project_dir
        errors: list[str] = []

        skills_dir = (
            session_dir
            / SESSION_ADD_DIR_SUBDIR
            / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
        )
        if not skills_dir.is_dir():
            errors.append(f"skills directory does not exist: {skills_dir}")
        elif not any(skills_dir.iterdir()):
            errors.append(f"skills directory is empty: {skills_dir}")

        config_path = session_dir / "config.toml"
        if not config_path.is_file():
            errors.append(f"config.toml does not exist: {config_path}")
        else:
            toml_content = config_path.read_text(encoding="utf-8")
            if "[mcp_servers.autoskillit]" not in toml_content:
                errors.append("config.toml missing [mcp_servers.autoskillit] section")

        auth_path = session_dir / "auth.json"
        if auth_path.exists() and not auth_path.is_symlink():
            errors.append(f"auth.json must be a symlink, not a regular file: {auth_path}")

        sessions_path = session_dir / "sessions"
        if sessions_path.exists() and not sessions_path.is_symlink():
            errors.append(f"sessions/ must be a symlink, not a regular directory: {sessions_path}")
        archived_path = session_dir / "archived_sessions"
        if archived_path.exists() and not archived_path.is_symlink():
            errors.append(
                f"archived_sessions/ must be a symlink, not a regular directory: {archived_path}"
            )

        rollout_errors, _ = _validate_inert_rollout_paths(session_dir)
        errors.extend(rollout_errors)
        return errors

    def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
        origin = spec.origin
        if origin is None:
            return ["Codex interactive validation requires unambiguous CmdOrigin metadata"]
        reconstructed: list[str] = [origin.binary, *origin.mode_flags]
        for flag, value in origin.kv_flags:
            reconstructed.extend((flag, value))
        reconstructed.extend(origin.positional)
        for flag, value in origin.variadic_pairs:
            reconstructed.extend((flag, value))
        if tuple(reconstructed) != spec.cmd:
            return ["Codex interactive CmdOrigin does not describe the finalized command"]
        if not spec.cwd or not Path(spec.cwd).is_absolute():
            return ["Codex interactive validation requires an absolute finalized cwd"]

        home_value = spec.env.get(_CODEX_HOME_ENV_VAR)
        sqlite_value = spec.env.get(_CODEX_SQLITE_HOME_ENV_VAR)
        if not home_value or home_value != sqlite_value:
            return [
                "Codex interactive reserved home and SQLite environment must name "
                "the same generated home"
            ]
        generated_home = Path(home_value)
        if not generated_home.is_absolute():
            return ["Codex interactive generated home must be absolute"]
        generated_home = generated_home.resolve(strict=False)
        if str(generated_home) != home_value:
            return ["Codex interactive generated home environment is not canonical"]

        sqlite_override = f"sqlite_home={_format_toml_value(str(generated_home))}"
        config_overrides = [
            value for flag, value in origin.kv_flags if flag == CodexFlags.CONFIG_OVERRIDE
        ]
        if not config_overrides or config_overrides[-1] != sqlite_override:
            return [
                "Codex interactive command is missing the highest-precedence "
                "generated-home sqlite_home override"
            ]
        profiles = [value for flag, value in origin.kv_flags if flag == CodexFlags.PROFILE]
        if len(profiles) > 1:
            return ["Codex interactive command has an ambiguous selected profile"]
        selected_profile = spec.env.get(PROVIDER_PROFILE_ENV_VAR)
        if profiles != ([selected_profile] if selected_profile else []):
            return ["Codex interactive profile metadata does not match the child environment"]

        config_path = generated_home / "config.toml"
        try:
            config_bytes = config_path.read_bytes()
        except OSError as exc:
            return [
                f"Failed to read finalized generated Codex config: {type(exc).__name__}: {exc}"
            ]
        layout_errors, before_fingerprint = _validate_inert_rollout_paths(generated_home)
        if layout_errors:
            return layout_errors

        probe_command: list[str] = [origin.binary]
        for flag, value in origin.kv_flags:
            if flag in (CodexFlags.PROFILE, CodexFlags.CONFIG_OVERRIDE):
                probe_command.extend((flag, value))
        probe_command.extend(("mcp", "list", CodexFlags.JSON))
        errors = _validate_mcp_probe(
            tuple(probe_command),
            env=spec.env,
            cwd=spec.cwd,
            config_bytes=config_bytes,
        )
        after_errors, after_fingerprint = _validate_inert_rollout_paths(generated_home)
        errors.extend(after_errors)
        if not after_errors and after_fingerprint != before_fingerprint:
            errors.append("Codex MCP validation mutated the inert rollout path topology")
        return errors

    def setup_session_dir(
        self,
        session_dir: Path,
        *,
        parent_sandbox_mode: str = "workspace-write",
        agent_defs: tuple[AgentDef, ...] | None = None,
        explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
        execution_role: SkillExecutionRole = SkillExecutionRole.SESSION,
    ) -> frozenset[str]:
        assert self.source_codex_home is not None
        codex_home_source = self.source_codex_home
        config_path = session_dir / "config.toml"
        if not config_path.is_file():
            raise FileNotFoundError(f"pre-launch Codex config snapshot is missing: {config_path}")
        definitions = _bundled_agent_definitions() if agent_defs is None else agent_defs
        explorer_binding_envs = _validated_explorer_binding_envs(definitions, explorer_binding_env)
        explorer_mcp_transport = (
            _canonical_explorer_mcp_transport(config_path) if explorer_binding_envs else None
        )
        if explorer_binding_envs and parent_sandbox_mode != "read-only":
            raise ValueError("explorer shared-principal projection requires a read-only parent")
        policy_definitions = definitions if explorer_binding_envs else agent_defs
        _validate_injected_explorer_parent_policy(policy_definitions, parent_sandbox_mode)
        projected_definitions = _preflight_agent_projection(
            session_dir,
            definitions,
            exact_definitions=agent_defs is not None,
        )
        rendered_parent_config = _render_parent_sandbox_config(
            config_path.read_text(encoding="utf-8"),
            parent_sandbox_mode,
        )
        rendered_parent_config = _render_cli_auth_store(
            rendered_parent_config,
            execution_role,
        )
        if explorer_binding_envs:
            assert explorer_mcp_transport is not None
            shared_binding = next(iter(explorer_binding_envs.values()))
            rendered_parent_config = _render_parent_explorer_config(
                rendered_parent_config,
                explorer_mcp_transport=explorer_mcp_transport,
                explorer_binding_env=shared_binding,
            )
        finalized_config = tomllib.loads(rendered_parent_config)
        if (
            execution_role is SkillExecutionRole.ORCHESTRATOR
            and finalized_config.get("cli_auth_credentials_store") != "file"
        ):
            raise ValueError("finalized ORCHESTRATOR config lost the file credential store")
        atomic_write(config_path, rendered_parent_config)

        auth_source = codex_home_source / "auth.json"
        auth_dest = session_dir / "auth.json"
        auth_target = auth_source.resolve(strict=False)
        auth_dest.symlink_to(auth_target)
        logger.debug(
            "codex_auth_symlink",
            src=str(auth_target),
            dest=str(auth_dest),
        )

        env_source = codex_home_source / ".env"
        if env_source.exists():
            shutil.copy2(env_source, session_dir / ".env")

        toml_definitions = projected_definitions
        if not explorer_binding_envs and agent_defs is None:
            toml_definitions = tuple(
                d for d in projected_definitions if d.name not in BUNDLED_EXPLORER_ROLES
            )
        _generate_agent_tomls(
            session_dir,
            toml_definitions,
            explorer_binding_envs=explorer_binding_envs,
            explorer_mcp_transport=explorer_mcp_transport,
        )
        registered = _register_agent_tomls(
            session_dir,
            toml_definitions,
            explorer_binding_envs=explorer_binding_envs,
        )
        logger.debug("codex_agents_registered", count=registered)
        if execution_role is SkillExecutionRole.SESSION:
            _materialize_profile_skills(
                session_dir,
                source_codex_home=codex_home_source,
            )
        return _codex_cfg.effective_codex_agent_names(session_dir)

    def refresh_explorer_binding_env(
        self,
        session_dir: Path,
        explorer_binding_env: Mapping[str, Mapping[str, str]],
    ) -> None:
        """Refresh server-issued explorer bindings for a restored Codex session."""
        refresh_explorer_binding_env(session_dir, explorer_binding_env)

    def clear_explorer_binding_env(self, session_dir: Path, roles: frozenset[str]) -> None:
        """Scrub terminal explorer bindings from a generated Codex session."""
        clear_explorer_binding_env(session_dir, roles)

    def validate_skill_content(self, content: str) -> list[str]:
        return []

    def adapt_skill_semantics(self, plan: SkillSemanticPlan) -> SkillSemanticAdaptationResult:
        """Adapt portable skill requirements to Codex collaboration instructions."""
        role_mapping = {
            role.name: (
                role.name.removeprefix("autoskillit:")
                if role.name.startswith("autoskillit:")
                else "worker"
                if role.name == "delegated-worker"
                else role.name
            )
            for role in plan.logical_roles
        }
        sibling_targets = {sibling.name: f"${sibling.name}" for sibling in plan.sibling_skills}
        model_policy: dict[str, tuple[str, str | None]] = {}
        fragments = [
            f"Logical role {role.name!r} maps to registered Codex agent "
            f"{role_mapping[role.name]!r}: {role.purpose}."
            for role in plan.logical_roles
        ]
        for policy in plan.child_model_policies:
            native_role = role_mapping[policy.role]
            model_policy[native_role] = _canonical_codex_model_effort(
                policy.model_class,
                policy.reasoning_effort,
            )
        for spawn in plan.child_spawns:
            native_role = role_mapping[spawn.role]
            model_id, effort = model_policy.get(native_role, ("", None))
            policy_text = ""
            if model_id:
                policy_text += f", model={model_id!r}"
            if effort:
                policy_text += f", reasoning_effort={effort!r}"
            if spawn.for_each is not None:
                fragments.append(
                    "Call spawn_agent once per runtime item in "
                    f"{spawn.for_each!r} with agent_type={native_role!r}, "
                    f"fork_turns='none'{policy_text}; retain every returned child terminal "
                    "result before parent synthesis."
                )
            else:
                assert spawn.count is not None
                fragments.append(
                    f"Call spawn_agent {spawn.count} time{'s' if spawn.count != 1 else ''} "
                    f"with agent_type={native_role!r}, fork_turns='none'{policy_text}; "
                    "retain every returned child terminal result before parent synthesis."
                )
        if plan.concurrency is not None and plan.concurrency.required:
            fragments.append("Spawn all independent children before awaiting any result.")
        if plan.join is not None and plan.join.required:
            fragments.append(
                "Use wait_agent with the exact returned child IDs; deliver every independent "
                "successful child terminal result before parent synthesis."
            )
        if plan.evidence is not None and plan.evidence.required:
            boundary = "independent " if plan.evidence.independent else ""
            fragments.append(f"Require {boundary}evidence from each child result.")
        fragments.extend(f"Invoke sibling skill {target}." for target in sibling_targets.values())
        fragments.extend(
            f"Use the server-owned git metadata writer for: {write.purpose}."
            for write in plan.git_metadata_writes
        )
        result = SkillSemanticAdaptationResult(
            instruction_fragments=tuple(fragments),
            logical_role_mapping=role_mapping,
            sibling_skill_targets=sibling_targets,
            model_effort_policy=model_policy,
        )
        result.validate_for(plan, backend=self.name)
        return result

    def version(self) -> str:
        try:
            result = subprocess.run(
                [*self.version_cmd()],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip() or result.stderr.strip()
        except subprocess.TimeoutExpired:
            return ""
        except OSError:
            logger.warning("Failed to run %s --version", self.binary_name(), exc_info=True)
            return ""

    def list_plugins(self) -> list[dict[str, Any]]:
        return []

    def ensure_pre_launch(
        self,
        *,
        session_dir: Path | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_dir: Path | None = None,
    ) -> PreLaunchReadiness:
        del executable
        try:
            assert self.source_codex_home is not None
            with codex_prelaunch_transaction(
                source_codex_home=self.source_codex_home,
                hook_config_format=self.capabilities.hook_config_format,
                plugin_dir=plugin_dir,
            ) as config_path:
                if session_dir is not None:
                    snapshot = config_path.read_bytes()
                    atomic_write(Path(session_dir) / "config.toml", snapshot.decode("utf-8"))
                    return PreLaunchReadiness(())
                return PreLaunchReadiness(
                    tuple(
                        _validate_global_codex_home(
                            self.source_codex_home, config_path=config_path
                        )
                    )
                )
        except Exception as exc:
            logger.error("codex_prelaunch_transaction_failed", exc_info=True)
            return PreLaunchReadiness(
                (f"Codex pre-launch configuration failed: {type(exc).__name__}: {exc}",)
            )

    def recover_cook_history(self) -> None:
        CodexSessionStore(log_dir=default_log_dir()).recover()

    def cook_session_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> AbstractContextManager[CookSessionHandle]:
        return CodexSessionStore(log_dir=default_log_dir()).prepare_attempt(
            session_home=session_home,
            project_dir=project_dir,
            launch_id=launch_id,
            attempt=attempt,
            current_resume_spec=current_resume_spec,
        )

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        if not self.capabilities.inspector_capable:
            raise CapabilityNotSupportedError("inspector_capable", self.name)
        msg = "inspector_capable is True but build_inspector_cmd has no implementation"
        raise AssertionError(msg)
