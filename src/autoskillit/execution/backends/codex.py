"""Codex/OpenAI backend implementation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import shutil
import signal
import sqlite3
import stat
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
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
    CODEX_COOK_RESERVED_ENV_VARS,
    CODEX_EFFORT_MAPPING,
    CODEX_INTERACTIVE_REQUIRED_ENV,
    CODEX_MCP_ENV_FORWARD_VARS,
    CODEX_MODEL_ALIASES,
    CODEX_SESSIONS_SUBDIR,
    CODEX_STARTUP_TRACE_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    ORCHESTRATOR_SESSION_REQUIRED_ENV,
    PROVIDER_PROFILE_ENV_VAR,
    RESUME_SESSION_BASELINE_KEYS,
    SESSION_ADD_DIR_SUBDIR,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    SKILL_SESSION_REQUIRED_ENV,
    BackendCapabilities,
    BackendConventions,
    BareResume,
    CapabilityNotSupportedError,
    ClaudeDirectoryConventions,
    CmdSpec,
    CookSessionHandle,
    ExecutableLaunchBinding,
    HookTrustPolicy,
    ManagedHeadlessSessionLineageRef,
    NamedResume,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NoResume,
    ObserverStatus,
    OutputFormat,
    PluginLaunchBinding,
    ResumeSpec,
    SessionCheckpoint,
    SessionLocator,
    SessionSummary,
    SkillSemanticAdaptationResult,
    SkillSemanticPlan,
    SkillSessionConfig,
    ValidatedAddDir,
    atomic_write,
    default_log_dir,
    extract_skill_name,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    BackendCmdBuilderBase,
    FlagVocabulary,
    _filter_protected_native_shell_env,
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
from autoskillit.execution.backends._codex_config import (
    CODEX_RECIPE_DELIVERY_BUDGET,
    _format_toml_value,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser
from autoskillit.execution.backends._codex_prelaunch import codex_prelaunch_transaction
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore


def _codex_home_from_plugin_binding(
    plugin_binding: PluginLaunchBinding | None,
) -> str | None:
    if plugin_binding is None or plugin_binding.plugin_dir is None:
        return None
    return str(plugin_binding.plugin_dir)


__all__ = [
    "CODEX_EXEC_FLAGS",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "CodexBackend",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexSessionLocator",
    "CodexStateReadinessProbe",
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
    PROFILE = "--profile"
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
        CodexFlags.PROFILE,
    }
)

VARIADIC_CODEX_FLAGS: frozenset[str] = frozenset({CodexFlags.ADD_DIR, CodexFlags.CONFIG_OVERRIDE})

NON_VARIADIC_CODEX_FLAGS: frozenset[str] = frozenset(
    {
        CodexFlags.JSON,
        CodexFlags.SANDBOX,
        CodexFlags.MODEL,
        CodexFlags.MODEL_SHORT,
        CodexFlags.PROFILE,
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
_CODEX_HOME_ENV_VAR = "CODEX_HOME"
_CODEX_SQLITE_HOME_ENV_VAR = "CODEX_SQLITE_HOME"


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


def _should_bypass_hook_trust(
    policy: HookTrustPolicy,
    *,
    automated_session: bool,
) -> bool:
    """Translate backend hook policy at the command-construction boundary."""
    if automated_session:
        return True
    match policy:
        case HookTrustPolicy.AUTOMATED:
            return True
        case HookTrustPolicy.REVIEW_EACH_SESSION:
            return False
    raise AssertionError(f"Unhandled hook trust policy: {policy!r}")


_CODEX_STATE_READINESS_COMMIT = "ad65f016ed0c91992fb175fa881a373cc460dd2a"


@dataclass(frozen=True, slots=True)
class _StateReadinessDef:
    database_name: str
    upstream_commit: str


_SUPPORTED_STATE_CONTRACTS = {
    "codex-cli 0.145.0": _StateReadinessDef(
        database_name="state_5.sqlite",
        upstream_commit=_CODEX_STATE_READINESS_COMMIT,
    )
}


@dataclass(frozen=True, slots=True)
class CodexStateReadinessProbe:
    """Read the version-mapped disposable Codex state database without mutation."""

    codex_version: str
    sqlite_home: Path
    poll_interval_seconds: float = 0.05
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be finite and positive")
        object.__setattr__(self, "sqlite_home", Path(self.sqlite_home))

    @property
    def database_path(self) -> Path | None:
        """Return the exact database path for a supported Codex version."""
        compatibility = _SUPPORTED_STATE_CONTRACTS.get(self.codex_version)
        return None if compatibility is None else self.sqlite_home / compatibility.database_name

    @property
    def upstream_commit(self) -> str | None:
        """Return the source revision defining the probed schema contract."""
        compatibility = _SUPPORTED_STATE_CONTRACTS.get(self.codex_version)
        return None if compatibility is None else compatibility.upstream_commit

    def check(self) -> ObserverStatus:
        """Perform one zero-wait, read-only readiness observation."""
        database_path = self.database_path
        if database_path is None:
            return ObserverStatus.UNSUPPORTED_VERSION
        try:
            path_stat = database_path.lstat()
        except FileNotFoundError:
            return ObserverStatus.ABSENT
        except OSError:
            return ObserverStatus.CORRUPT
        if not stat.S_ISREG(path_stat.st_mode):
            return ObserverStatus.CORRUPT

        connection: sqlite3.Connection | None = None
        try:
            uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=0.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 0")
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(backfill_state)")
                if len(row) > 1 and isinstance(row[1], str)
            }
            if not {"id", "status"}.issubset(columns):
                return ObserverStatus.SCHEMA_CHANGED
            row = connection.execute("SELECT status FROM backfill_state WHERE id = 1").fetchone()
            if row is None or len(row) != 1 or not isinstance(row[0], str):
                return ObserverStatus.INCOMPLETE
            return ObserverStatus.READY if row[0] == "complete" else ObserverStatus.INCOMPLETE
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                return ObserverStatus.LOCKED
            if "no such table" in message or "no such column" in message:
                return ObserverStatus.SCHEMA_CHANGED
            return ObserverStatus.CORRUPT
        except (OSError, sqlite3.DatabaseError, ValueError):
            return ObserverStatus.CORRUPT
        finally:
            if connection is not None:
                connection.close()

    def wait(
        self,
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> ObserverStatus:
        """Poll until ready, a terminal adapter failure, timeout, or cancellation."""
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        is_cancelled = cancelled or (lambda: False)
        deadline = self._clock() + timeout_seconds
        while True:
            if is_cancelled():
                return ObserverStatus.CANCELLED
            if self._clock() >= deadline:
                return ObserverStatus.TIMEOUT
            status = self.check()
            if status is ObserverStatus.READY:
                return status
            if status in {
                ObserverStatus.CORRUPT,
                ObserverStatus.SCHEMA_CHANGED,
                ObserverStatus.UNSUPPORTED_VERSION,
            }:
                return status
            remaining = deadline - self._clock()
            if remaining <= 0:
                return ObserverStatus.TIMEOUT
            self._sleep(min(self.poll_interval_seconds, remaining))


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
        extras.update(SHARED_BASELINE_ENV)
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
            filtered_extras = _filter_protected_native_shell_env(extras)
            out.update(
                {
                    key: value
                    for key, value in filtered_extras.items()
                    if key != CODEX_STARTUP_TRACE_ENV_VAR
                }
            )
        # This is an outer-cook control signal, never child or nested-session state.
        out.pop(CODEX_STARTUP_TRACE_ENV_VAR, None)
        if required is not None:
            missing = required - frozenset(out)
            if missing:
                raise ValueError(f"Required env vars missing from session env: {sorted(missing)}")
        return out


@dataclass(frozen=True, slots=True)
class CodexSessionLocator(SessionLocator):
    store_root: Path | None = None
    index_path: Path | None = None

    def _store(self) -> CodexSessionStore:
        return CodexSessionStore(
            log_dir=self.store_root or default_log_dir(),
            index_path=self.index_path,
        )

    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        return self._store().locate_session(session_id)

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

    def project_log_dir(self, cwd: str) -> Path:  # cwd unused; Codex uses a global session store
        return (self.store_root or default_log_dir()) / CODEX_SESSIONS_SUBDIR

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        return self.locate_session(session_id)

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        return self._store().read_index(cwd)


_CODEX_PROBE_TIMEOUT_SECONDS = 15.0
_CODEX_PROBE_STREAM_LIMIT = 64 * 1024
_CODEX_VALIDATION_CACHE_LIMIT = 128
_CODEX_VALIDATION_CACHE: dict[str, None] = {}
_CODEX_VALIDATION_CACHE_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class _BoundedProbeResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    failure: str | None = None


def _terminate_probe(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _run_bounded_codex_probe(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
) -> _BoundedProbeResult:
    """Run a normal Codex config-load probe with hard time and byte bounds."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return _BoundedProbeResult(
            returncode=None,
            stdout=b"",
            stderr=b"",
            failure=f"binary unavailable ({type(exc).__name__})",
        )

    selector: selectors.BaseSelector | None = None
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + _CODEX_PROBE_TIMEOUT_SECONDS
    try:
        assert process.stdout is not None
        assert process.stderr is not None
        selector_factory = selectors.DefaultSelector
        selector = selector_factory()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_probe(process)
                return _BoundedProbeResult(
                    returncode=None,
                    stdout=bytes(output["stdout"]),
                    stderr=bytes(output["stderr"]),
                    failure="timed out",
                )
            if not selector.get_map():
                time.sleep(min(0.01, remaining))
                continue
            events = selector.select(timeout=min(0.1, remaining))
            for key, _ in events:
                stream_name = key.data
                try:
                    file_descriptor = (
                        key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
                    )
                    chunk = os.read(file_descriptor, 8192)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = output[stream_name]
                target.extend(chunk)
                if len(target) > _CODEX_PROBE_STREAM_LIMIT:
                    del target[_CODEX_PROBE_STREAM_LIMIT:]
                    _terminate_probe(process)
                    return _BoundedProbeResult(
                        returncode=None,
                        stdout=bytes(output["stdout"]),
                        stderr=bytes(output["stderr"]),
                        failure=f"{stream_name} exceeded {_CODEX_PROBE_STREAM_LIMIT} bytes",
                    )
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _terminate_probe(process)
        return _BoundedProbeResult(
            returncode=None,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            failure="timed out while reaping",
        )
    except BaseException:
        _terminate_probe(process)
        raise
    finally:
        if selector is not None:
            selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return _BoundedProbeResult(
        returncode=returncode,
        stdout=bytes(output["stdout"]),
        stderr=bytes(output["stderr"]),
    )


def _probe_diagnostic(result: _BoundedProbeResult) -> str:
    """Return bounded, non-content diagnostics safe for configs containing secrets."""
    stdout_digest = hashlib.sha256(result.stdout).hexdigest()[:16]
    stderr_digest = hashlib.sha256(result.stderr).hexdigest()[:16]
    return (
        f"stdout_bytes={len(result.stdout)} stdout_sha256={stdout_digest} "
        f"stderr_bytes={len(result.stderr)} stderr_sha256={stderr_digest}"
    )


def _mcp_inventory_entries(document: Any) -> list[dict[str, Any]] | None:
    if isinstance(document, list):
        return [entry for entry in document if isinstance(entry, dict)]
    if not isinstance(document, dict):
        return None
    for key in ("servers", "mcp_servers"):
        value = document.get(key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, dict):
            return [
                {"name": name, **entry}
                for name, entry in value.items()
                if isinstance(name, str) and isinstance(entry, dict)
            ]
    return None


def _string_array(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return value


def _validate_codex_mcp_inventory(stdout: bytes, config_bytes: bytes) -> list[str]:
    try:
        document = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["Codex MCP validation returned malformed JSON"]
    try:
        config = tomllib.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ["Final Codex config bytes are not valid UTF-8 TOML"]

    expected = config.get("mcp_servers", {}).get("autoskillit")
    if not isinstance(expected, dict):
        return ["Final Codex config is missing mcp_servers.autoskillit"]
    entries = _mcp_inventory_entries(document)
    if entries is None:
        return ["Codex MCP validation JSON has no server inventory"]
    matches = [entry for entry in entries if entry.get("name") == "autoskillit"]
    if len(matches) != 1:
        return [
            "Codex MCP validation expected exactly one enabled autoskillit server; "
            f"found {len(matches)}"
        ]
    actual = matches[0]
    if actual.get("enabled") is False:
        return ["Codex MCP validation reports autoskillit as disabled"]
    transport = actual.get("transport")
    if not isinstance(transport, dict):
        transport = actual
    errors: list[str] = []
    if transport.get("type", "stdio") != "stdio":
        errors.append("Codex MCP autoskillit transport is not stdio")
    if transport.get("command") != expected.get("command"):
        errors.append("Codex MCP autoskillit command does not match final config")
    expected_args = _string_array(expected.get("args", []))
    actual_args = _string_array(transport.get("args", []))
    if expected_args is None:
        errors.append("Final Codex config autoskillit args are not an array of strings")
    if actual_args is None:
        errors.append("Codex MCP autoskillit args are not an array of strings")
    elif expected_args is not None and actual_args != expected_args:
        errors.append("Codex MCP autoskillit args do not match final config")
    expected_env_vars = _string_array(expected.get("env_vars", []))
    actual_env_vars = _string_array(transport.get("env_vars", []))
    if expected_env_vars is None:
        errors.append("Final Codex config autoskillit env_vars are not an array of strings")
    if actual_env_vars is None:
        errors.append("Codex MCP autoskillit env_vars are not an array of strings")
    elif expected_env_vars is not None and set(actual_env_vars) != set(expected_env_vars):
        errors.append("Codex MCP autoskillit env_vars do not match final config")
    for key in ("startup_timeout_sec", "tool_timeout_sec"):
        if key in expected and actual.get(key) != expected[key]:
            errors.append(f"Codex MCP autoskillit {key} does not match final config")
    return errors


def _validation_digest(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
    config_bytes: bytes,
) -> str:
    digest = hashlib.sha256()
    for value in command:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    for key, value in sorted(env.items()):
        digest.update(key.encode("utf-8"))
        digest.update(b"=")
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(cwd.encode("utf-8"))
    digest.update(b"\0")
    digest.update(config_bytes)
    return digest.hexdigest()


def _is_cached_validation(digest: str) -> bool:
    with _CODEX_VALIDATION_CACHE_GUARD:
        if digest not in _CODEX_VALIDATION_CACHE:
            return False
        _CODEX_VALIDATION_CACHE[digest] = _CODEX_VALIDATION_CACHE.pop(digest)
        return True


def _cache_validation(digest: str) -> None:
    with _CODEX_VALIDATION_CACHE_GUARD:
        _CODEX_VALIDATION_CACHE.pop(digest, None)
        _CODEX_VALIDATION_CACHE[digest] = None
        while len(_CODEX_VALIDATION_CACHE) > _CODEX_VALIDATION_CACHE_LIMIT:
            del _CODEX_VALIDATION_CACHE[next(iter(_CODEX_VALIDATION_CACHE))]


def _validate_mcp_probe(
    command: tuple[str, ...],
    *,
    env: Mapping[str, str],
    cwd: str,
    config_bytes: bytes,
) -> list[str]:
    digest = _validation_digest(command, env=env, cwd=cwd, config_bytes=config_bytes)
    if _is_cached_validation(digest):
        return []
    result = _run_bounded_codex_probe(command, env=env, cwd=cwd)
    if result.failure is not None:
        return [f"Codex MCP validation {result.failure}; {_probe_diagnostic(result)}"]
    if result.returncode != 0:
        return [
            f"Codex MCP validation exited with status {result.returncode}; "
            f"{_probe_diagnostic(result)}"
        ]
    errors = _validate_codex_mcp_inventory(result.stdout, config_bytes)
    if errors:
        diagnostic = _probe_diagnostic(result)
        return [f"{error}; {diagnostic}" for error in errors]
    _cache_validation(digest)
    return []


def _validate_global_codex_home(
    source_codex_home: Path,
    *,
    config_path: Path,
) -> list[str]:
    try:
        config_bytes = config_path.read_bytes()
    except OSError as exc:
        return [f"Failed to read final Codex config: {type(exc).__name__}: {exc}"]
    sqlite_override = f"sqlite_home={_format_toml_value(str(source_codex_home))}"
    command = (
        "codex",
        CodexFlags.CONFIG_OVERRIDE,
        sqlite_override,
        "mcp",
        "list",
        CodexFlags.JSON,
    )
    env = dict(os.environ)
    for key in CODEX_COOK_RESERVED_ENV_VARS:
        env[key] = str(source_codex_home)
    return _validate_mcp_probe(
        command,
        env=env,
        cwd=str(source_codex_home),
        config_bytes=config_bytes,
    )


def _validate_inert_rollout_paths(
    generated_home: Path,
) -> tuple[list[str], tuple[tuple[str, str, int, int], ...]]:
    errors: list[str] = []
    fingerprint: list[tuple[str, str, int, int]] = []
    for name in ("sessions", "archived_sessions"):
        public_path = generated_home / name
        if not public_path.is_symlink():
            errors.append(f"{public_path} must be an inert pre-view symlink")
            continue
        try:
            target = public_path.resolve(strict=True)
            stat = target.stat()
        except OSError as exc:
            errors.append(f"{public_path} has an invalid target: {type(exc).__name__}: {exc}")
            continue
        if not target.is_relative_to(generated_home):
            errors.append(f"{public_path} escapes the generated home")
            continue
        if not target.is_dir():
            errors.append(f"{public_path} target is not a directory")
            continue
        try:
            entries = list(target.iterdir())
        except OSError as exc:
            errors.append(f"{public_path} target is unreadable: {type(exc).__name__}: {exc}")
            continue
        if entries:
            errors.append(f"{public_path} inert target is not empty")
        fingerprint.append((name, os.readlink(public_path), stat.st_dev, stat.st_ino))
    return errors, tuple(fingerprint)


_READ_ONLY_AGENT_TOOLS = frozenset({"Read", "Grep", "Glob"})


def _canonical_codex_model_effort(
    model_class: str | None,
    reasoning_effort: str | None = None,
) -> tuple[str, str | None]:
    """Translate the one canonical semantic policy used by agents and call sites."""
    if model_class is None:
        return "", reasoning_effort
    return (
        CODEX_MODEL_ALIASES[model_class],
        reasoning_effort or CODEX_EFFORT_MAPPING.get(model_class),
    )


def _generate_agent_tomls(session_dir: Path) -> int:
    agents_src = pkg_root() / "agents"
    out_dir = session_dir / "agents"
    out_dir.mkdir(exist_ok=True)
    count = 0
    for md_path in sorted(agents_src.glob("*.md")):
        if md_path.name in ("CLAUDE.md", "AGENTS.md"):
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
        declared_tools = meta.get("tools")
        sandbox_mode = "workspace-write"
        if (
            isinstance(declared_tools, list)
            and declared_tools
            and all(isinstance(tool, str) for tool in declared_tools)
            and set(declared_tools) <= _READ_ONLY_AGENT_TOOLS
        ):
            sandbox_mode = "read-only"
        lines = [
            f"name = {_format_toml_value(name)}",
            f"description = {_format_toml_value(desc)}",
            f"sandbox_mode = {_format_toml_value(sandbox_mode)}",
        ]
        model_key = meta.get("model")
        if model_key and model_key in CODEX_MODEL_ALIASES:
            physical_model, effort = _canonical_codex_model_effort(model_key)
            lines.append(f"model = {_format_toml_value(physical_model)}")
            if effort:
                lines.append(f"model_reasoning_effort = {_format_toml_value(effort)}")
        body = f"{body}\n\n{codex_discipline_suffix()}"
        lines.append(f"developer_instructions = '''\n{body}\n'''")
        toml_path = out_dir / f"{name}.toml"
        atomic_write(toml_path, "\n".join(lines) + "\n")
        tomllib.loads(toml_path.read_text(encoding="utf-8"))
        count += 1
    logger.debug("codex_agents_generated", count=count, dest=str(out_dir))
    return count


def _register_agent_tomls(session_dir: Path) -> int:
    """Register generated agent config layers in the session config."""
    config_path = session_dir / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(config_text)
    configured_agents = config.get("agents", {})
    if not isinstance(configured_agents, dict):
        configured_agents = {}

    registrations: list[str] = []
    for agent_path in sorted((session_dir / "agents").glob("*.toml")):
        agent = tomllib.loads(agent_path.read_text(encoding="utf-8"))
        name = agent.get("name")
        description = agent.get("description")
        if not isinstance(name, str) or not name or name in configured_agents:
            continue
        if not isinstance(description, str) or not description:
            continue
        registrations.extend(
            [
                f"[agents.{_format_toml_value(name)}]",
                f"description = {_format_toml_value(description)}",
                f"config_file = {_format_toml_value(f'agents/{agent_path.name}')}",
                "",
            ]
        )

    if not registrations:
        return 0
    separator = "\n" if config_text.endswith("\n") else "\n\n"
    registration_text = "\n".join(registrations)
    updated = f"{config_text}{separator}{registration_text}"
    tomllib.loads(updated)
    atomic_write(config_path, updated)
    return len(registrations) // 4


def _materialize_profile_skills(
    session_dir: Path,
    *,
    source_codex_home: Path | None = None,
) -> int:
    """Symlink source-home profile skills into a generated Codex home.

    Scans the selected Codex home's ``skills`` for subdirectories containing
    SKILL.md. Each is symlinked into session_dir/skills/<name>. Falls back
    to shutil.copytree if symlink creation fails. Subdirectories without
    SKILL.md are skipped. Returns the number of skills materialized.
    """
    source_home = Path.home() / ".codex" if source_codex_home is None else Path(source_codex_home)
    profile_skills_root = source_home / "skills"
    if not profile_skills_root.is_dir():
        return 0
    count = 0
    skills_base = session_dir / "skills"
    skills_base.mkdir(parents=True, exist_ok=True)
    entries = list(profile_skills_root.iterdir())
    for entry in entries:
        if not entry.is_dir() or not (entry / "SKILL.md").is_file():
            continue
        target = skills_base / entry.name
        if target.exists() or target.is_symlink():
            continue
        try:
            target.symlink_to(entry.resolve())
        except OSError:
            logger.debug(
                "codex_profile_skill_symlink_failed_using_copytree",
                skill=entry.name,
                exc_info=True,
            )
            shutil.copytree(entry, target)
        count += 1
    return count


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
            supports_tool_list_changed=False,
            required_skill_fields=frozenset({"name", "description"}),
            required_session_files=frozenset({"config.toml"}),
            session_dir_symlinks=frozenset({"sessions", "archived_sessions"}),
            applicable_guards=frozenset({"write_guard"}),
            # Codex uses run_cmd instead of Write/Edit — those tools don't exist in Codex
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
            supports_model_invocation_gating=False,
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
                include_scope_discipline=True,
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
        extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ""
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
            sandbox=sandbox_mode,
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
                include_scope_discipline=True,
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
        extras[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] = ""
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
            developer_instructions = (
                f"{system_prompt}\n\n{codex_discipline_suffix()}"
                if system_prompt is not None
                else codex_discipline_suffix()
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
                FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
            }
        )
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
        # build_env strips this key from extras unconditionally, so it must be
        # injected here, after build_env returns (as the other builders do).
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
    ) -> CmdSpec:
        if not resume_session_id.strip():
            msg = "resume_session_id must be a non-empty string"
            raise ValueError(msg)
        cmd = _codex_exec_base(sandbox="read-only", json=(output_format == OutputFormat.JSON))
        cmd.append(CodexFlags.RESUME_SUBCOMMAND)
        cmd.append(resume_session_id)
        cmd.append(f"{codex_discipline_suffix()}\n\n{prompt}")
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

    def setup_session_dir(self, session_dir: Path) -> None:
        assert self.source_codex_home is not None
        codex_home_source = self.source_codex_home
        config_path = session_dir / "config.toml"
        if not config_path.is_file():
            raise FileNotFoundError(f"pre-launch Codex config snapshot is missing: {config_path}")
        tomllib.loads(config_path.read_text(encoding="utf-8"))

        auth_source = codex_home_source / "auth.json"
        auth_dest = session_dir / "auth.json"
        if auth_source.exists():
            auth_dest.symlink_to(auth_source.resolve(strict=True))
            logger.debug(
                "codex_auth_symlink",
                src=str(auth_source),
                dest=str(auth_dest),
            )

        env_source = codex_home_source / ".env"
        if env_source.exists():
            shutil.copy2(env_source, session_dir / ".env")

        _generate_agent_tomls(session_dir)
        registered = _register_agent_tomls(session_dir)
        logger.debug("codex_agents_registered", count=registered)
        _materialize_profile_skills(
            session_dir,
            source_codex_home=codex_home_source,
        )

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
            fragments.append(
                f"Call spawn_agent {spawn.count} time{'s' if spawn.count != 1 else ''} "
                f"with agent_type={native_role!r}, fork_turns='none'{policy_text}; "
                "retain every returned child ID."
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
    ) -> list[str]:
        del executable
        os.environ[MCP_CLIENT_BACKEND_ENV_VAR] = AGENT_BACKEND_CODEX
        try:
            assert self.source_codex_home is not None
            with codex_prelaunch_transaction(
                source_codex_home=self.source_codex_home,
                hook_config_format=self.capabilities.hook_config_format,
            ) as config_path:
                if session_dir is not None:
                    snapshot = config_path.read_bytes()
                    atomic_write(
                        Path(session_dir) / "config.toml",
                        snapshot.decode("utf-8"),
                    )
                    return []
                return _validate_global_codex_home(
                    self.source_codex_home,
                    config_path=config_path,
                )
        except Exception as exc:
            logger.error(
                "codex_prelaunch_transaction_failed",
                exc_info=True,
            )
            return [f"Codex pre-launch configuration failed: {type(exc).__name__}: {exc}"]

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
