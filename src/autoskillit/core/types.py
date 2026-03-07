"""Core type contracts: StrEnum discriminators, protocols, and constants.

Zero autoskillit imports. Provides the shared type vocabulary for all higher layers.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum, StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")

AUTOSKILLIT_INSTALLED_VERSION: str = version("autoskillit")


class RetryReason(StrEnum):
    RESUME = "resume"
    NONE = "none"


class MergeFailedStep(StrEnum):
    PATH_VALIDATION = "path_validation"
    BRANCH_DETECTION = "branch_detection"
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    PRE_REBASE_CHECK = "pre_rebase_check"
    REBASE = "rebase"
    POST_REBASE_TEST_GATE = "post_rebase_test_gate"
    MERGE = "merge"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    WORKTREE_INTACT_BASE_NOT_PUBLISHED = "worktree_intact_base_not_published"
    WORKTREE_DIRTY_ABORT_FAILED = "worktree_dirty_abort_failed"
    WORKTREE_DIRTY_MID_OPERATION = "worktree_dirty_mid_operation"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"
    MAIN_REPO_DIRTY_ABORT_FAILED = "main_repo_dirty_abort_failed"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillSource(StrEnum):
    BUNDLED = "bundled"


class RecipeSource(StrEnum):
    PROJECT = "project"
    BUILTIN = "builtin"


class OutputFormat(StrEnum):
    """Claude CLI output format with declared data capabilities.

    STREAM_JSON emits per-turn NDJSON records (type=assistant, type=result),
    providing assistant_messages and model_breakdown.
    JSON emits a single result envelope — no assistant records.
    """

    JSON = "json"
    STREAM_JSON = "stream-json"

    @property
    def supports_assistant_messages(self) -> bool:
        return self == OutputFormat.STREAM_JSON

    @property
    def supports_model_breakdown(self) -> bool:
        return self == OutputFormat.STREAM_JSON

    @property
    def required_cli_flags(self) -> tuple[str, ...]:
        """CLI flags required when this format is used with -p (headless) mode."""
        if self == OutputFormat.STREAM_JSON:
            return ("--verbose",)
        return ()

    @classmethod
    def derive(cls, *, completion_marker: str) -> OutputFormat:
        """Derive the required format from feature configuration.

        If completion_marker is set, recovery requires assistant_messages,
        which requires STREAM_JSON format.
        """
        if completion_marker:
            return cls.STREAM_JSON
        return cls.JSON


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


class ChannelConfirmation(StrEnum):
    """How subprocess completion was confirmed by the two-channel detection system.

    Replaces SubprocessResult.data_confirmed: bool to eliminate ambiguity
    between "Channel A confirmed content" and "no monitoring ran".

    Invariant (from process.py):
    - CHANNEL_A: heartbeat fired; stdout contains non-empty type=result record.
    - CHANNEL_B: session JSONL marker fired; drain expired OR no heartbeat configured.
      stdout may be empty. Downstream must not require stdout content.
    - UNMONITORED: no channel monitoring active (NATURAL_EXIT, STALE, TIMED_OUT,
      sync path, or heartbeat disabled with no Channel B win).
    """

    CHANNEL_A = "channel_a"
    CHANNEL_B = "channel_b"
    UNMONITORED = "unmonitored"


class SessionOutcome(StrEnum):
    """Classification of a completed headless session.

    Maps bijectively from the two-field (success, needs_retry) boolean pair
    on SkillResult to a single named discriminant:

        SUCCEEDED  → (success=True,  needs_retry=False)
        RETRIABLE  → (success=False, needs_retry=True)
        FAILED     → (success=False, needs_retry=False)

    The combination (success=True, needs_retry=True) is structurally impossible
    and has no corresponding member.
    """

    SUCCEEDED = "succeeded"
    RETRIABLE = "retriable"
    FAILED = "failed"


#: Semantic contract for SubprocessResult fields per TerminationReason.
#: These invariants are enforced by tests/test_process_lifecycle.py
#: TestAdjudicationCoverageMatrix.
#:
#: NATURAL_EXIT:
#:   channel_confirmation=UNMONITORED (typical: process exited before channels fired)
#:   channel_confirmation=CHANNEL_A (simultaneous: process exit + heartbeat in same tick)
#:   channel_confirmation=CHANNEL_B (simultaneous: process exit + session monitor completion)
#:   returncode=process's actual exit code (0 = voluntary, nonzero = crash)
#:   stdout=whatever was flushed to the temp file before exit
#:   Kill-anomaly possible when returncode==0, UNMONITORED, and stdout is success+empty,
#:   empty_output, or unparseable → _is_kill_anomaly returns True.
#:   When CHANNEL_A or CHANNEL_B: no kill anomaly; session completed.
#:
#: COMPLETED (Channel A):
#:   channel_confirmation=CHANNEL_A (heartbeat confirmed type=result in stdout)
#:   returncode=nonzero (SIGTERM/SIGKILL from async_kill_process_tree)
#:   stdout=contains a complete type=result NDJSON record
#:
#: COMPLETED (Channel B, drain expired OR no heartbeat configured):
#:   channel_confirmation=CHANNEL_B (session JSONL is sole authority)
#:   returncode=nonzero (SIGTERM/SIGKILL)
#:   stdout=may be empty (CLI not yet flushed type=result before kill)
#:   _compute_success provenance bypass applies: return True immediately.
#:
#: STALE:
#:   channel_confirmation=UNMONITORED (typical: stale monitor fired alone)
#:   channel_confirmation=CHANNEL_A (simultaneous: stale monitor + heartbeat in same tick)
#:   returncode=nonzero (SIGTERM/SIGKILL)
#:   _build_skill_result intercepts before _compute_success: attempts
#:   stdout recovery; if successful returns subtype="recovered_from_stale".
#:   STALE+CHANNEL_B is structurally impossible: session_monitor returns either
#:   "stale" or "completion", never both; stale path sets UNMONITORED.
#:
#: TIMED_OUT:
#:   channel_confirmation=UNMONITORED (never modified)
#:   returncode=-1 (hardcoded in _build_skill_result, not from process)
#:   _build_skill_result constructs synthetic ClaudeSessionResult(subtype="timeout").
#:   Always returns success=False, needs_retry=False.
_TERMINATION_CONTRACT = None  # Marker — contract is documented above in comments.


@dataclass
class SubprocessResult:
    """Result from a managed subprocess execution."""

    returncode: int
    stdout: str
    stderr: str
    termination: TerminationReason
    pid: int
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED
    """How completion was confirmed by the two-channel detection system.

    CHANNEL_A: heartbeat confirmed type=result in stdout; data availability guaranteed.
    CHANNEL_B: session JSONL marker fired; drain expired or no heartbeat configured.
               stdout may be empty — callers must trust JSONL signal, not stdout content.
    UNMONITORED: no channel monitoring active (NATURAL_EXIT, STALE, TIMED_OUT, sync path).
    """
    proc_snapshots: list[dict[str, object]] | None = None
    channel_b_session_id: str = ""
    start_ts: str = ""
    end_ts: str = ""


@runtime_checkable
class SubprocessRunner(Protocol):
    """Protocol for async subprocess execution. Matches run_managed_async signature."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = True,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: Any | None = None,
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
    "Agent",  # actual tool name; "Explore" is a subagent_type parameter, blocked via Agent
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Skill tools that route headless Claude sessions — canonical constant used by
# recipe_validator.py.
SKILL_TOOLS: frozenset[str] = frozenset({"run_skill"})

# Authoritative MCP tool registries. Defined here (L0) so both pipeline/ (L1)
# and recipe/ (L2) can reference them without cross-layer import violations.
GATED_TOOLS: frozenset[str] = frozenset(
    {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "test_check",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
        "migrate_recipe",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "report_bug",
        "prepare_issue",
        "enrich_issues",
    }
)

UNGATED_TOOLS: frozenset[str] = frozenset(
    {
        "kitchen_status",
        "get_pipeline_report",
        "get_token_summary",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
        "fetch_github_issue",
    }
)

# Canonical prefix required for all skill_command values passed to run_skill.
# Enforced at the Claude Code hook boundary by skill_command_guard.py.
SKILL_COMMAND_PREFIX: str = "/"

# Canonical prefix for bundled autoskillit slash commands.
AUTOSKILLIT_SKILL_PREFIX: str = "/autoskillit:"


@dataclass
class FailureRecord:
    """Structured record of a single run_skill failure.

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

    @property
    def outcome(self) -> SessionOutcome:
        """Classify this result as SUCCEEDED, RETRIABLE, or FAILED.

        Derived from the (success, needs_retry) pair — not a stored field.
        Not included in to_json().
        """
        if self.success:
            return SessionOutcome.SUCCEEDED
        if self.needs_retry:
            return SessionOutcome.RETRIABLE
        return SessionOutcome.FAILED


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

    def record(
        self,
        step_name: str,
        token_usage: dict[str, Any] | None,
        *,
        start_ts: str = "",
        end_ts: str = "",
    ) -> None: ...

    def get_report(self) -> list[dict[str, Any]]: ...

    def compute_total(self) -> dict[str, Any]: ...

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


@runtime_checkable
class CloneManager(Protocol):
    """Protocol for clone-based pipeline run isolation."""

    def clone_repo(
        self, source_dir: str, run_name: str, branch: str = "", strategy: str = ""
    ) -> dict[str, str]: ...

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]: ...

    def push_to_remote(
        self, clone_path: str, source_dir: str = "", branch: str = "", *, remote_url: str = ""
    ) -> dict[str, str | bool]: ...


@runtime_checkable
class GitHubFetcher(Protocol):
    """Protocol for fetching and filing GitHub issue content.

    Implementations must never raise — all errors must be captured and
    returned in the result dict with success=False.
    """

    @property
    def has_token(self) -> bool:
        """True if this fetcher was constructed with an authentication token."""
        ...

    async def fetch_issue(
        self,
        issue_ref: str,
        *,
        include_comments: bool = True,
    ) -> dict[str, Any]: ...

    async def search_issues(
        self,
        query: str,
        owner: str,
        repo: str,
        *,
        state: str = "open",
    ) -> dict[str, Any]: ...

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        *,
        labels: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def add_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]: ...
