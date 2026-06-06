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
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    AUTOSKILLIT_APPLICABLE_GUARDS,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES,
    CAMPAIGN_ID_ENV_VAR,
    CODEX_EFFORT_MAPPING,
    CODEX_INTERACTIVE_REQUIRED_ENV,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MODEL_ALIASES,
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
    CodexEventType,
    NamedResume,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionCheckpoint,
    SkillSessionConfig,
    ValidatedAddDir,
    atomic_write,
    default_log_dir,
    extract_skill_name,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.execution.backends._claude_prompt import (
    _HEADLESS_EXCLUSIVE_VARS,
    _MAX_MCP_OUTPUT_TOKENS_VALUE,
    _PROVIDER_EXTRAS_BASE_DENYLIST,
    _SESSION_BASELINE_ENV,
    _SKILL_SESSION_EXTRAS_DENYLIST,
    PromptBuildContext,
    _compose_resume_prompt,
    _ensure_skill_prefix,
    apply_prompt_injector_chain,
)
from autoskillit.execution.backends._cmd_builder import CmdBuilder
from autoskillit.execution.backends._codex_config import (
    _format_toml_value,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_hooks import (
    sync_hooks_to_codex_config,
)
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser

__all__ = [
    "CODEX_EXEC_FLAGS",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexSessionLocator",
    "NON_VARIADIC_CODEX_FLAGS",
    "VARIADIC_CODEX_FLAGS",
    "ensure_codex_mcp_registered",
]

logger = get_logger(__name__)


@unique
class CodexFlags(StrEnum):
    JSON = "--json"
    SANDBOX = "--sandbox"
    MODEL = "--model"
    MODEL_SHORT = "-m"
    ADD_DIR = "--add-dir"
    RESUME_SUBCOMMAND = "resume"
    CONFIG_OVERRIDE = "-c"
    DANGEROUSLY_BYPASS = "--dangerously-bypass-approvals-and-sandbox"
    DANGEROUSLY_BYPASS_HOOK_TRUST = "--dangerously-bypass-hook-trust"


CODEX_EXEC_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.JSON,
        CodexFlags.SANDBOX,
        CodexFlags.MODEL,
        CodexFlags.CONFIG_OVERRIDE,
        CodexFlags.ADD_DIR,
        CodexFlags.DANGEROUSLY_BYPASS_HOOK_TRUST,
    }
)

CODEX_TOP_LEVEL_ONLY_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.DANGEROUSLY_BYPASS,
        CodexFlags.MODEL_SHORT,
    }
)

VARIADIC_CODEX_FLAGS: frozenset[str] = frozenset({CodexFlags.ADD_DIR, CodexFlags.CONFIG_OVERRIDE})

NON_VARIADIC_CODEX_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.JSON,
        CodexFlags.SANDBOX,
        CodexFlags.MODEL,
        CodexFlags.MODEL_SHORT,
        CodexFlags.RESUME_SUBCOMMAND,
        CodexFlags.DANGEROUSLY_BYPASS,
        CodexFlags.DANGEROUSLY_BYPASS_HOOK_TRUST,
    }
)


CODEX_ENV_DENYLIST: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_STREAM_IDLE_TIMEOUT_MS",
    }
)

CODEX_ENV_PREFIX_DENYLIST: tuple[str, ...] = ("CLAUDE_CODE_",)

_IMAGE_GENERATION_DISABLED = "features.image_generation=false"


def _codex_exec_base(
    *,
    sandbox: str,
    json: bool = True,
    extra_overrides: Sequence[str] = (),
    bypass_hook_trust: bool = False,
) -> list[str]:
    cmd: list[str] = ["codex", "exec"]
    if json:
        cmd.append(CodexFlags.JSON)
    cmd.extend([CodexFlags.SANDBOX, sandbox])
    for override in extra_overrides:
        cmd.extend([CodexFlags.CONFIG_OVERRIDE, override])
    cmd.extend([CodexFlags.CONFIG_OVERRIDE, _IMAGE_GENERATION_DISABLED])
    if bypass_hook_trust:
        # Safe: --sandbox workspace-write already restricts filesystem writes.
        cmd.append(CodexFlags.DANGEROUSLY_BYPASS_HOOK_TRUST)
    return cmd


def _codex_exec_extras(
    *,
    session_type: str,
    include_session_baseline: bool = False,
    include_agent_backend_flat: bool = False,
    applicable_guards: frozenset[str] | None = None,
    write_guard_tool_names: frozenset[str] | None = None,
) -> dict[str, str]:
    extras: dict[str, str] = {}
    if include_session_baseline:
        extras.update(_SESSION_BASELINE_ENV)
    extras.update(
        {
            "AUTOSKILLIT_HEADLESS": "1",
            "AUTOSKILLIT_HEADLESS_AUTO_GATE": "1",
            "AUTOSKILLIT_SESSION_TYPE": session_type,
            AGENT_BACKEND_DYNACONF_ENV_VAR: AGENT_BACKEND_CODEX,
            MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
        }
    )
    if include_agent_backend_flat:
        extras[AGENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
    if applicable_guards is not None:
        extras[AUTOSKILLIT_APPLICABLE_GUARDS] = ",".join(sorted(applicable_guards))
    if write_guard_tool_names is not None:
        extras[AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES] = ",".join(sorted(write_guard_tool_names))
    return extras


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


_SESSION_START_TYPES: frozenset[str] = frozenset(
    {
        CodexEventType.THREAD_STARTED.value,
        CodexEventType.SESSION_META.value,
    }
)


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
        """Check if a rollout NDJSON file's session-start event matches thread_id.

        Only reads until the first parseable NDJSON object — the session-start
        event is always the first event in a Codex rollout file.
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
                    if isinstance(obj, dict) and obj.get("type") in _SESSION_START_TYPES:
                        # thread.started uses top-level thread_id;
                        # session_meta uses payload.id
                        tid = obj.get("thread_id") or obj.get("payload", {}).get("id", "")
                        return tid == thread_id
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


def _generate_agent_tomls(session_dir: Path) -> int:
    agents_src = pkg_root() / "agents"
    out_dir = session_dir / "agents"
    out_dir.mkdir(exist_ok=True)
    count = 0
    for md_path in sorted(agents_src.glob("*.md")):
        if md_path.name == "CLAUDE.md":
            continue
        content = md_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            logger.warning("agent_toml_skip_no_frontmatter", path=str(md_path))
            continue
        parts = content.split("---", 2)
        if len(parts) < 3:
            logger.warning("agent_toml_skip_no_frontmatter", path=str(md_path))
            continue
        meta = load_yaml(parts[1])
        if not isinstance(meta, dict):
            logger.warning("agent_toml_skip_invalid_frontmatter", path=str(md_path))
            continue
        body = parts[2].strip()
        if not body:
            logger.warning("agent_toml_skip_empty_body", path=str(md_path))
            continue
        if "'''" in body:
            logger.warning("agent_toml_skip_triple_quote", path=str(md_path))
            continue
        name = meta.get("name")
        if not name:
            logger.warning("agent_toml_skip_missing_name", path=str(md_path))
            continue
        desc = meta.get("description")
        if not desc:
            logger.warning("agent_toml_skip_missing_description", path=str(md_path))
            continue
        lines = [
            f"name = {_format_toml_value(name)}",
            f"description = {_format_toml_value(desc)}",
            'sandbox_mode = "workspace-write"',
        ]
        model_key = meta.get("model")
        if model_key and model_key in CODEX_MODEL_ALIASES:
            lines.append(f"model = {_format_toml_value(CODEX_MODEL_ALIASES[model_key])}")
            effort = CODEX_EFFORT_MAPPING.get(model_key)
            if effort:
                lines.append(f"model_reasoning_effort = {_format_toml_value(effort)}")
        lines.append(f"developer_instructions = '''\n{body}\n'''")
        atomic_write(out_dir / f"{name}.toml", "\n".join(lines) + "\n")
        count += 1
    logger.debug("codex_agents_generated", count=count, dest=str(out_dir))
    return count


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
            applicable_guards=frozenset({"write_guard"}),
            # Codex uses run_cmd instead of Write/Edit — those tools don't exist in Codex
            write_guard_tool_names=frozenset({"apply_patch", "Bash", "run_cmd"}),
            env_denylist_prefixes=CODEX_ENV_PREFIX_DENYLIST,
            min_version="0.130.0",
            version_check_command="codex --version",
            process_name="codex",
            skills_subdir="skills",
            hook_config_format="toml_nested",
            write_detection_strategy="file_changes",
            patch_format="codex_star_update",
            default_skill_sandbox_mode="workspace-write",
            mcp_env_forward_vars=CODEX_MCP_ENV_FORWARD_VARS,
            replay_capable=False,
            record_capable=False,
            anthropic_provider_capable=False,
            inspector_capable=False,
            has_unguarded_filesystem_access=True,
            git_metadata_writable=False,
            skill_sigil="$",
        )

    @property
    def conventions(self) -> BackendConventions:
        return BackendConventions(
            skills_subdir=ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR,
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
        if env_extras:
            headless_extras.update(env_extras)
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
        sandbox_mode: str = "workspace-write",
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
            sandbox_mode = config.sandbox_mode
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
            ),
        )

        extras = _codex_exec_extras(
            session_type=SESSION_TYPE_SKILL,
            include_agent_backend_flat=True,
            applicable_guards=self.capabilities.applicable_guards,
            write_guard_tool_names=self.capabilities.write_guard_tool_names,
        )
        extras["MAX_MCP_OUTPUT_TOKENS"] = _MAX_MCP_OUTPUT_TOKENS_VALUE
        extras["MCP_CONNECTION_NONBLOCKING"] = "0"
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
                if k not in _SKILL_SESSION_EXTRAS_DENYLIST:
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

        cmd = _codex_exec_base(
            sandbox=sandbox_mode,
            bypass_hook_trust=self.capabilities.mcp_config_capable,
        )
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
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

        prompt = apply_prompt_injector_chain(
            effective_prompt,
            PromptBuildContext(
                completion_marker=completion_marker,
                cwd=cwd,
                temp_dir_relpath=temp_dir_relpath,
                has_skill_prefix=False,
                profile_name="",
            ),
        )

        extras = _codex_exec_extras(
            session_type=SESSION_TYPE_ORCHESTRATOR,
            include_agent_backend_flat=True,
            applicable_guards=self.capabilities.applicable_guards,
            write_guard_tool_names=self.capabilities.write_guard_tool_names,
        )
        extras["MAX_MCP_OUTPUT_TOKENS"] = _MAX_MCP_OUTPUT_TOKENS_VALUE
        extras["MCP_CONNECTION_NONBLOCKING"] = "0"
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
                if k not in _PROVIDER_EXTRAS_BASE_DENYLIST:
                    extras[k] = v

        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        env = CodexEnvPolicy().build_env(
            filtered_base,
            extras=extras,
            required=ORCHESTRATOR_SESSION_REQUIRED_ENV | {MCP_CLIENT_BACKEND_ENV_VAR},
        )

        cmd = _codex_exec_base(
            sandbox="read-only",
            extra_overrides=["web_search=disabled"],
            bypass_hook_trust=self.capabilities.mcp_config_capable,
        )
        if model:
            cmd += [CodexFlags.MODEL, self.translate_model(model)]
            for override in self.model_config_overrides(model):
                cmd += [CodexFlags.CONFIG_OVERRIDE, override]
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
            for override in self.model_config_overrides(model):
                builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, override)
        builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, _IMAGE_GENERATION_DISABLED)
        if system_prompt is not None and isinstance(resume_spec, NoResume):
            builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, f"developer_instructions={system_prompt}")
        if initial_prompt is not None:
            builder.positional(initial_prompt)
        for d in add_dirs:
            builder.variadic_pair(CodexFlags.ADD_DIR, str(d))
        base_env = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        merged_extras: dict[str, str] = dict(_SESSION_BASELINE_ENV)
        merged_extras.update(
            {
                "AUTOSKILLIT_HEADLESS": "",
                "AUTOSKILLIT_HEADLESS_AUTO_GATE": "",
                "AUTOSKILLIT_SESSION_TYPE": "",
                AGENT_BACKEND_DYNACONF_ENV_VAR: AGENT_BACKEND_CODEX,
                MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
                FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
            }
        )
        if env_extras:
            merged_extras.update(env_extras)
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
        cmd = _codex_exec_base(sandbox="read-only", json=(output_format == OutputFormat.JSON))
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(prompt)
        filtered_base = {k: v for k, v in os.environ.items() if k not in _HEADLESS_EXCLUSIVE_VARS}
        resume_extras = _codex_exec_extras(session_type="", include_session_baseline=True)
        if env_extras:
            resume_extras.update(env_extras)
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

        try:
            _generate_agent_tomls(session_dir)
        except Exception:
            logger.warning("codex_agent_toml_generation_failed", exc_info=True)

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
        errors: list[str] = []
        try:
            ensure_codex_mcp_registered()
        except Exception as exc:
            logger.warning("codex_mcp_registration_failed", exc_info=True)
            errors.append(f"Failed to ensure MCP registration: {exc}")

        try:
            sync_hooks_to_codex_config(
                hook_config_format=self.capabilities.hook_config_format,
            )
        except Exception as exc:
            logger.warning("codex_hook_sync_failed", exc_info=True)
            errors.append(f"Failed to sync hooks to Codex config: {exc}")

        errors.extend(_validate_codex_config())
        return errors

    def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
        raise RuntimeError("build_inspector_cmd not yet implemented — lands in #3534")
