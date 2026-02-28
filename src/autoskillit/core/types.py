"""Core type contracts: StrEnum discriminators, protocols, and constants.

Zero autoskillit imports. Provides the shared type vocabulary for all higher layers.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class RetryReason(StrEnum):
    RESUME = "resume"
    NONE = "none"


class MergeFailedStep(StrEnum):
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    REBASE = "rebase"
    MERGE = "merge"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillSource(StrEnum):
    BUNDLED = "bundled"


class RecipeSource(StrEnum):
    PROJECT = "project"
    BUILTIN = "builtin"


class Severity(StrEnum):
    OK = "ok"
    ERROR = "error"
    WARNING = "warning"


class TerminationReason(StrEnum):
    """How a managed subprocess ended.

    Propagates termination provenance from run_managed_async to consumers,
    replacing implicit inference from exit codes.
    """

    NATURAL_EXIT = "natural_exit"
    COMPLETED = "completed"
    STALE = "stale"
    TIMED_OUT = "timed_out"


@dataclass
class SubprocessResult:
    """Result from a managed subprocess execution."""

    returncode: int
    stdout: str
    stderr: str
    termination: TerminationReason
    pid: int


@runtime_checkable
class SubprocessRunner(Protocol):
    """Protocol for async subprocess execution. Matches run_managed_async signature."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        heartbeat_marker: str = "",
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = True,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
    ) -> Awaitable[SubprocessResult]: ...


@dataclass
class LoadReport:
    """A single file that failed to load, with the reason."""

    path: Path
    error: str


@dataclass
class LoadResult(Generic[T]):
    """Discovery result: successfully loaded items + error reports."""

    items: list[T]
    errors: list[LoadReport] = field(default_factory=list)


# The substring Claude CLI emits when the context window is full.
# Used by ClaudeSessionResult._is_context_exhausted() for detection.
# Centralized here so tests can reference the canonical value.
CONTEXT_EXHAUSTION_MARKER = "prompt is too long"

# Attribute names set unconditionally by logging.LogRecord.__init__ and makeRecord().
# Passing any of these as keys in the extra={} dict to ctx.info/ctx.error causes
# FastMCP's stdlib logging bridge to raise KeyError at runtime.
# Used by server/helpers._notify() for pre-dispatch validation.
RESERVED_LOG_RECORD_KEYS: frozenset[str] = frozenset(
    {
        # Set unconditionally in LogRecord.__init__
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        # Python 3.12+ addition
        "taskName",
        # Additional keys checked explicitly in makeRecord (not in __init__)
        "message",
        "asctime",
    }
)

# Native Claude Code tools that pipeline orchestrators must NEVER use directly.
# Canonical source of truth — imported by server.py and tests.
PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Task",
    "Explore",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Skill tools that route headless Claude sessions — canonical constant used by
# recipe_validator.py.
SKILL_TOOLS: frozenset[str] = frozenset({"run_skill", "run_skill_retry"})

# Known field names in run_skill_retry response — used by workflow validation
RETRY_RESPONSE_FIELDS: frozenset[str] = frozenset(
    {
        "success",
        "result",
        "session_id",
        "subtype",
        "is_error",
        "exit_code",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
    }
)


@dataclass
class FailureRecord:
    """Structured record of a single run_skill / run_skill_retry failure.

    Pure-stdlib dataclass — no autoskillit imports required.
    Shared between pipeline/audit.py (DefaultAuditLog store) and
    execution/headless.py (_capture_failure).
    """

    timestamp: str  # ISO 8601 UTC, e.g. "2026-02-24T16:12:26Z"
    skill_command: str  # truncated to COMMAND_MAX_LEN
    exit_code: int
    subtype: str  # e.g. "error", "stale", "timeout", "gate_error"
    needs_retry: bool
    retry_reason: str  # RetryReason.value string
    stderr: str  # truncated to STDERR_MAX_LEN

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return asdict(self)


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len, appending a count of truncated chars."""
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


@dataclass
class SkillResult:
    """Typed result returned by _build_skill_result and run_headless_core."""

    success: bool
    result: str
    session_id: str
    subtype: str
    is_error: bool
    exit_code: int
    needs_retry: bool
    retry_reason: RetryReason
    stderr: str
    token_usage: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "result": self.result,
                "session_id": self.session_id,
                "subtype": self.subtype,
                "is_error": self.is_error,
                "exit_code": self.exit_code,
                "needs_retry": self.needs_retry,
                "retry_reason": self.retry_reason,
                "stderr": self.stderr,
                "token_usage": self.token_usage,
            },
            default=lambda o: o.value if isinstance(o, Enum) else str(o),
        )


@dataclass
class CleanupResult:
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:  # type: ignore[type-arg]
        return {
            "success": self.success,
            "deleted": self.deleted,
            "failed": [{"path": p, "error": e} for p, e in self.failed],
            "skipped": self.skipped,
        }


@runtime_checkable
class GatePolicy(Protocol):
    """Protocol for gate enable/disable state."""

    @property
    def enabled(self) -> bool: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...


@runtime_checkable
class AuditStore(Protocol):
    """Protocol for pipeline failure accumulation."""

    def record_failure(self, record: FailureRecord) -> None: ...

    def get_report(self) -> list[FailureRecord]: ...

    def get_report_as_dicts(self) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...


@runtime_checkable
class TokenStore(Protocol):
    """Protocol for per-step token usage accumulation."""

    def record(self, step_name: str, token_usage: dict[str, Any] | None) -> None: ...

    def get_report(self) -> list[dict[str, Any]]: ...

    def compute_total(self) -> dict[str, int]: ...

    def clear(self) -> None: ...


@runtime_checkable
class TestRunner(Protocol):
    """Protocol for running a test suite and reporting pass/fail."""

    async def run(self, cwd: Path) -> tuple[bool, str]: ...


@runtime_checkable
class HeadlessExecutor(Protocol):
    """Protocol for running headless Claude Code sessions."""

    async def run(
        self,
        skill_command: str,
        cwd: str,
        *,
        model: str = "",
        step_name: str = "",
        add_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
    ) -> SkillResult: ...


@runtime_checkable
class RecipeRepository(Protocol):
    """Protocol for recipe discovery and loading."""

    def find(self, name: str, project_dir: Path) -> Any: ...

    def list(self, project_dir: Path) -> Any: ...

    def load_and_validate(
        self, name: str, project_dir: Any, *, suppressed: Sequence[str] | None = None
    ) -> dict[str, Any]: ...

    def validate_from_path(self, script_path: Any) -> dict[str, Any]: ...

    def list_all(self, project_dir: Any | None = None) -> dict[str, Any]: ...


@runtime_checkable
class MigrationService(Protocol):
    """Protocol for applying migration notes to a recipe file."""

    async def migrate(self, recipe_path: Path) -> dict[str, Any]: ...


@runtime_checkable
class DatabaseReader(Protocol):
    """Protocol for read-only SQLite query execution."""

    def query(
        self,
        db_path: str,
        sql: str,
        params: list | dict,  # type: ignore[type-arg]
        timeout_sec: int,
        max_rows: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class WorkspaceManager(Protocol):
    """Protocol for directory teardown operations."""

    def delete_contents(
        self,
        directory: Path,
        preserve: set[str] | None = None,
    ) -> CleanupResult: ...
