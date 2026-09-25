"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    CODEX_EFFORT_MAPPING,
    CODEX_HOME_ENV_VAR,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MODEL_ALIASES,
    CODEX_SESSIONS_SUBDIR,
    CODEX_VALID_MODEL_IDS,
    RETIRED_CODEX_MODEL_BASE_IDS,
    RETIRED_CODEX_MODEL_PREFIXES,
    RETIRED_CODEX_MODEL_SUFFIXES,
    BackendCapabilities,
    BackendConventions,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    CmdSpec,
    CodexRuntimeSpec,
    ExecutableLaunchBinding,
    ExecutionIdentity,
    ExplorationDispatchRenderer,
    HookTrustPolicy,
    InteractiveInvocationValidation,
    LineDriver,
    ManagedRouteHomeBackend,
    PreLaunchReadiness,
    ResumeSpec,
    SemanticAdaptationContext,
    SessionAttemptHandle,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    default_log_dir,
    get_logger,
    required_join_is_unsupported,
)
from autoskillit.execution.backends._backend_cmd_builder_base import FlagVocabulary
from autoskillit.execution.backends._codex.app_server import CodexAppServerDriver
from autoskillit.execution.backends._codex.headless_commands import (
    CodexOrdinaryHeadlessCommandMixin,
)
from autoskillit.execution.backends._codex.interactive_validation import (
    validate_codex_interactive_invocation,
)
from autoskillit.execution.backends._codex_cmd_builders import (
    CODEX_ENV_PREFIX_DENYLIST,
    NON_VARIADIC_CODEX_FLAGS,
    VARIADIC_CODEX_FLAGS,
    CodexEnvPolicy,
    CodexFlags,
    CodexSessionLocator,
    CodexStateReadinessProbe,
)
from autoskillit.execution.backends._codex_config import (
    CODEX_RECIPE_DELIVERY_BUDGET,
    CODEX_SPAWNABLE_BUILT_IN_AGENT_NAMES,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_discovery import (
    CODEX_CLI_MIN_VERSION,
    CODEX_MANAGED_HOME_ROUTE,
    CODEX_SKILL_DISCOVERY_CONTRACT,
)
from autoskillit.execution.backends._codex_execution_identity import (
    extract_codex_execution_identity,
)
from autoskillit.execution.backends._codex_explorer_projection import (
    _canonical_codex_model_effort,
    clear_explorer_binding_env,
    refresh_explorer_binding_env,
)
from autoskillit.execution.backends._codex_managed_route import (
    prepare_managed_codex_catalog as _prepare_managed_codex_catalog,
)
from autoskillit.execution.backends._codex_managed_route import (
    project_managed_route,
)
from autoskillit.execution.backends._codex_managed_route import (
    projected_manifest_path as _projected_manifest_path,
)
from autoskillit.execution.backends._codex_managed_route import (
    read_managed_codex_catalog as _read_managed_codex_catalog,
)
from autoskillit.execution.backends._codex_managed_route import (
    verify_managed_session_dir as _verify_managed_session_dir,
)
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser
from autoskillit.execution.backends._codex_prelaunch import codex_prelaunch_transaction
from autoskillit.execution.backends._codex_probes import (
    _validate_generated_codex_home,
    _validate_inert_rollout_paths,
)
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore
from autoskillit.execution.backends._explorer_dispatch import (
    CODEX_EXPLORATION_DISPATCH_RENDERER,
)

_CODEX_SQLITE_HOME_ENV_VAR = "CODEX_SQLITE_HOME"


def _validate_managed_skill_catalog(skills_dir: Path) -> list[str]:
    if skills_dir.is_symlink() or not skills_dir.is_dir():
        return [f"managed skills catalog must be a real directory: {skills_dir}"]
    managed_entries = [entry for entry in skills_dir.iterdir() if not entry.name.startswith(".")]
    if not managed_entries:
        return [f"managed skills catalog has no managed skills: {skills_dir}"]
    if any(
        entry.is_symlink() or not entry.is_dir() or not (entry / "SKILL.md").is_file()
        for entry in managed_entries
    ):
        return [f"managed skills catalog must contain real skill directories: {skills_dir}"]
    return []


def _append_symlink_shape_error(errors: list[str], path: Path, diagnostic: str) -> None:
    if path.exists() and not path.is_symlink():
        errors.append(diagnostic)


__all__ = [
    "CODEX_SKILL_DISCOVERY_CONTRACT",
    "CODEX_SPAWNABLE_BUILT_IN_AGENT_NAMES",
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

_CODEX_INTERACTIVE_VALUE_BEARING_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.MODEL,
        CodexFlags.MODEL_SHORT,
        CodexFlags.ADD_DIR,
        CodexFlags.SANDBOX,
        CodexFlags.CONFIG_OVERRIDE,
        CodexFlags.PROFILE,
    }
)


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
class CodexBackend(CodexOrdinaryHeadlessCommandMixin):
    source_codex_home: Path | None = None
    runtime_spec: CodexRuntimeSpec = field(default_factory=CodexRuntimeSpec)

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
            min_version=CODEX_CLI_MIN_VERSION,
            version_check_command="codex --version",
            process_name="codex",
            process_name_aliases=frozenset({"codex", "node"}),
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
            cook_exact_binding_probe_required=True,
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
            # Profile admission reads the same deprecated upstream user root.
            profile_skills_source=CODEX_MANAGED_HOME_ROUTE.discovery_root(source_codex_home),
            persistent_session_root_subdir=Path(CODEX_SESSIONS_SUBDIR),
            skill_sigil=self.capabilities.skill_sigil,
            managed_skill_discovery=CODEX_MANAGED_HOME_ROUTE,
        )

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer:
        return CODEX_EXPLORATION_DISPATCH_RENDERER

    def build_cmd(
        self,
        skill_command: str,
        cwd: str,
        *,
        generated_home: Path | str | None = None,
    ) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command, generated_home=generated_home)
        spec = replace(spec, cwd=cwd)
        if spec.app_server_plan is not None:
            spec = replace(spec, app_server_plan=replace(spec.app_server_plan, cwd=cwd))
        return spec

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

    def interactive_ordering_flags(self) -> tuple[frozenset[str], frozenset[str]]:
        return VARIADIC_CODEX_FLAGS, _CODEX_INTERACTIVE_VALUE_BEARING_FLAGS

    def translate_model(self, model: str) -> str:
        from autoskillit.core import (
            strip_context_window_suffix,
        )

        base = strip_context_window_suffix(model)
        if (
            base in RETIRED_CODEX_MODEL_BASE_IDS
            or base.startswith(RETIRED_CODEX_MODEL_PREFIXES)
            or base.endswith(RETIRED_CODEX_MODEL_SUFFIXES)
        ):
            raise ValueError(f"Retired Codex model: {base}")
        return CODEX_MODEL_ALIASES.get(base, base)

    prepare_managed_codex_catalog = _prepare_managed_codex_catalog
    read_managed_session_catalog = _read_managed_codex_catalog
    projected_manifest_path = _projected_manifest_path
    verify_managed_session_dir = _verify_managed_session_dir

    def model_config_overrides(self, model: str) -> tuple[str, ...]:
        from autoskillit.core import strip_context_window_suffix

        base = strip_context_window_suffix(model)
        effort = CODEX_EFFORT_MAPPING.get(base)
        if effort:
            return (f"model_reasoning_effort={effort}",)
        return ()

    def version_cmd(self) -> tuple[str, ...]:
        return ("codex", "--version")

    def validate_session_layout(
        self,
        session_dir: Path,
        *,
        project_dir: Path | None = None,
    ) -> list[str]:
        del project_dir
        errors: list[str] = []
        route = self.conventions.managed_skill_discovery
        if route is None:
            return ["backend declares no managed skill discovery route"]
        skills_dir = route.catalog_dir(session_dir)
        discovery_entry = route.discovery_root(session_dir)
        errors.extend(_validate_managed_skill_catalog(skills_dir))
        if route.entry_point_is_alias:
            assert discovery_entry is not None
            if not discovery_entry.is_symlink():
                errors.append(f"discovery entry point must be a symlink: {discovery_entry}")
            elif os.readlink(discovery_entry) != route.alias_target:
                errors.append(
                    f"discovery entry point has the wrong alias target: {discovery_entry}"
                )
            elif discovery_entry.resolve(strict=False) != skills_dir.resolve(strict=False):
                errors.append(
                    f"discovery entry point must resolve to managed catalog: {discovery_entry}"
                )
        config_path = session_dir / "config.toml"
        if not config_path.is_file():
            errors.append(f"config.toml does not exist: {config_path}")
        else:
            toml_content = config_path.read_text(encoding="utf-8")
            if "[mcp_servers.autoskillit]" not in toml_content:
                errors.append("config.toml missing [mcp_servers.autoskillit] section")
        auth_path = session_dir / "auth.json"
        _append_symlink_shape_error(
            errors,
            auth_path,
            f"auth.json must be a symlink, not a regular file: {auth_path}",
        )

        sessions_path = session_dir / "sessions"
        _append_symlink_shape_error(
            errors,
            sessions_path,
            f"sessions/ must be a symlink, not a regular directory: {sessions_path}",
        )
        archived_path = session_dir / "archived_sessions"
        _append_symlink_shape_error(
            errors,
            archived_path,
            f"archived_sessions/ must be a symlink, not a regular directory: {archived_path}",
        )

        rollout_errors, _ = _validate_inert_rollout_paths(session_dir)
        errors.extend(rollout_errors)
        return errors

    def validate_interactive_invocation(self, spec: CmdSpec) -> InteractiveInvocationValidation:
        return validate_codex_interactive_invocation(spec)

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
        if required_join_is_unsupported(
            plan,
            self.capabilities,
            self.name,
            adaptation_context,
        ):
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
        if session_dir is None:
            return PreLaunchReadiness(())
        try:
            assert self.source_codex_home is not None
            generated_home = Path(session_dir).expanduser().resolve(strict=False)
            with codex_prelaunch_transaction(
                source_codex_home=self.source_codex_home,
                destination_home=generated_home,
                runtime_spec=self.runtime_spec,
                hook_config_format=self.capabilities.hook_config_format,
                plugin_dir=plugin_dir,
            ):
                return PreLaunchReadiness(
                    (),
                    {
                        CODEX_HOME_ENV_VAR: str(generated_home),
                        _CODEX_SQLITE_HOME_ENV_VAR: str(generated_home),
                    },
                )
        except Exception as exc:
            logger.error("codex_prelaunch_transaction_failed", exc_info=True)
            return PreLaunchReadiness((f"Codex pre-launch configuration failed: {exc}",))

    def probe_launch_readiness(
        self, *, session_dir: Path, executable: ExecutableLaunchBinding
    ) -> PreLaunchReadiness:
        generated_home = Path(session_dir).expanduser().resolve(strict=False)
        try:
            errors = tuple(
                _validate_generated_codex_home(
                    generated_home,
                    config_path=generated_home / "config.toml",
                    executable=executable,
                )
            )
        except Exception as exc:
            logger.error("codex_launch_readiness_probe_failed", exc_info=True)
            return PreLaunchReadiness((f"Codex launch readiness probe failed: {exc}",))
        return PreLaunchReadiness(
            errors,
            {
                CODEX_HOME_ENV_VAR: str(generated_home),
                _CODEX_SQLITE_HOME_ENV_VAR: str(generated_home),
            },
        )

    def recover_cook_history(self) -> None:
        CodexSessionStore(log_dir=default_log_dir()).recover()

    def session_attempt_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> AbstractContextManager[SessionAttemptHandle]:
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

    def line_driver(self, spec: CmdSpec) -> LineDriver | None:
        if spec.app_server_plan is None:
            return None
        return CodexAppServerDriver(spec.app_server_plan)


if TYPE_CHECKING:
    _MANAGED_ROUTE_CONFORMANCE: type[ManagedRouteHomeBackend] = CodexBackend
