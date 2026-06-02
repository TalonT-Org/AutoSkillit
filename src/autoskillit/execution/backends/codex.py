"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

import zstandard

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_APPLICABLE_GUARDS,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_INTERACTIVE_REQUIRED_ENV,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    RESUME_SESSION_BASELINE_KEYS,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    BackendCapabilities,
    BackendConventions,
    BareResume,
    ClaudeDirectoryConventions,
    CmdSpec,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    SkillSessionConfig,
    ValidatedAddDir,
    default_log_dir,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    _SESSION_BASELINE_ENV,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    _inject_completion_directive,
    _inject_completion_reminder,
    _inject_cwd_anchor,
    _inject_narration_suppression,
)
from autoskillit.execution.backends._cmd_builder import CmdBuilder
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser

__all__ = [
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexSessionLocator",
    "ensure_codex_mcp_registered",
]

logger = get_logger(__name__)


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
    CONFIG_OVERRIDE = "-c"
    DANGEROUSLY_BYPASS = "--dangerously-bypass-approvals-and-sandbox"


CODEX_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
    }
)

CODEX_ENV_PREFIX_DENYLIST: tuple[str, ...] = ("CLAUDE_CODE_",)


@dataclass(frozen=True, slots=True)
class CodexEnvPolicy:
    denylist_prefixes: tuple[str, ...] = CODEX_ENV_PREFIX_DENYLIST

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
            and not any(k.startswith(p) for p in self.denylist_prefixes)
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
    def locate_session(self, session_id: str, codex_home: Path | None = None) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None

        candidates: list[Path] = []
        # 1. Permanent storage (symlink target) — checked first because
        #    ephemeral CODEX_HOME may be cleaned up by the time we search
        candidates.append(default_log_dir() / "codex-sessions")
        # 2. Explicit codex_home or CODEX_HOME env var
        if codex_home is not None:
            candidates.append(codex_home / "sessions")
        else:
            env_home = os.environ.get("CODEX_HOME")
            if env_home:
                candidates.append(Path(env_home) / "sessions")
        # 3. Default Codex home (~/.codex/sessions/)
        candidates.append(Path.home() / ".codex" / "sessions")

        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = str(candidate.resolve()) if candidate.exists() else str(candidate)
            except OSError:
                continue
            if resolved in seen or not candidate.exists():
                continue
            seen.add(resolved)
            result = self._search_tree(candidate, session_id)
            if result is not None:
                return result
        return None

    def _search_tree(self, sessions_dir: Path, thread_id: str) -> Path | None:
        """Walk YYYY/MM/DD/ date tree for rollout-*.jsonl matching thread_id."""
        try:
            year_dirs = sorted(sessions_dir.iterdir(), reverse=True)
        except OSError:
            return None
        for year_dir in year_dirs:
            if not year_dir.is_dir():
                continue
            try:
                month_dirs = sorted(year_dir.iterdir(), reverse=True)
            except OSError:
                continue
            for month_dir in month_dirs:
                if not month_dir.is_dir():
                    continue
                try:
                    day_dirs = sorted(month_dir.iterdir(), reverse=True)
                except OSError:
                    continue
                for day_dir in day_dirs:
                    if not day_dir.is_dir():
                        continue
                    try:
                        entries = list(day_dir.iterdir())
                    except OSError:
                        continue
                    for entry in entries:
                        if (
                            entry.is_file()
                            and entry.name.startswith("rollout-")
                            and entry.name.endswith(".jsonl")
                        ):
                            if self._file_matches_thread(entry, thread_id):
                                return entry
        return None

    @staticmethod
    def _file_matches_thread(path: Path, thread_id: str) -> bool:
        """Check if a rollout NDJSON file's thread.started event matches thread_id.

        Only reads until the first parseable NDJSON object — thread.started is
        always the first event in a Codex rollout file.
        """
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        return False
                    if isinstance(obj, dict) and obj.get("type") == "thread.started":
                        return obj.get("thread_id", "") == thread_id
                    return False
        except OSError:
            return False
        return False

    def read_session(self, path: Path) -> list[dict]:
        """Read and parse a Codex session log file.

        Handles both plain .jsonl (current Codex v0.133.0+) and
        .jsonl.zst (legacy) formats based on file extension.
        """
        try:
            if path.name.endswith(".zst"):
                raw = path.read_bytes()
                decompressed = zstandard.ZstdDecompressor().decompress(raw)
                text = decompressed.decode("utf-8")
            else:
                text = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("read_session: failed to read", path=str(path), exc_info=True)
            return []
        result: list[dict] = []
        for line in text.splitlines():
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                result.append(obj)
        return result


def _validate_codex_config() -> list[str]:
    """Run codex doctor --json and check config.load status."""
    try:
        result = subprocess.run(
            ["codex", "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        logger.warning("codex_doctor_timeout")
        return []
    except OSError:
        logger.warning("codex_doctor_unavailable", exc_info=True)
        return []

    if getattr(result, "returncode", -1) != 0:
        logger.warning("codex_doctor_nonzero_exit", returncode=getattr(result, "returncode", None))
        return []

    stdout = getattr(result, "stdout", None) or ""
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("codex_doctor_json_parse_failed")
        return []

    checks = doc.get("checks", {})
    config_check = checks.get("config.load", {})
    status = config_check.get("status")

    if status is not None and status != "ok":
        summary = config_check.get("summary", "unknown config error")
        remediation = config_check.get("remediation", "")
        parts = [f"Codex config validation failed: {summary}"]
        if remediation:
            parts.append(f"Remediation: {remediation}")
        parts.append("Run 'codex doctor' for full diagnostics.")
        return [" ".join(parts)]

    return []


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
            project_local_skills_capable=False,
            supports_tool_list_changed=False,
            required_skill_fields=frozenset({"name", "description"}),
            required_session_files=frozenset({"config.toml"}),
            session_dir_symlinks=frozenset({"auth.json", ".env", "sessions"}),
            applicable_guards=frozenset(),
            env_denylist_prefixes=CODEX_ENV_PREFIX_DENYLIST,
            min_version="0.130.0",
            version_check_command="codex --version",
            process_name="codex",
            skills_subdir="skills",
            mcp_env_forward_vars=frozenset({MCP_CLIENT_BACKEND_ENV_VAR}),
            replay_capable=False,
            record_capable=False,
            anthropic_provider_capable=False,
            inspector_capable=True,
        )

    @property
    def conventions(self) -> BackendConventions:
        return BackendConventions(
            project_local_skill_search_dirs=(".codex/skills", ".agents/skills"),
        )

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return CmdSpec(cmd=spec.cmd, env=spec.env, cwd=cwd)

    def stream_parser(self, completion_marker: str = "") -> CodexStreamParser:
        return CodexStreamParser(completion_marker=completion_marker)

    def result_parser(self) -> CodexResultParser:
        return CodexResultParser()

    def env_policy(self) -> CodexEnvPolicy:
        return CodexEnvPolicy(denylist_prefixes=self.capabilities.env_denylist_prefixes)

    def session_locator(self) -> CodexSessionLocator:
        return CodexSessionLocator()

    def write_tool_names(self) -> frozenset[str]:
        return frozenset()

    def binary_name(self) -> str:
        return "codex"

    def translate_model(self, model: str) -> str:
        from autoskillit.core import (
            CODEX_MODEL_ALIASES,
            strip_context_window_suffix,
        )

        base = strip_context_window_suffix(model)
        return CODEX_MODEL_ALIASES.get(base, base)

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
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, d]
        cmd.append(prompt)
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        headless_extras: dict[str, str] = {}
        if env_extras:
            headless_extras.update(env_extras)
        headless_extras[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
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
            allowed_write_prefixes = config.allowed_write_prefixes
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
            "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1",
            "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_SKILL,
            "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
            AGENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
            AUTOSKILLIT_APPLICABLE_GUARDS: ",".join(sorted(self.capabilities.applicable_guards)),
            "MCP_CONNECTION_NONBLOCKING": "0",
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
        if allowed_write_prefixes:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] = ":".join(allowed_write_prefixes)
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
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
        if add_dirs:
            extras["CODEX_HOME"] = add_dirs[0].path

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=SKILL_SESSION_REQUIRED_ENV | {MCP_CLIENT_BACKEND_ENV_VAR},
        )

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
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(cmd=tuple(cmd), env=env, cwd=cwd, is_resume=bool(resume_session_id))

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
        allowed_write_prefixes: tuple[str, ...] = (),
        sentinel_contract: str = "",
        resume_message: str | None = None,
    ) -> CmdSpec:
        _plugin_source = plugin_source  # noqa: F841  # no-op: Codex has no --plugin-dir
        _output_format = output_format  # noqa: F841  # no-op: --json is unconditional
        _exit_ms = exit_after_stop_delay_ms  # noqa: F841  # no-op: Claude-only
        _stream_ms = stream_idle_timeout_ms  # noqa: F841  # no-op: Claude-only

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
            AGENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
            AUTOSKILLIT_APPLICABLE_GUARDS: ",".join(sorted(self.capabilities.applicable_guards)),
            "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1",
            "MCP_CONNECTION_NONBLOCKING": "0",
        }
        if scenario_step_name:
            extras["SCENARIO_STEP_NAME"] = scenario_step_name
        campaign_id = os.environ.get(CAMPAIGN_ID_ENV_VAR)
        if campaign_id:
            extras[CAMPAIGN_ID_ENV_VAR] = campaign_id
        kitchen_session_id = os.environ.get(KITCHEN_SESSION_ID_ENV_VAR)
        if kitchen_session_id:
            extras[KITCHEN_SESSION_ID_ENV_VAR] = kitchen_session_id
        if completion_marker:
            extras["AUTOSKILLIT_COMPLETION_MARKER"] = completion_marker
        if allowed_write_prefix:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIX"] = allowed_write_prefix
        if allowed_write_prefixes:
            extras["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] = ":".join(allowed_write_prefixes)
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
        if env_extras:
            for k, v in env_extras.items():
                if k not in ("AUTOSKILLIT_SESSION_TYPE", "AUTOSKILLIT_HEADLESS"):
                    extras[k] = v

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=ORCHESTRATOR_SESSION_REQUIRED_ENV | {MCP_CLIENT_BACKEND_ENV_VAR},
        )

        cmd: list[str] = [
            "codex",
            "exec",
            CodexFlags.JSON,
            CodexFlags.SANDBOX,
            "read-only",
            CodexFlags.ASK_FOR_APPROVAL_SHORT,
            "never",
            CodexFlags.CONFIG_OVERRIDE,
            "web_search=disabled",
        ]
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(cmd=tuple(cmd), env=env, cwd=cwd, is_resume=bool(resume_session_id))

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
        if tools:
            logger.warning(
                "codex_tools_ignored",
                extra={"tools": list(tools)},
            )
        builder = CmdBuilder("codex")
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
        if system_prompt is not None and isinstance(resume_spec, NoResume):
            builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, f"developer_instructions={system_prompt}")
        if initial_prompt is not None:
            builder.positional(initial_prompt)
        for d in add_dirs:
            builder.variadic_pair(CodexFlags.ADD_DIR, str(d))
        base_env = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        merged_extras: dict[str, str] = dict(_SESSION_BASELINE_ENV)
        if env_extras:
            merged_extras.update(env_extras)
        merged_extras[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        if add_dirs:
            merged_extras.setdefault("CODEX_HOME", str(add_dirs[0]))
        effective_required = CODEX_INTERACTIVE_REQUIRED_ENV | (required_env or frozenset())
        env = CodexEnvPolicy().build_env(
            base_env, extras=merged_extras, required=effective_required
        )
        partial = builder.build()
        return CmdSpec(
            cmd=partial.cmd,
            env=env,
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
        if not resume_session_id.strip():
            msg = "resume_session_id must be a non-empty string"
            raise ValueError(msg)
        cmd: list[str] = ["codex", "exec"]
        if output_format == OutputFormat.JSON:
            cmd.append(CodexFlags.JSON)
        cmd.extend([CodexFlags.SANDBOX, "read-only"])
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(prompt)
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        resume_extras: dict[str, str] = dict(_SESSION_BASELINE_ENV)
        if env_extras:
            resume_extras.update(env_extras)
        resume_extras[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        env = self.env_policy().build_env(
            filtered_base,
            extras=resume_extras,
            required=RESUME_SESSION_BASELINE_KEYS | {MCP_CLIENT_BACKEND_ENV_VAR},
        )
        return CmdSpec(cmd=tuple(cmd), env=env, is_resume=True)

    def validate_session_layout(self, session_dir: Path) -> list[str]:
        errors: list[str] = []

        skills_dir = session_dir / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
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

        return errors

    def setup_session_dir(self, session_dir: Path) -> None:
        codex_home_source = Path.home() / ".codex"

        try:
            shutil.copy2(
                codex_home_source / "config.toml",
                session_dir / "config.toml",
            )
        except FileNotFoundError:
            logger.error(
                "codex_config_copy_missing",
                src=str(codex_home_source / "config.toml"),
            )
            raise

        auth_source = codex_home_source / "auth.json"
        auth_dest = session_dir / "auth.json"
        if auth_source.exists():
            try:
                auth_dest.symlink_to(auth_source.resolve())
                logger.debug(
                    "codex_auth_symlink",
                    src=str(auth_source),
                    dest=str(auth_dest),
                )
            except OSError:
                logger.warning("codex_auth_symlink_failed", src=str(auth_source))
        else:
            logger.warning("codex_auth_copy_missing", src=str(auth_source))

        env_source = codex_home_source / ".env"
        if env_source.exists():
            shutil.copy2(env_source, session_dir / ".env")

        sessions_target = default_log_dir() / "codex-sessions"
        sessions_target.mkdir(parents=True, exist_ok=True)
        try:
            (session_dir / "sessions").symlink_to(sessions_target)
        except OSError:
            logger.warning(
                "codex_sessions_symlink_failed", target=str(sessions_target), exc_info=True
            )

    def validate_skill_content(self, content: str) -> list[str]:
        return []

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

    def ensure_pre_launch(self) -> list[str]:
        os.environ[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        try:
            ensure_codex_mcp_registered()
        except Exception as exc:
            logger.warning("codex_mcp_registration_failed", exc_info=True)
            return [f"Failed to ensure MCP registration: {exc}"]

        return _validate_codex_config()

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        raise RuntimeError("build_inspector_cmd not yet implemented — lands in #3534")
