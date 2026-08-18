"""Codex command builders, flag vocabulary, env policy, state probe, and session locator.

Owns the Codex flag vocabulary (`CodexFlags`), env-policy dataclass, the
state-readiness probe, the CodexSessionLocator, and shared cmd/extras
helpers consumed by `codex.py`.
"""

from __future__ import annotations

import json
import math
import sqlite3
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from pathlib import Path
from typing import Any

import zstandard

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    AGENT_BACKEND_ENV_VAR,
    AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR,
    AUTOSKILLIT_APPLICABLE_GUARDS,
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES,
    CODEX_SESSIONS_SUBDIR,
    CODEX_STARTUP_TRACE_ENV_VAR,
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    HookTrustPolicy,
    ObserverStatus,
    SessionLocator,
    SessionSummary,
    default_log_dir,
    get_logger,
)
from autoskillit.execution.backends._backend_cmd_builder_base import (
    SHARED_BASELINE_ENV,
    _filter_protected_native_shell_env,
)
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore

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


def _codex_exec_base(
    *,
    sandbox: str | None,
    json: bool = True,
    extra_overrides: Sequence[str] = (),
    bypass_hook_trust: bool = False,
) -> list[str]:
    cmd: list[str] = ["codex", "exec"]
    if json:
        cmd.append(CodexFlags.JSON)
    if sandbox is not None:
        cmd.extend([CodexFlags.SANDBOX, sandbox])
    for override in extra_overrides:
        cmd.extend([CodexFlags.CONFIG_OVERRIDE, override])
    cmd.extend([CodexFlags.CONFIG_OVERRIDE, _IMAGE_GENERATION_DISABLED])
    if bypass_hook_trust:
        # Hook trust is independent from the sandbox selected by config/CLI.
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
            FLEET_INSPECTOR_MODEL_ENV_VAR: "",
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
        }
    )
    extras.setdefault(LAUNCH_ID_ENV_VAR, "")
    extras.setdefault(AUTOSKILLIT_STATE_ROOT_ENV_VAR, "")
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
            filtered_extras.setdefault("AUTOSKILLIT_SKILL_NAME", "")
            out.update(
                (key, value)
                for key, value in filtered_extras.items()
                if key != CODEX_STARTUP_TRACE_ENV_VAR
            )
        out.setdefault(AUDIT_ADMISSION_AUTHORITY_PATH_ENV_VAR, "")  # Outer-cook control only.
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

    def read_session(self, path: Path) -> list[dict[str, Any]]:
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
        except (OSError, UnicodeDecodeError, zstandard.ZstdError):
            logger.warning("read_session: failed to read", path=str(path), exc_info=True)
            return []
        result: list[dict[str, Any]] = []
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


__all__ = [
    "CODEX_EXEC_FLAGS",
    "CODEX_ENV_DENYLIST",
    "CODEX_ENV_PREFIX_DENYLIST",
    "CODEX_TOP_LEVEL_ONLY_FLAGS",
    "CodexEnvPolicy",
    "CodexFlags",
    "CodexSessionLocator",
    "CodexStateReadinessProbe",
    "NON_VARIADIC_CODEX_FLAGS",
    "VARIADIC_CODEX_FLAGS",
]
