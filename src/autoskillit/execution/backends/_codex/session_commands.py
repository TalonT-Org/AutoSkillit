"""Codex skill, orchestrator, interactive, resume, and setup command construction."""

from __future__ import annotations

import os
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import assert_never, cast

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT_ENV_VAR,
    AUTOSKILLIT_INSTALLED_VERSION,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    CODEX_INTERACTIVE_REQUIRED_ENV,
    CODEX_RESERVED_HOME_ENV_VARS,
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    RESUME_SESSION_BASELINE_KEYS,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    AgentDef,
    BackendCapabilities,
    CmdSpec,
    CodexAppServerPlan,
    ExecutableLaunchBinding,
    FreshLaunch,
    InteractiveLaunch,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    OutputFormat,
    PluginLaunchBinding,
    PluginLoadMode,
    PositionalRole,
    RestoreSession,
    ResumeWithBriefing,
    SessionCheckpoint,
    SkillDiscoveryRouteDef,
    SkillExecutionRole,
    SkillSessionConfig,
    ValidatedAddDir,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
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
from autoskillit.execution.backends._codex.session_home import (
    _canonical_generated_home,
    _configure_interactive_home,
    _generated_home_config_overrides,
)
from autoskillit.execution.backends._codex.session_setup import setup_codex_session_dir
from autoskillit.execution.backends._codex_cmd_builders import (
    _IMAGE_GENERATION_DISABLED,
    CodexEnvPolicy,
    CodexFlags,
    _codex_app_server_base,
    _codex_exec_extras,
    _should_bypass_hook_trust,
)
from autoskillit.execution.backends._codex_config import _format_toml_value
from autoskillit.execution.backends._codex_discovery import (
    CODEX_APP_SERVER_ROUTE,
    select_interactive_discovery_route,
)

logger = get_logger(__name__)


class CodexCommandMixin(BackendCmdBuilderBase):
    source_codex_home: Path | None

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Return the concrete Codex backend capabilities."""

    @abstractmethod
    def translate_model(self, model: str) -> str:
        """Resolve a configured Codex model alias."""

    @abstractmethod
    def model_config_overrides(self, model: str) -> tuple[str, ...]:
        """Return model-specific Codex config overrides."""

    @abstractmethod
    def env_policy(self) -> CodexEnvPolicy:
        """Return the concrete Codex environment policy."""

    @staticmethod
    def _otlp_overrides(extras: Mapping[str, str]) -> tuple[str, ...]:
        logs_endpoint = extras.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        metrics_endpoint = extras.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
        if not logs_endpoint or not metrics_endpoint:
            return ()
        return (
            "otel.exporter="
            f'{{otlp-http={{endpoint={_format_toml_value(logs_endpoint)},protocol="json"}}}}',
            "otel.metrics_exporter="
            f'{{otlp-http={{endpoint={_format_toml_value(metrics_endpoint)},protocol="json"}}}}',
        )

    def _prepare_skill_session_environment(
        self,
        *,
        skill_command: str,
        cwd: str,
        completion_marker: str,
        add_dirs: Sequence[ValidatedAddDir],
        exit_after_stop_delay_ms: int,
        stream_idle_timeout_ms: int,
        scenario_step_name: str,
        child_outcome_log_dir: str,
        allowed_write_prefix: str,
        allowed_write_prefixes: tuple[str, ...],
        provider_extras: Mapping[str, str] | None,
        profile_name: str,
    ) -> tuple[dict[str, str], dict[str, str], ValidatedAddDir]:
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
        if len(add_dirs) != 1 or not add_dirs[0].session_home or not add_dirs[0].skill_entries:
            raise ValueError(
                "Codex app-server skill sessions require exactly one add-dir bound to a "
                "nonempty session_home with a frozen, nonempty skill catalog"
            )
        managed_catalog = add_dirs[0]
        for reserved_key in CODEX_RESERVED_HOME_ENV_VARS:
            extras[reserved_key] = managed_catalog.session_home
        if exit_after_stop_delay_ms:
            extras.setdefault(
                AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT_ENV_VAR, str(exit_after_stop_delay_ms / 1000)
            )
        if stream_idle_timeout_ms:
            extras.setdefault(
                AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT_ENV_VAR, str(stream_idle_timeout_ms / 1000)
            )
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=SKILL_SESSION_REQUIRED_ENV
            | {MCP_CLIENT_BACKEND_ENV_VAR}
            | CODEX_RESERVED_HOME_ENV_VARS,
        )
        return env, extras, managed_catalog

    @staticmethod
    def _prepare_interactive_environment(
        *,
        generated_home: Path | None,
        plugin_binding: PluginLaunchBinding | None,
        env_extras: Mapping[str, str] | None,
        required_env: frozenset[str] | None,
    ) -> tuple[dict[str, str], tuple[tuple[str, str], ...], SkillDiscoveryRouteDef | None]:
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
        route = select_interactive_discovery_route(
            generated_home=generated_home,
            plugin_binding=plugin_binding,
        )
        projected_skill_entries = _configure_interactive_home(
            route=route,
            generated_home=generated_home,
            plugin_binding=plugin_binding,
            base_env=base_env,
            merged_extras=merged_extras,
        )
        effective_required = CODEX_INTERACTIVE_REQUIRED_ENV | (required_env or frozenset())
        if generated_home is not None:
            effective_required |= CODEX_RESERVED_HOME_ENV_VARS
        env = CodexEnvPolicy().build_env(
            base_env, extras=merged_extras, required=effective_required
        )
        # build_env strips this key, so inject it after the call like other builders.
        env.update({NATIVE_SHELL_CAPTURE_MODE_ENV_VAR: NativeShellCaptureMode.CAPTURE.value})
        return env, projected_skill_entries, route

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
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        exit_after_stop_delay_ms: int = 0,
        stream_idle_timeout_ms: int = 0,
        project_root: Path | str | None = None,
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
            child_outcome_log_dir = cfg["child_outcome_log_dir"]
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

        env, extras, managed_catalog = self._prepare_skill_session_environment(
            skill_command=skill_command,
            cwd=cwd,
            completion_marker=completion_marker,
            add_dirs=add_dirs,
            exit_after_stop_delay_ms=exit_after_stop_delay_ms,
            stream_idle_timeout_ms=stream_idle_timeout_ms,
            scenario_step_name=scenario_step_name,
            child_outcome_log_dir=child_outcome_log_dir,
            allowed_write_prefix=allowed_write_prefix,
            allowed_write_prefixes=allowed_write_prefixes,
            provider_extras=provider_extras,
            profile_name=profile_name,
        )
        session_home = str(
            _canonical_generated_home(
                managed_catalog.session_home,
                argument_name="managed catalog session_home",
            )
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )

        cmd = _codex_app_server_base(extra_overrides=self._otlp_overrides(extras))
        bypass_hook_trust = _should_bypass_hook_trust(
            self.capabilities.hook_trust_policy, automated_session=True
        )
        config_overrides: dict[str, object] = {
            "bypass_hook_trust": bypass_hook_trust,
            **_generated_home_config_overrides(Path(session_home)),
        }
        if model:
            for override in self.model_config_overrides(model):
                key, _, value = override.partition("=")
                config_overrides[key] = value
        if network_access:
            config_overrides["sandbox_workspace_write.network_access"] = True
        catalog_root = str(CODEX_APP_SERVER_ROUTE.catalog_dir(Path(session_home)))
        app_server_plan = CodexAppServerPlan(
            session_home=session_home,
            catalog_root=catalog_root,
            expected_skill_names=frozenset(name for name, _ in managed_catalog.skill_entries),
            expected_skill_entries=managed_catalog.skill_entries,
            cwd=cwd,
            prompt=prompt,
            model=self.translate_model(model) if model else None,
            sandbox=sandbox_mode,
            approval_policy="never",
            bypass_hook_trust=bypass_hook_trust,
            developer_instructions=None,
            config_overrides=config_overrides,
            client_version=AUTOSKILLIT_INSTALLED_VERSION,
            resume_thread_id=resume_session_id,
        )

        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=cwd,
            is_resume=bool(resume_session_id),
            process_idle_timeout_ms=stream_idle_timeout_ms,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            managed_skill_catalog=managed_catalog,
            app_server_plan=app_server_plan,
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
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        sentinel_contract: str = "",
        resume_message: str | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        project_root: Path | str | None = None,
        managed_attempt_id: str | None = None,
        managed_skill_catalog: ValidatedAddDir | None = None,
    ) -> CmdSpec:
        # Codex uses ensure_codex_mcp_registered and CODEX_MCP_TOOL_TIMEOUT_FLOOR;
        # this shared-Protocol parameter is intentionally ignored.
        del mcp_tool_timeout_sec
        if (
            managed_skill_catalog is None
            or not managed_skill_catalog.session_home
            or not managed_skill_catalog.skill_entries
        ):
            raise ValueError(
                "Codex app-server food-truck launches require a managed catalog bound to a "
                "nonempty session_home with a frozen, nonempty skill catalog"
            )
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
        session_home = str(
            _canonical_generated_home(
                managed_skill_catalog.session_home,
                argument_name="managed catalog session_home",
            )
        )
        for reserved_key in CODEX_RESERVED_HOME_ENV_VARS:
            extras[reserved_key] = session_home
        if exit_after_stop_delay_ms:
            extras.setdefault(
                AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT_ENV_VAR, str(exit_after_stop_delay_ms / 1000)
            )
        if stream_idle_timeout_ms:
            extras.setdefault(
                AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT_ENV_VAR, str(stream_idle_timeout_ms / 1000)
            )
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=ORCHESTRATOR_SESSION_REQUIRED_ENV
            | {MCP_CLIENT_BACKEND_ENV_VAR}
            | CODEX_RESERVED_HOME_ENV_VARS,
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )

        cmd = _codex_app_server_base(extra_overrides=self._otlp_overrides(extras))
        bypass_hook_trust = _should_bypass_hook_trust(
            self.capabilities.hook_trust_policy, automated_session=True
        )
        config_overrides: dict[str, object] = {
            "bypass_hook_trust": bypass_hook_trust,
            "web_search": "disabled",
            **_generated_home_config_overrides(Path(session_home)),
        }
        if model:
            for override in self.model_config_overrides(model):
                key, _, value = override.partition("=")
                config_overrides[key] = value
        catalog_root = str(CODEX_APP_SERVER_ROUTE.catalog_dir(Path(session_home)))
        app_server_plan = CodexAppServerPlan(
            session_home=session_home,
            catalog_root=catalog_root,
            expected_skill_names=frozenset(
                name for name, _ in managed_skill_catalog.skill_entries
            ),
            expected_skill_entries=managed_skill_catalog.skill_entries,
            cwd=cwd,
            prompt=prompt,
            model=self.translate_model(model) if model else None,
            sandbox="read-only",
            approval_policy="never",
            bypass_hook_trust=bypass_hook_trust,
            developer_instructions=None,
            config_overrides=config_overrides,
            client_version=AUTOSKILLIT_INSTALLED_VERSION,
            resume_thread_id=resume_session_id or "",
        )

        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=cwd,
            is_resume=bool(resume_session_id),
            process_idle_timeout_ms=stream_idle_timeout_ms,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            managed_skill_catalog=managed_skill_catalog,
            app_server_plan=app_server_plan,
            force_inactive_agent_teams=force_inactive_agent_teams,
        )

    def build_interactive_cmd(
        self,
        *,
        launch: InteractiveLaunch = FreshLaunch(),
        model: str | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_binding: PluginLaunchBinding | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        generated_home: Path | None = None,
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
        tools: Sequence[str] = (),
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        project_root: Path | str | None = None,
        mcp_tool_timeout_sec: float | None = None,
    ) -> CmdSpec:
        # Codex uses ensure_codex_mcp_registered and CODEX_MCP_TOOL_TIMEOUT_FLOOR;
        # this shared-Protocol parameter is intentionally ignored.
        del mcp_tool_timeout_sec
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
        config_overrides: list[str] = []
        match launch:
            case FreshLaunch(system_prompt=system_prompt, initial_prompt=initial_prompt):
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
            case RestoreSession(session_id=sid):
                builder.mode_flag(CodexFlags.RESUME_SUBCOMMAND)
                builder.positional(sid, role=PositionalRole.RESUME_TARGET)
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
                initial_prompt = None
            case ResumeWithBriefing(session_id=sid, briefing=briefing):
                builder.mode_flag(CodexFlags.RESUME_SUBCOMMAND)
                builder.positional(sid, role=PositionalRole.RESUME_TARGET)
                builder.mode_flag(CodexFlags.DANGEROUSLY_BYPASS)
                initial_prompt = briefing
            case _ as unreachable:
                assert_never(unreachable)
        if model:
            builder.kv_flag(CodexFlags.MODEL, self.translate_model(model))
            config_overrides.extend(self.model_config_overrides(model))
        config_overrides.append(_IMAGE_GENERATION_DISABLED)
        if isinstance(launch, FreshLaunch):
            # Interactive TUI tasks are unknown at launch (including manual runs), so
            # they retain full scope coverage without a dispatch-time skill identity
            # that could select narrower skill-session delivery.
            _interactive_suffix = codex_discipline_suffix(include_scope=True)
            developer_instructions = (
                f"{system_prompt}\n\n{_interactive_suffix}"
                if system_prompt is not None
                else _interactive_suffix
            )
            config_overrides.append(
                f"developer_instructions={_format_toml_value(developer_instructions)}",
            )
        if generated_home is None and (
            plugin_binding is None or plugin_binding.load_mode is not PluginLoadMode.PROJECTED_HOME
        ):
            raise ValueError("generated_home is required for Codex interactive launches")
        if generated_home is not None:
            generated_home = _canonical_generated_home(
                generated_home,
                argument_name="generated_home",
            )
            config_overrides.append(
                f"sqlite_home={_format_toml_value(str(generated_home))}",
            )
        if initial_prompt is not None:
            builder.positional(initial_prompt, role=PositionalRole.PROMPT)
        for override in config_overrides:
            builder.variadic_pair(CodexFlags.CONFIG_OVERRIDE, override)
        for d in add_dirs:
            builder.variadic_pair(CodexFlags.ADD_DIR, str(d))
        env, projected_skill_entries, route = self._prepare_interactive_environment(
            generated_home=generated_home,
            plugin_binding=plugin_binding,
            env_extras=env_extras,
            required_env=required_env,
        )
        if executable is not None and dict(env) != dict(executable.launch_environment):
            raise ValueError("interactive environment changed after executable binding")
        partial = builder.build()
        managed_skill_catalog = next(
            (entry for entry in add_dirs if isinstance(entry, ValidatedAddDir)),
            None,
        )
        return CmdSpec(
            cmd=partial.cmd,
            env=executable.launch_environment if executable is not None else env,
            origin=partial.origin,
            is_resume=not isinstance(launch, FreshLaunch),
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            managed_skill_catalog=managed_skill_catalog,
            projected_skill_entries=projected_skill_entries,
            skill_discovery_route=route,
            force_inactive_agent_teams=force_inactive_agent_teams,
        )

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_binding: PluginLaunchBinding | None = None,
        session_home: str | None = None,
        managed_skill_catalog: ValidatedAddDir | None = None,
        env_extras: Mapping[str, str] | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
        include_scope_discipline: bool = False,
        skill_session: bool = False,
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        project_root: Path | str | None = None,
        mcp_tool_timeout_sec: float | None = None,
    ) -> CmdSpec:
        # Codex uses ensure_codex_mcp_registered and CODEX_MCP_TOOL_TIMEOUT_FLOOR;
        # this shared-Protocol parameter is intentionally ignored.
        del skill_session, mcp_tool_timeout_sec
        if not resume_session_id.strip():
            msg = "resume_session_id must be a non-empty string"
            raise ValueError(msg)
        if output_format != OutputFormat.JSON:
            logger.warning("codex_output_format_coerced")
        if managed_skill_catalog is not None:
            if not managed_skill_catalog.session_home or not managed_skill_catalog.skill_entries:
                raise ValueError(
                    "Codex app-server managed resume requires a catalog bound to a nonempty "
                    "session_home with a frozen, nonempty skill catalog"
                )
            if session_home is not None and session_home != managed_skill_catalog.session_home:
                raise ValueError(
                    "Codex app-server managed resume home does not agree with its bound catalog"
                )
            session_home = managed_skill_catalog.session_home

        if session_home is None:
            raise ValueError("session_home is required for Codex resume launches")
        session_home = str(_canonical_generated_home(session_home, argument_name="session_home"))

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        resume_extras = _codex_exec_extras(
            session_type="", include_session_baseline=True, include_agent_backend_flat=True
        )
        _merge_caller_env_extras(resume_extras, env_extras)
        for reserved_key in CODEX_RESERVED_HOME_ENV_VARS:
            resume_extras[reserved_key] = session_home
        env = self.env_policy().build_env(
            filtered_base,
            extras=resume_extras,
            required=(
                RESUME_SESSION_BASELINE_KEYS
                | {MCP_CLIENT_BACKEND_ENV_VAR}
                | CODEX_RESERVED_HOME_ENV_VARS
            ),
        )
        env.update(
            _managed_native_shell_env(
                decision=native_shell_capture_decision,
                lineage_ref=managed_lineage_ref,
                attempt_id=managed_attempt_id,
            )
        )

        cmd = _codex_app_server_base(extra_overrides=self._otlp_overrides(resume_extras))
        bypass_hook_trust = _should_bypass_hook_trust(
            self.capabilities.hook_trust_policy, automated_session=True
        )
        if managed_skill_catalog is not None:
            session_home = cast(str, session_home)
            catalog_root = str(CODEX_APP_SERVER_ROUTE.catalog_dir(Path(session_home)))
            expected_entries = managed_skill_catalog.skill_entries
        else:
            catalog_root = ""
            expected_entries = ()
        # Mirrors build_skill_session_cmd's own prompt composition: discipline
        # digests are prepended into the prompt itself, not carried on a
        # separate developer_instructions channel.
        composed_prompt = (
            f"{codex_discipline_suffix(include_scope=include_scope_discipline)}\n\n{prompt}"
        )
        app_server_plan = CodexAppServerPlan(
            session_home=session_home,
            catalog_root=catalog_root,
            expected_skill_names=frozenset(name for name, _ in expected_entries),
            expected_skill_entries=expected_entries,
            cwd=str(project_root) if project_root is not None else "",
            prompt=composed_prompt,
            model=None,
            sandbox="read-only",
            approval_policy="never",
            bypass_hook_trust=bypass_hook_trust,
            developer_instructions=None,
            config_overrides={
                "bypass_hook_trust": bypass_hook_trust,
                **_generated_home_config_overrides(Path(session_home)),
            },
            client_version=AUTOSKILLIT_INSTALLED_VERSION,
            resume_thread_id=resume_session_id,
        )

        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            cwd=str(project_root) if project_root is not None else "",
            is_resume=True,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            managed_skill_catalog=managed_skill_catalog,
            app_server_plan=app_server_plan,
            force_inactive_agent_teams=force_inactive_agent_teams,
        )

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
        return setup_codex_session_dir(
            self.source_codex_home,
            session_dir,
            parent_sandbox_mode=parent_sandbox_mode,
            agent_defs=agent_defs,
            explorer_binding_env=explorer_binding_env,
            execution_role=execution_role,
        )
