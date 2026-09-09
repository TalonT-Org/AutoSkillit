"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    CODEX_EFFORT_MAPPING,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MODEL_ALIASES,
    CODEX_SESSIONS_SUBDIR,
    CODEX_VALID_MODEL_IDS,
    PROVIDER_PROFILE_ENV_VAR,
    SESSION_ADD_DIR_SUBDIR,
    BackendCapabilities,
    BackendConventions,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    CmdSpec,
    CookSessionHandle,
    ExecutableLaunchBinding,
    ExecutionIdentity,
    ExplorationDispatchRenderer,
    HookTrustPolicy,
    PreLaunchReadiness,
    ResumeSpec,
    SemanticAdaptationContext,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    atomic_write,
    default_log_dir,
    get_logger,
    required_join_is_unsupported,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    FlagVocabulary,
    _merge_caller_env_extras,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,
)
from autoskillit.execution.backends._codex.session_commands import (
    CodexSessionCommandMixin,
)
from autoskillit.execution.backends._codex_cmd_builders import (
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
    _canonical_codex_model_effort,
    clear_explorer_binding_env,
    refresh_explorer_binding_env,
)
from autoskillit.execution.backends._codex_managed_route import project_managed_route
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser
from autoskillit.execution.backends._codex_prelaunch import (
    _staged_error,
    codex_prelaunch_transaction,
)
from autoskillit.execution.backends._codex_probes import (
    _validate_global_codex_home,
    _validate_inert_rollout_paths,
    _validate_mcp_probe,
)
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore
from autoskillit.execution.backends._explorer_dispatch import (
    CODEX_EXPLORATION_DISPATCH_RENDERER,
)
from autoskillit.execution.process import INTERACTIVE_TETHER_CEILING_SECONDS

# Codex has its own timeout mechanism (``ensure_codex_mcp_registered`` /
# ``CODEX_MCP_TOOL_TIMEOUT_FLOOR``); ``mcp_tool_timeout_sec`` on Codex builders
# exists only to satisfy the shared Protocol and is intentionally ignored.
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


def _codex_logical_role_mapping(plan: SkillSemanticPlan) -> dict[str, str]:
    return {
        role.name: (
            role.name.removeprefix("autoskillit:")
            if role.name.startswith("autoskillit:")
            else "worker"
            if role.name == "delegated-worker"
            else role.name
        )
        for role in plan.logical_roles
    }


@dataclass(frozen=True, slots=True)
class CodexBackend(CodexSessionCommandMixin):
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
            fixed_set_join_capable=False,
            managed_fixed_batch_route_capable=True,
            native_model_ids=CODEX_VALID_MODEL_IDS,
        )

    @property
    def conventions(self) -> BackendConventions:
        source_codex_home = self.source_codex_home
        assert source_codex_home is not None
        return BackendConventions(
            skills_subdir=ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR,
            project_local_skill_search_dirs=(".codex/skills", ".agents/skills"),
            profile_skills_source=source_codex_home / "skills",
            persistent_session_root_subdir=Path(CODEX_SESSIONS_SUBDIR),
            skill_sigil=self.capabilities.skill_sigil,
        )

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer:
        return CODEX_EXPLORATION_DISPATCH_RENDERER

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return replace(spec, cwd=cwd)

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
        force_inactive_agent_teams: bool = False,  # no-op: Codex has no team concept
        env_extras: Mapping[str, str] | None = None,
        project_root: Path | str | None = None,
    ) -> CmdSpec:
        headless_extras = _codex_exec_extras(session_type="")
        _merge_caller_env_extras(headless_extras, env_extras)
        cmd = _codex_exec_base(
            sandbox="workspace-write",
            extra_overrides=self._otlp_overrides(headless_extras),
        )
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, d]
        cmd.append(prompt)
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = self.env_policy().build_env(filtered_base, extras=headless_extras)
        return CmdSpec(
            cmd=tuple(cmd), env=env, force_inactive_agent_teams=force_inactive_agent_teams
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
        discovery_skills_dir = session_dir / self.conventions.skills_subdir
        if not skills_dir.is_dir():
            errors.append(f"skills directory does not exist: {skills_dir}")
        elif not any(skills_dir.iterdir()) and not (
            discovery_skills_dir.is_dir() and any(discovery_skills_dir.iterdir())
        ):
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

    configure_managed_session_dir = project_managed_route

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

    def adapt_skill_semantics(
        self,
        plan: SkillSemanticPlan,
        adaptation_context: SemanticAdaptationContext | None = None,
    ) -> SkillSemanticAdaptationResult:
        """Adapt portable skill requirements to Codex collaboration instructions."""
        if required_join_is_unsupported(plan, self.capabilities, adaptation_context):
            return SkillSemanticAdaptationResult(
                unsupported_operation=SkillSemanticOperation.REQUIRED_JOIN,
                diagnostic=(
                    "Codex exposes wait-any/mailbox-activity semantics rather than "
                    "fixed-set fan-in. Skills declaring join.required=true cannot be "
                    "honestly realized on this backend and must be refused at admission."
                ),
            )
        role_mapping = _codex_logical_role_mapping(plan)
        managed_join = bool(plan.join and plan.join.required) and (
            adaptation_context is not None
            and adaptation_context.admits_managed_join_for(self.name)
        )
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
        for spawn in () if managed_join else plan.child_spawns:
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
        if managed_join:
            fragments.append(
                "Use the server-owned managed fixed-batch route to declare, launch, and "
                "join the complete assignment set before parent synthesis."
            )
        elif plan.concurrency is not None and plan.concurrency.required:
            fragments.append("Spawn all independent children before awaiting any result.")
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
            adaptation_context_digest=(
                adaptation_context.digest if adaptation_context and managed_join else ""
            ),
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
                    try:
                        snapshot = config_path.read_bytes()
                        atomic_write(Path(session_dir) / "config.toml", snapshot.decode("utf-8"))
                    except Exception as exc:
                        raise _staged_error("snapshot write", exc) from exc
                    return PreLaunchReadiness(())
                try:
                    errors = tuple(
                        _validate_global_codex_home(
                            self.source_codex_home, config_path=config_path
                        )
                    )
                except Exception as exc:
                    raise _staged_error("native home validation", exc) from exc
                return PreLaunchReadiness(errors)
        except Exception as exc:
            logger.error("codex_prelaunch_transaction_failed", exc_info=True)
            return PreLaunchReadiness((f"Codex pre-launch configuration failed: {exc}",))

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
        ceiling_seconds: float = INTERACTIVE_TETHER_CEILING_SECONDS,
    ) -> AbstractContextManager[CookSessionHandle]:
        return CodexSessionStore(log_dir=default_log_dir()).prepare_attempt(
            session_home=session_home,
            project_dir=project_dir,
            launch_id=launch_id,
            attempt=attempt,
            ceiling_seconds=ceiling_seconds,
            current_resume_spec=current_resume_spec,
        )

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        if not self.capabilities.inspector_capable:
            raise CapabilityNotSupportedError("inspector_capable", self.name)
        msg = "inspector_capable is True but build_inspector_cmd has no implementation"
        raise AssertionError(msg)
