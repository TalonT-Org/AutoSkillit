from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex as re
from packaging.version import InvalidVersion, Version

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    CAMPAIGN_ID_ENV_VAR,
    CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION,
    CLAUDE_CODE_CAPABILITIES,
    CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
    CLAUDE_MCP_CONNECT_TIMEOUT_ENV_VAR,
    CLAUDE_MCP_CONNECT_TIMEOUT_MS,
    CLAUDE_MCP_CONNECTION_NONBLOCKING,
    CONTEXT_EXHAUSTION_MARKER,
    NON_VARIADIC_CLAUDE_FLAGS,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    SESSION_ADD_DIR_SUBDIR,
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
    CookSessionHandle,
    ExecutableLaunchBinding,
    ExplorationDispatchRenderer,
    ManagedHeadlessSessionLineageRef,
    NamedResume,
    NativeShellCaptureDecision,
    NoResume,
    OutputFormat,
    PluginLaunchBinding,
    PreLaunchReadiness,
    ResumeSpec,
    SessionCheckpoint,
    SessionEvent,
    SessionLocator,
    SessionSummary,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    SkillSessionConfig,
    ValidatedAddDir,
    YAMLError,
    build_agent_env,
    claude_code_log_path,
    claude_code_project_dir,
    executable_binding_matches_current_file,
    extract_skill_name,
    fast_loads,
    load_yaml,
    pkg_root,
    read_registry,
    truncate_text,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
    FlagVocabulary,
)
from autoskillit.execution.backends._claude_prompt import (
    _CLAUDE_SKILL_SESSION_HARDENING,
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
from autoskillit.execution.backends._explorer_dispatch import (
    CLAUDE_EXPLORATION_DISPATCH_RENDERER,
)
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import parse_session_result

log = logging.getLogger(__name__)  # noqa: TID251 — stdlib fallback: used before configure_logging(); structlog proxy would emit to stderr via import-time WriteLoggerFactory
_EXPLORER_BINDING_REJECTION_MESSAGE = "Claude Code does not support explorer binding projection"

# The minimum annotation-support version as a pre-parsed Version instance,
# derived from the core constant to avoid redundant string parsing at every
# launch. Used by _claude_host_attestation_env() to determine whether the
# installed Claude Code CLI supports ``anthropic/maxResultSizeChars``.
_ANNOTATION_SUPPORT_MIN = Version(CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION)


#: Documented Claude Code env-var that enables/disables the agent-teams
#: surface. Confirmed via code.claude.com/docs/en/agent-teams as the only
#: public toggle. The repository-scoped force-inactive setting removes or
#: overrides this env var before every Claude launch and any conflicting
#: entry in the target repo's .claude/settings*.json files.
CLAUDE_AGENT_TEAMS_ENV_VAR: str = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"


def _neutralize_agent_teams_env(env: dict[str, str]) -> None:
    """Remove ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`` from ``env`` in place."""
    env.pop(CLAUDE_AGENT_TEAMS_ENV_VAR, None)


def detect_repository_agent_teams_setting(
    project_root: Path | str | None,
) -> tuple[str | None, str]:
    """Return (effective_value, source_path) for any conflicting settings file.

    Per Claude Code's documented settings precedence, ``env.<var>`` entries
    in ``.claude/settings.json`` or ``.claude/settings.local.json`` apply
    after user-level settings and can re-enable teams even when the
    launcher process env has the var unset.

    Returns ``(None, "")`` when no conflicting entry is found. The caller
    must combine the launcher-env scan with this file scan and refuse the
    launch when neither confirms an inactive effective state.
    """
    if project_root is None:
        return (None, "")
    root = Path(project_root).expanduser().resolve()
    candidates = (root / ".claude" / "settings.json", root / ".claude" / "settings.local.json")
    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        try:
            import json as _json

            parsed = _json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        env = parsed.get("env")
        if not isinstance(env, dict):
            continue
        value = env.get(CLAUDE_AGENT_TEAMS_ENV_VAR)
        if isinstance(value, str):
            return (value, str(candidate))
    return (None, "")


#: Truthy values that re-enable Claude agent teams if present in the env.
_AGENT_TEAMS_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _active_agent_teams(value: str) -> bool:
    """Return True if the string value would re-enable agent teams."""
    return value.strip().lower() in _AGENT_TEAMS_TRUTHY


def neutralize_repository_agent_teams_settings(project_root: Path | str | None) -> int:
    """Strip conflicting ``env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`` entries.

    Returns the number of settings files modified. Each file is rewritten
    after the offending key is removed. Refuses to rewrite when the file
    is malformed or unreadable.
    """
    if project_root is None:
        return 0
    root = Path(project_root).expanduser().resolve()
    candidates = (root / ".claude" / "settings.json", root / ".claude" / "settings.local.json")
    modified = 0
    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            continue
        try:
            import json as _json

            parsed = _json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        env = parsed.get("env")
        if not isinstance(env, dict):
            continue
        if CLAUDE_AGENT_TEAMS_ENV_VAR not in env:
            continue
        del env[CLAUDE_AGENT_TEAMS_ENV_VAR]
        try:
            new_content = _json.dumps(parsed, indent=2, sort_keys=True)
        except (ValueError, TypeError):
            continue
        from autoskillit.core.io import atomic_write

        atomic_write(candidate, new_content)
        modified += 1
    return modified


def _resolve_project_root_for_inactive_check(project_root: Path | str | None) -> None:
    """Refuse a headless launch when ``force_inactive_agent_teams=True`` but no project_root was provided.

    Without ``project_root``, ``assert_agent_teams_inactive`` cannot read
    the target repo's ``.claude/settings*.json`` files, so the only path
    it can confirm is the resolved launcher env. The plan's Step 5 (3)
    requires a positive confirmation of BOTH the env and the settings
    files; passing ``None`` is a fail-open bypass.
    """
    if project_root is None:
        raise RuntimeError(
            "force_inactive_agent_teams=True requires project_root so the "
            "settings file scan can confirm inactivity"
        )


def _interactive_invocation_environment_policy(
    env: Mapping[str, str],
    project_root: Path | str | None,
) -> list[str]:
    """Content-policy errors for an interactive Claude launch.

    The interactive cook/order checkpoint must positively confirm that the
    effective environment will leave Claude agent teams inactive. Returns
    a list of human-readable error strings (empty list when no violation
    is detected). The launch layer surfaces these as pre-spawn failures.

    The policy matches what the per-builder assertions check — the launch
    env must not carry a truthy ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS``
    value, and any conflicting entry in the target repository's
    ``.claude/settings*.json`` files would re-enable teams under Claude's
    documented settings precedence.
    """
    errors: list[str] = []
    env_value = env.get(CLAUDE_AGENT_TEAMS_ENV_VAR)
    if env_value is not None and _active_agent_teams(env_value):
        errors.append(
            f"{CLAUDE_AGENT_TEAMS_ENV_VAR}={env_value!r} is set in the launch "
            f"environment; Claude agent teams would be active at launch"
        )
    file_value, file_path = detect_repository_agent_teams_setting(project_root)
    if file_value is not None and _active_agent_teams(file_value):
        errors.append(
            f"{CLAUDE_AGENT_TEAMS_ENV_VAR}={file_value!r} is set in "
            f"{file_path}; Claude agent teams would be re-enabled by "
            "repository settings precedence"
        )
    return errors


def assert_agent_teams_inactive(
    env: Mapping[str, str],
    project_root: Path | str | None,
    *,
    force_inactive: bool,
) -> None:
    """Verify that the effective environment will result in inactive agent teams.

    Raises ``RuntimeError`` when ``force_inactive`` is True but neither the
    process env nor the target repository's settings files positively
    confirm an inactive policy. This is the pre-spawn refusal surface.
    """
    if not force_inactive:
        return
    if CLAUDE_AGENT_TEAMS_ENV_VAR in env and _active_agent_teams(env[CLAUDE_AGENT_TEAMS_ENV_VAR]):
        raise RuntimeError(
            f"force_inactive_agent_teams requested but {CLAUDE_AGENT_TEAMS_ENV_VAR} "
            f"is set to {env[CLAUDE_AGENT_TEAMS_ENV_VAR]!r} in the launch env"
        )
    file_value, file_path = detect_repository_agent_teams_setting(project_root)
    if file_value is not None and _active_agent_teams(file_value):
        raise RuntimeError(
            f"force_inactive_agent_teams requested but {CLAUDE_AGENT_TEAMS_ENV_VAR} "
            f"is set to {file_value!r} in {file_path}"
        )


def _claude_host_attestation_env(
    installed_version: Version | None,
) -> dict[str, str]:
    """Build the host client attestation env for one Claude-launched session.

    Carries the launcher's attestation of what the connected Claude Code host
    client supports to the MCP server — read once at server startup (see
    ``server._recipe_delivery``) and used as the conservative-default source
    for recipe-delivery decisions.

    ``annotation_support`` is derived from the installed CLI version probed by
    ``ensure_pre_launch()`` — not hardcoded. Below 2.1.91, annotation metadata
    is stripped by the client and tool results fall back to token-gated
    ``MAX_MCP_OUTPUT_TOKENS`` only. When the installed version is unknown
    (pre-launch probe not yet run), annotation support defaults to ``"0"``
    (conservative fallback).

    Deliberately NOT part of ``SHARED_BASELINE_ENV``: Codex has its own
    receipt-based protected recipe-delivery pipeline and must never be told it
    has annotation support.
    """
    meta_support = (
        "1"
        if installed_version is not None and installed_version >= _ANNOTATION_SUPPORT_MIN
        else "0"
    )
    return {
        AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS: str(CLAUDE_INJECTED_CLIENT_RESULT_TOKENS),
        AUTOSKILLIT_ATTESTED_META_SUPPORT: meta_support,
    }


_ORDER_GREETING_PREFIXES = (
    "Today's special:",
    "Order up! Today's special:",
    "Order up! The kitchen",
    "Kitchen's open!",
    "Table for one!",
    "Fresh off the menu",
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
)

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

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        normalized_cwd = str(Path(cwd).expanduser().resolve(strict=False))
        index_path = self.project_log_dir(normalized_cwd) / "sessions-index.json"
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(entries, list):
            return ()

        launch_ids_by_session_id = {
            claude_session_id: launch_id
            for launch_id, registry_entry in read_registry(Path(normalized_cwd)).items()
            if isinstance(registry_entry, Mapping)
            and isinstance(
                claude_session_id := registry_entry.get("claude_session_id"),
                str,
            )
        }
        summaries: list[SessionSummary] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            entry_cwd = entry.get("cwd")
            if not isinstance(entry_cwd, str):
                continue
            resolved_entry_cwd = str(Path(entry_cwd).expanduser().resolve(strict=False))
            if resolved_entry_cwd != normalized_cwd:
                continue

            session_id = entry.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                continue
            first_prompt = entry.get("firstPrompt")
            normalized_prompt = first_prompt if isinstance(first_prompt, str) else ""
            summary = entry.get("summary")
            git_branch = entry.get("gitBranch")
            modified = entry.get("modified")
            summaries.append(
                SessionSummary(
                    backend_name=AGENT_BACKEND_CLAUDE_CODE,
                    session_id=session_id,
                    launch_id=launch_ids_by_session_id.get(session_id),
                    cwd=resolved_entry_cwd,
                    first_prompt=normalized_prompt,
                    summary=summary if isinstance(summary, str) else "",
                    git_branch=git_branch if isinstance(git_branch, str) else None,
                    modified=modified if isinstance(modified, str) else None,
                    is_sidechain=False,
                    session_type_hint=(
                        "order"
                        if normalized_prompt.startswith(_ORDER_GREETING_PREFIXES)
                        else "cook"
                    ),
                )
            )
        return tuple(summaries)


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

        if record_type in {"task_started", "task_progress", "task_notification", "task_updated"}:
            task_id = obj.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            status: object = obj.get("status")
            if record_type == "task_updated":
                patch = obj.get("patch")
                if not isinstance(patch, dict):
                    return SessionEvent(
                        kind=BackendEventKind.IGNORED,
                        is_terminal=False,
                        has_marker=False,
                    )
                status = patch.get("status")
            active_statuses = {"pending", "running", "paused"}
            terminal_statuses = {"completed", "failed", "stopped", "killed"}
            if record_type in {"task_started", "task_progress"}:
                task_active = True
            elif status in active_statuses:
                task_active = True
            elif status in terminal_statuses:
                task_active = False
            else:
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            return SessionEvent(
                kind=BackendEventKind.TASK_LIFECYCLE,
                is_terminal=False,
                has_marker=False,
                task_id=task_id.strip(),
                task_active=task_active,
            )

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
            message = obj.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "ScheduleWakeup"
                for block in content
            ):
                return SessionEvent(
                    kind=BackendEventKind.SCHEDULE_WAKEUP,
                    is_terminal=False,
                    has_marker=False,
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

    @property
    def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer:
        return CLAUDE_EXPLORATION_DISPATCH_RENDERER

    def setup_session_dir(
        self,
        session_dir: Path,
        *,
        parent_sandbox_mode: str = "workspace-write",
        explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
        execution_role: SkillExecutionRole = SkillExecutionRole.SESSION,
    ) -> frozenset[str] | None:
        del execution_role
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
        return CmdSpec(
            cmd=spec.cmd,
            env=spec.env,
            cwd=cwd,
            inherited_fds=spec.inherited_fds,
        )

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
    ) -> CmdSpec:
        cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
        if model:
            cmd += [ClaudeFlags.MODEL, self.translate_model(model)]
        env = dict(build_agent_env(base=base, extras=env_extras, required=required))
        env.update(_HEADLESS_ENV_HARDENING)
        if force_inactive_agent_teams:
            _neutralize_agent_teams_env(env)
            _resolve_project_root_for_inactive_check(project_root)
            assert_agent_teams_inactive(env, project_root, force_inactive=True)
        return CmdSpec(cmd=tuple(cmd), env=env)

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
            settings_root = str(executable.cwd) if executable is not None else None
            assert_agent_teams_inactive(
                neutralized_env,
                settings_root,
                force_inactive=True,
            )
            neutralize_repository_agent_teams_settings(settings_root)
            effective_env = neutralized_env
        if executable is not None and dict(effective_env) != dict(executable.launch_environment):
            raise ValueError("interactive environment changed after executable binding")
        partial = builder.build()
        return CmdSpec(
            cmd=partial.cmd,
            env=(executable.launch_environment if executable is not None else effective_env),
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
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
    ) -> CmdSpec:
        del (
            native_shell_capture_decision,
            managed_lineage_ref,
            managed_attempt_id,
            include_scope_discipline,
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
        )

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
        )
        extras.update(_claude_host_attestation_env(None))
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
        force_inactive_agent_teams: bool = False,
        project_root: Path | str | None = None,
    ) -> CmdSpec:
        del (
            native_shell_capture_decision,
            managed_lineage_ref,
            managed_attempt_id,
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

    def adapt_skill_semantics(self, plan: SkillSemanticPlan) -> SkillSemanticAdaptationResult:
        """Adapt portable skill requirements to Claude Code instructions."""
        if (
            plan.join is not None
            and plan.join.required
            and not self.capabilities.fixed_set_join_capable
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
                "Before this wave, call declare_join_batch with the loaded skill "
                "name and one assignment label per direct child. Then issue every "
                "member as one ordinary unnamed foreground Agent(subagent_type=...) "
                "call in a single message. Retain every direct result. Only after "
                "the ledger reports complete do you synthesize or allow Stop."
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
        re-enable ``CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS``. The plan's
        Step 5.4 mandates this content-policy surface here in addition to
        the per-builder enforcement.
        """
        env = dict(spec.env) if spec is not None else {}
        project_root: Path | str | None = None
        cwd = spec.cwd if spec is not None else None
        if cwd is not None:
            project_root = cwd
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

    def recover_cook_history(self) -> None:
        return None

    def cook_session_context(
        self,
        *,
        session_home: Path,
        project_dir: Path,
        launch_id: str,
        attempt: int,
        current_resume_spec: ResumeSpec,
    ) -> AbstractContextManager[CookSessionHandle]:
        del session_home, project_dir, launch_id, attempt, current_resume_spec
        return nullcontext(
            CookSessionHandle(
                view_id="",
                pass_fds=(),
                _record_spawn=_ignore_child_identity,
                _record_reaped=_ignore_child_identity,
            )
        )

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        if not self.capabilities.inspector_capable:
            raise CapabilityNotSupportedError("inspector_capable", self.name)
        msg = "inspector_capable is True but build_inspector_cmd has no implementation"
        raise AssertionError(msg)


def _ignore_child_identity(pid: int, pgid: int) -> None:
    del pid, pgid
