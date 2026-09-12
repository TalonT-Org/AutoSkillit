"""Backend orchestration for the Claude Code CLI session runner.

Owns the Claude Code env policy (`ClaudeEnvPolicy`), the host-attestation
helper, and the `ClaudeCodeBackend` implementation. Public surface
includes the session locator and result parsers re-exported from the
sibling modules `_claude_session_locator` and `_claude_parse`.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import regex as re
from packaging.version import InvalidVersion, Version

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    CLAUDE_CODE_CAPABILITIES,
    CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_MS,
    CLAUDE_MCP_CONNECTION_NONBLOCKING,
    NON_VARIADIC_CLAUDE_FLAGS,
    SESSION_ADD_DIR_SUBDIR,
    VARIADIC_CLAUDE_FLAGS,
    AgentDef,
    BackendCapabilities,
    BackendConventions,
    BareResume,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    ClaudeFlags,
    CmdSpec,
    ExecutableLaunchBinding,
    ExplorationDispatchRenderer,
    LineDriver,
    ManagedHeadlessSessionLineageRef,
    NamedResume,
    NativeShellCaptureDecision,
    NoResume,
    OutputFormat,
    PluginLaunchBinding,
    PreLaunchReadiness,
    ResumeSpec,
    SemanticAdaptationContext,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    ValidatedAddDir,
    YAMLError,
    build_agent_env,
    executable_binding_matches_current_file,
    load_yaml,
    pkg_root,
    required_join_is_unsupported,
    truncate_text,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    FlagVocabulary,
)
from autoskillit.execution.backends._claude.environment import (
    ClaudeCookSupportMixin,
    ClaudeEnvPolicy,
    _claude_host_attestation_env,
    _interactive_invocation_environment_policy,
    _neutralize_agent_teams_env,
    _resolve_project_root_for_inactive_check,
    assert_agent_teams_inactive,
    detect_repository_agent_teams_setting,
    find_malformed_agent_teams_settings,
    neutralize_repository_agent_teams_settings,
)
from autoskillit.execution.backends._claude.session_commands import ClaudeSessionCommandMixin
from autoskillit.execution.backends._claude_parse import (
    ClaudeResultParser,
    ClaudeStreamParser,
)
from autoskillit.execution.backends._claude_prompt import (
    _CLAUDE_SKILL_SESSION_HARDENING,
    _HEADLESS_ENV_HARDENING,
    _INTERACTIVE_ENV_EXCLUSIONS,
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _apply_output_format,
)
from autoskillit.execution.backends._claude_session_locator import ClaudeSessionLocator
from autoskillit.execution.backends._cmd_builder import CmdBuilder
from autoskillit.execution.backends._explorer_dispatch import (
    CLAUDE_EXPLORATION_DISPATCH_RENDERER,
)

log = logging.getLogger(__name__)  # noqa: TID251 — stdlib fallback: used before configure_logging(); structlog proxy would emit to stderr via import-time WriteLoggerFactory
_EXPLORER_BINDING_REJECTION_MESSAGE = "Claude Code does not support explorer binding projection"


__all__ = [
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
    "detect_repository_agent_teams_setting",
    "find_malformed_agent_teams_settings",
]


@dataclass(frozen=True, slots=True)
class ClaudeCodeBackend(ClaudeCookSupportMixin, ClaudeSessionCommandMixin):
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

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer:
        return CLAUDE_EXPLORATION_DISPATCH_RENDERER

    def setup_session_dir(
        self,
        session_dir: Path,
        *,
        parent_sandbox_mode: str = "workspace-write",
        agent_defs: tuple[AgentDef, ...] | None = None,
        explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
        execution_role: SkillExecutionRole = SkillExecutionRole.SESSION,
    ) -> frozenset[str] | None:
        del agent_defs, execution_role
        if explorer_binding_env:
            raise ValueError(_EXPLORER_BINDING_REJECTION_MESSAGE)
        return None

    def refresh_explorer_binding_env(
        self,
        session_dir: Path,
        explorer_binding_env: Mapping[str, Mapping[str, str]],
    ) -> None:
        if explorer_binding_env:
            raise ValueError(_EXPLORER_BINDING_REJECTION_MESSAGE)

    def clear_explorer_binding_env(self, session_dir: Path, roles: frozenset[str]) -> None:
        if roles:
            raise ValueError(_EXPLORER_BINDING_REJECTION_MESSAGE)

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return replace(spec, cwd=cwd)

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
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
        mcp_tool_timeout_sec: float | None = None,
    ) -> CmdSpec:
        cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
        if model:
            cmd += [ClaudeFlags.MODEL, self.translate_model(model)]
        env = dict(build_agent_env(base=base, extras=env_extras, required=required))
        env.update(_HEADLESS_ENV_HARDENING)
        if (
            mcp_tool_timeout_sec is not None
            and isinstance(mcp_tool_timeout_sec, (int, float))
            and mcp_tool_timeout_sec > 0
        ):
            env[CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR] = str(mcp_tool_timeout_sec)
        if force_inactive_agent_teams:
            _neutralize_agent_teams_env(env)
            _resolve_project_root_for_inactive_check(project_root)
            assert_agent_teams_inactive(env, project_root, force_inactive=True)
        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            force_inactive_agent_teams=force_inactive_agent_teams,
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
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
        mcp_tool_timeout_sec: float | None = None,
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
        plugin_binding
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
        mcp_tool_timeout_sec
            When given, injects ``CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`` so Claude Code's
            client-side idle-abort timeout for this MCP server matches the server-side
            ``anyio.fail_after`` ceiling. ``None`` omits the injection.

        Orchestration level
        -------------------
        An interactive session operates at L1 (cook, ``autoskillit cook``) by default.
        It becomes an L2 orchestrator (order, ``autoskillit order``) when the user
        calls ``open_kitchen``, granting full kitchen access and the ability to dispatch
        L1 ``run_skill`` workers. Unlike headless sessions, the orchestration level of
        an interactive session is determined at runtime by kitchen state, not by a
        ``SESSION_TYPE`` env variable.
        """
        del generated_home
        builder = CmdBuilder(str(executable.path) if executable is not None else "claude")
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
        if plugin_binding is not None:
            builder.kv_flag(ClaudeFlags.PLUGIN_DIR, str(plugin_binding.plugin_dir))
        if initial_prompt is not None:
            builder.positional(initial_prompt)
        for d in add_dirs:
            builder.variadic_pair(ClaudeFlags.ADD_DIR, str(d))
        for t in tools:
            builder.variadic_pair(ClaudeFlags.TOOLS, t)
        merged: dict[str, str] = dict(SHARED_BASELINE_ENV) | _claude_host_attestation_env(None)
        merged[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        merged[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if env_extras:
            merged.update(env_extras)
        merged["MCP_CONNECTION_NONBLOCKING"] = CLAUDE_MCP_CONNECTION_NONBLOCKING
        merged[CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR] = str(CLAUDE_MCP_CONNECT_TIMEOUT_MS)
        if (
            mcp_tool_timeout_sec is not None
            and isinstance(mcp_tool_timeout_sec, (int, float))
            and mcp_tool_timeout_sec > 0
        ):
            merged[CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR] = str(mcp_tool_timeout_sec)
        interactive_base = {
            k: v for k, v in os.environ.items() if k not in _INTERACTIVE_ENV_EXCLUSIONS
        }
        effective_env = build_agent_env(
            base=interactive_base,
            extras=merged,
            required=required_env,
        )
        if force_inactive_agent_teams:
            # ``build_agent_env`` returns a read-only ``MappingProxyType``;
            # neutralize on a single mutable copy and re-derive both the
            # assertion and the launch env from it.
            neutralized_env = dict(effective_env)
            _neutralize_agent_teams_env(neutralized_env)
            settings_root = str(project_root) if project_root is not None else None
            _resolve_project_root_for_inactive_check(settings_root)
            # Settings entries are stripped before the confirmation, not after:
            # asserting first refuses every repository whose settings enable
            # teams, which is precisely the population this opt-in serves. A
            # malformed file is refused rather than rewritten, so the assertion
            # below still fails closed on one.
            neutralize_repository_agent_teams_settings(settings_root)
            assert_agent_teams_inactive(
                neutralized_env,
                settings_root,
                force_inactive=True,
            )
            effective_env = neutralized_env
        # With an executable binding this equality is the proof that the
        # binding was resolved from the neutralized env rather than captured
        # before neutralization; a genuinely stale binding still fails here.
        if executable is not None and dict(effective_env) != dict(executable.launch_environment):
            raise ValueError("interactive environment changed after executable binding")
        partial = builder.build()
        return CmdSpec(
            cmd=partial.cmd,
            env=(executable.launch_environment if executable is not None else effective_env),
            origin=partial.origin,
            is_resume=isinstance(resume_spec, (NamedResume, BareResume)),
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
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
        env_extras: Mapping[str, str] | None = None,
        native_shell_capture_decision: NativeShellCaptureDecision | None = None,
        managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None,
        managed_attempt_id: str | None = None,
        include_scope_discipline: bool = False,
        skill_session: bool = False,
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
        mcp_tool_timeout_sec: float | None = None,
    ) -> CmdSpec:
        del (
            native_shell_capture_decision,
            managed_lineage_ref,
            managed_attempt_id,
            include_scope_discipline,
            session_home,
        )
        cmd: list[str] = [
            "claude",
            ClaudeFlags.PRINT,
            prompt,
            ClaudeFlags.RESUME,
            resume_session_id,
            ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS,
        ]
        _apply_output_format(cmd, output_format)
        if plugin_binding is not None:
            cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_binding.plugin_dir)]
        merged: dict[str, str] = dict(SHARED_BASELINE_ENV) | _claude_host_attestation_env(None)
        merged[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        merged[AGENT_BACKEND_DYNACONF_ENV_VAR] = AGENT_BACKEND_CLAUDE_CODE
        if env_extras:
            for key, value in env_extras.items():
                if key not in _PROVIDER_EXTRAS_BASE_DENYLIST:
                    merged[key] = value
        if (
            mcp_tool_timeout_sec is not None
            and isinstance(mcp_tool_timeout_sec, (int, float))
            and mcp_tool_timeout_sec > 0
        ):
            merged[CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT_ENV_VAR] = str(mcp_tool_timeout_sec)
        env = dict(build_agent_env(base={}, extras=merged))
        env.update(_HEADLESS_ENV_HARDENING)
        if skill_session:
            env.update(_CLAUDE_SKILL_SESSION_HARDENING)
        if force_inactive_agent_teams:
            _neutralize_agent_teams_env(env)
            _resolve_project_root_for_inactive_check(project_root)
            assert_agent_teams_inactive(env, project_root, force_inactive=True)
        return CmdSpec(
            cmd=tuple(cmd),
            env=env,
            is_resume=True,
            inherited_fds=plugin_binding.inherited_fds if plugin_binding is not None else (),
            force_inactive_agent_teams=force_inactive_agent_teams,
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
            session_dir / SESSION_ADD_DIR_SUBDIR / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
        )
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

    def adapt_skill_semantics(
        self,
        plan: SkillSemanticPlan,
        adaptation_context: SemanticAdaptationContext | None = None,
    ) -> SkillSemanticAdaptationResult:
        """Adapt portable skill requirements to Claude Code instructions."""
        if required_join_is_unsupported(
            plan,
            self.capabilities,
            self.name,
            adaptation_context,
        ):
            return SkillSemanticAdaptationResult(
                unsupported_operation=SkillSemanticOperation.REQUIRED_JOIN,
                diagnostic=(
                    "Claude Code cannot support join.required=true: the runtime "
                    "does not have the declared-batch, claim guard, success/failure "
                    "settlers, unresolved-follow-up gate, and Stop completion gate "
                    "all capability-attested. Refuse the skill at admission."
                ),
            )
        role_mapping = {role.name: role.name for role in plan.logical_roles}
        sibling_targets = {
            sibling.name: f"/autoskillit:{sibling.name}" for sibling in plan.sibling_skills
        }
        model_policy: dict[str, tuple[str, str | None]] = {}
        fragments = [f"Logical role {role.name!r}: {role.purpose}." for role in plan.logical_roles]
        for policy in plan.child_model_policies:
            model_id = self.translate_model(policy.model_class) if policy.model_class else ""
            model_policy[policy.role] = (model_id, policy.reasoning_effort)
        for spawn in plan.child_spawns:
            spawn_policy = next(
                (
                    candidate
                    for candidate in plan.child_model_policies
                    if candidate.role == spawn.role
                ),
                None,
            )
            model_arg = (
                f", model={spawn_policy.model_class!r}"
                if spawn_policy is not None and spawn_policy.model_class is not None
                else ""
            )
            effort_text = (
                f" under reasoning policy {spawn_policy.reasoning_effort!r}"
                if spawn_policy is not None and spawn_policy.reasoning_effort is not None
                else ""
            )
            if spawn.for_each is not None:
                fragments.append(
                    f"Issue one Agent(subagent_type={spawn.role!r}{model_arg}) call per "
                    f"runtime item in {spawn.for_each!r}{effort_text}."
                )
            else:
                assert spawn.count is not None
                fragments.append(
                    f"Issue {spawn.count} Agent(subagent_type={spawn.role!r}{model_arg}) "
                    f"call{'s' if spawn.count != 1 else ''}{effort_text}."
                )
        if plan.concurrency is not None and plan.concurrency.required:
            fragments.append("Issue all independent child calls in one message so they overlap.")
        if plan.join is not None and plan.join.required:
            fragments.append(
                "Before this wave, call declare_join_batch with the normalized bare skill "
                "name and exact session_id delivered at runtime by the Skill PostToolUse "
                "additionalContext, plus one assignment label per direct child. If that "
                "context is absent, report the missing join authority and stop; do not invent "
                "an ID or scan binding files. Then issue every member as one ordinary unnamed "
                "foreground Agent(subagent_type=...) call in a single message. Retain every "
                "direct result. Only after the ledger reports complete do you synthesize or "
                "allow Stop."
            )
        if plan.evidence is not None and plan.evidence.required:
            boundary = "independent " if plan.evidence.independent else ""
            fragments.append(f"Require {boundary}evidence from each child result.")
        fragments.extend(f"Invoke sibling skill {target}." for target in sibling_targets.values())
        fragments.extend(
            f"Perform the required git metadata write: {write.purpose}."
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

    def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
        """Verify the interactive launch spec's effective environment policy.

        When the spec carries a request to keep Claude agent teams inactive,
        this checkpoint positively confirms that neither the resolved env
        nor the target repository's ``.claude/settings*.json`` files
        re-enable ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS``. A spec that
        carries no such request is not subject to the policy: agent teams
        enabled by a developer's own settings are legitimate state, not a
        launch defect.
        """
        if not spec.force_inactive_agent_teams:
            return []
        env = dict(spec.env)
        project_root: Path | str | None = spec.cwd if spec.cwd else None
        return _interactive_invocation_environment_policy(env, project_root)

    def ensure_pre_launch(
        self,
        *,
        session_dir: Path | None = None,
        executable: ExecutableLaunchBinding | None = None,
        plugin_dir: Path | None = None,
    ) -> PreLaunchReadiness:
        del session_dir, plugin_dir
        if executable is None:
            return PreLaunchReadiness(
                errors=("Claude Code launch requires an exact executable binding",)
            )
        if not executable_binding_matches_current_file(executable):
            return PreLaunchReadiness(
                errors=("Claude Code executable changed after capability probing",)
            )
        environment = executable.launch_environment
        try:
            result = subprocess.run(
                (str(executable.path), "--version"),
                capture_output=True,
                text=True,
                timeout=5,
                env=dict(environment),
                cwd=str(executable.cwd),
            )
        except subprocess.TimeoutExpired:
            return PreLaunchReadiness(errors=("Claude Code capability probe timed out",))
        except OSError as exc:
            return PreLaunchReadiness(errors=(f"Claude Code capability probe failed: {exc}",))
        stdout = result.stdout if isinstance(result.stdout, str) else ""
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.returncode != 0:
            raw_diagnostic = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
            normalized = "".join(char if char.isprintable() else " " for char in raw_diagnostic)
            diagnostic = truncate_text(" ".join(normalized.split()), max_len=1_000)
            detail = f": {diagnostic}" if diagnostic else ""
            return PreLaunchReadiness(
                errors=(
                    "Claude Code capability probe failed with exit code "
                    f"{result.returncode}{detail}",
                )
            )
        output = stdout.strip() or stderr.strip()
        if not output:
            return PreLaunchReadiness(
                errors=("Claude Code capability probe returned empty output",)
            )
        match = re.search(r"\b(\d+\.\d+\.\d+)\b", output)
        if match is None:
            return PreLaunchReadiness(
                errors=("Claude Code capability probe returned unparseable version output",)
            )
        try:
            installed = Version(match.group(1))
            minimum = Version(self.capabilities.min_version)
        except InvalidVersion:
            return PreLaunchReadiness(
                errors=("Claude Code capability probe returned unparseable version output",)
            )
        if installed < minimum:
            return PreLaunchReadiness(
                errors=(f"AutoSkillit requires Claude Code {minimum} or newer; found {installed}",)
            )
        return PreLaunchReadiness(errors=(), attested_env=_claude_host_attestation_env(installed))

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        if not self.capabilities.inspector_capable:
            raise CapabilityNotSupportedError("inspector_capable", self.name)
        msg = "inspector_capable is True but build_inspector_cmd has no implementation"
        raise AssertionError(msg)

    def line_driver(self, spec: CmdSpec) -> LineDriver | None:
        del spec
        return None
