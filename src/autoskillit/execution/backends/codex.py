"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path

import zstandard

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    CAMPAIGN_ID_ENV_VAR,
    KITCHEN_SESSION_ID_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    BackendCapabilities,
    BareResume,
    CmdSpec,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    SkillSessionConfig,
    ValidatedAddDir,
    extract_skill_name,
    get_logger,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    _inject_completion_directive,
    _inject_completion_reminder,
    _inject_cwd_anchor,
    _inject_narration_suppression,
)
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
            and not any(k.startswith(p) for p in CODEX_ENV_PREFIX_DENYLIST)
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
        if codex_home is not None:
            sessions_dir = codex_home / "sessions"
        else:
            env_home = os.environ.get("CODEX_HOME")
            if env_home:
                sessions_dir = Path(env_home) / "sessions"
            else:
                sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.exists():
            return None
        for year_dir in sessions_dir.iterdir():
            if not year_dir.is_dir():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir():
                        continue
                    for entry in day_dir.iterdir():
                        if entry.is_file() and entry.name == f"{session_id}.jsonl.zst":
                            return entry
        return None

    def read_session(self, path: Path) -> list[dict]:
        try:
            raw = path.read_bytes()
            decompressed = zstandard.ZstdDecompressor().decompress(raw)
            text = decompressed.decode("utf-8")
        except Exception:
            logger.warning("read_session: failed to decompress", path=str(path), exc_info=True)
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
            session_record_types=frozenset(),
        )

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        spec = self.build_headless_cmd(skill_command)
        return CmdSpec(cmd=spec.cmd, env=spec.env, cwd=cwd)

    def stream_parser(self, completion_marker: str = "") -> CodexStreamParser:
        return CodexStreamParser(completion_marker=completion_marker)

    def result_parser(self) -> CodexResultParser:
        return CodexResultParser()

    def env_policy(self) -> CodexEnvPolicy:
        return CodexEnvPolicy()

    def session_locator(self) -> CodexSessionLocator:
        return CodexSessionLocator()

    def write_tool_names(self) -> frozenset[str]:
        return frozenset()

    def binary_name(self) -> str:
        return "codex"

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
            cmd += [CodexFlags.MODEL, model]
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, d]
        cmd.append(prompt)
        base: dict[str, str] = dict(os.environ)
        if env_extras:
            base.update(env_extras)
        env = self.env_policy().build_env(base)
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
            "AUTOSKILLIT_SESSION_TYPE": SESSION_TYPE_SKILL,
            "MAX_MCP_OUTPUT_TOKENS": _MAX_MCP_OUTPUT_TOKENS_VALUE,
            AGENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
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
        env = CodexEnvPolicy().build_env(filtered_base, extras=extras)

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
            cmd += [CodexFlags.MODEL, model]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(cmd=tuple(cmd), env=env, cwd=cwd)

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
        if cwd:
            extras["AUTOSKILLIT_CWD"] = cwd
        if env_extras:
            for k, v in env_extras.items():
                if k not in ("AUTOSKILLIT_SESSION_TYPE", "AUTOSKILLIT_HEADLESS"):
                    extras[k] = v

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(filtered_base, extras=extras)

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
            cmd += [CodexFlags.MODEL, model]
        if resume_session_id:
            cmd.append(CodexFlags.RESUME_SUBCOMMAND)
            cmd.append(resume_session_id)
        cmd.append(prompt)

        return CmdSpec(cmd=tuple(cmd), env=env, cwd=cwd)

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
        cmd: list[str] = []
        match resume_spec:
            case NoResume():
                cmd = ["codex", CodexFlags.DANGEROUSLY_BYPASS]
            case NamedResume(session_id=sid):
                cmd = ["codex", CodexFlags.RESUME_SUBCOMMAND, sid, CodexFlags.DANGEROUSLY_BYPASS]
            case BareResume():
                cmd = ["codex", CodexFlags.RESUME_SUBCOMMAND, CodexFlags.DANGEROUSLY_BYPASS]
        if model:
            cmd += [CodexFlags.MODEL, model]
        # plugin_source: explicit no-op for Codex
        for d in add_dirs:
            cmd += [CodexFlags.ADD_DIR, str(d)]
        if system_prompt is not None and isinstance(resume_spec, NoResume):
            cmd += [CodexFlags.CONFIG_OVERRIDE, f"developer_instructions={system_prompt}"]
        if initial_prompt is not None:
            cmd.append(initial_prompt)
        base_env = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        merged_extras: dict[str, str] = dict(env_extras) if env_extras else {}
        if add_dirs:
            merged_extras.setdefault("CODEX_HOME", str(add_dirs[0]))
        env = CodexEnvPolicy().build_env(base_env, extras=merged_extras, required=required_env)
        return CmdSpec(cmd=tuple(cmd), env=env)

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
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(prompt)
        base: dict[str, str] = dict(os.environ)
        if env_extras:
            base.update(env_extras)
        env = self.env_policy().build_env(base)
        return CmdSpec(cmd=tuple(cmd), env=env)
