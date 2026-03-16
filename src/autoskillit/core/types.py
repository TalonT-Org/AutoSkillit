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

# Env vars that control MCP server-level behavior and must not leak into
# user-code subprocesses (pytest runs, shell commands, etc.).
# Add new internal vars here as they are introduced.
AUTOSKILLIT_PRIVATE_ENV_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
    }
)


class RetryReason(StrEnum):
    RESUME = "resume"
    NONE = "none"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EARLY_STOP = "early_stop"


class MergeFailedStep(StrEnum):
    PATH_VALIDATION = "path_validation"
    PROTECTED_BRANCH = "protected_branch"
    BRANCH_DETECTION = "branch_detection"
    DIRTY_TREE = "dirty_tree"
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    PRE_REBASE_CHECK = "pre_rebase_check"
    MERGE_COMMITS_DETECTED = "merge_commits_detected"
    REBASE = "rebase"
    GENERATED_FILE_CLEANUP = "generated_file_cleanup"
    POST_REBASE_TEST_GATE = "post_rebase_test_gate"
    MERGE = "merge"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    WORKTREE_INTACT_BASE_NOT_PUBLISHED = "worktree_intact_base_not_published"
    WORKTREE_INTACT_MERGE_COMMITS_DETECTED = "worktree_intact_merge_commits_detected"
    WORKTREE_DIRTY = "worktree_dirty"
    WORKTREE_DIRTY_ABORT_FAILED = "worktree_dirty_abort_failed"
    WORKTREE_DIRTY_MID_OPERATION = "worktree_dirty_mid_operation"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"
    MAIN_REPO_DIRTY_ABORT_FAILED = "main_repo_dirty_abort_failed"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    BUNDLED_EXTENDED = "bundled_extended"


class RecipeSource(StrEnum):
    PROJECT = "project"
    BUILTIN = "builtin"


class ClaudeFlags(StrEnum):
    """Canonical registry of all claude CLI flags used by autoskillit.

    Every flag string that autoskillit passes to the claude binary MUST be
    defined here. Call sites must reference these constants — never hardcode
    flag strings at the call site.

    When the claude CLI renames or removes a flag:
      1. Update the constant value here.
      2. Follow the failing tests in test_flag_contracts.py to update call sites.
    """

    # Permission bypass
    ALLOW_DANGEROUSLY_SKIP_PERMISSIONS = (
        "--allow-dangerously-skip-permissions"  # enables option without activating
    )
    DANGEROUSLY_SKIP_PERMISSIONS = "--dangerously-skip-permissions"  # actually bypasses all checks

    # Prompt / execution mode
    PRINT = "-p"

    # Model selection
    MODEL = "--model"

    # Plugin / directory
    PLUGIN_DIR = "--plugin-dir"
    ADD_DIR = "--add-dir"

    # Output format
    OUTPUT_FORMAT = "--output-format"
    VERBOSE = "--verbose"

    # Interactive session restrictions
    TOOLS = "--tools"
    APPEND_SYSTEM_PROMPT = "--append-system-prompt"


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
            return (ClaudeFlags.VERBOSE,)
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
    elapsed_seconds: float = 0.0
    """Pre-computed monotonic elapsed time in seconds (always >= 0).

    Set by headless.py using time.monotonic() brackets around the subprocess run.
    Consumers (session_log, tokens) must use this float directly — never re-derive
    duration from start_ts/end_ts ISO strings.
    """


@runtime_checkable
class SubprocessRunner(Protocol):
    """Protocol for async subprocess execution. Matches run_managed_async signature."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str] | None = None,
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
        "claim_issue",
        "release_issue",
        "wait_for_ci",
        "create_unique_branch",
        "write_telemetry_files",
        "get_pr_reviews",
        "bulk_close_issues",
        "check_pr_mergeable",
        "set_commit_status",
        "wait_for_merge_queue",
        # Formerly ungated — now kitchen-gated:
        "fetch_github_issue",
        "get_issue_title",
        "get_ci_status",
        "get_pipeline_report",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset({"test_check"})


@dataclass(frozen=True)
class ValidatedAddDir:
    """An --add-dir path validated for Claude Code convention compliance.

    Cannot be constructed directly — use ``validate_add_dir()`` or obtain from
    ``DefaultSessionSkillManager.init_session()``.

    Implements ``__str__``, ``__fspath__``, and ``__truediv__`` so it works
    transparently with ``str(d)`` (used by ``build_interactive_cmd``),
    ``shutil.rmtree`` (used by chefs-hat), and ``d / "subdir"`` (path
    composition in tests and production code).
    """

    path: str

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def __truediv__(self, other: str | Path) -> Path:
        return Path(self.path) / other


FREE_RANGE_TOOLS: frozenset[str] = frozenset({"open_kitchen", "close_kitchen"})

UNGATED_TOOLS: frozenset[str] = FREE_RANGE_TOOLS

CATEGORY_TAGS: frozenset[str] = frozenset(
    {"github", "ci", "clone", "telemetry", "arch-lens", "audit"}
)

# Maps each MCP tool name to its functional category subset tags.
# Mirrors the FastMCP @mcp.tool(tags=...) category assignments in the server layer.
# Tools with no functional category are absent from this map (empty intersection = no finding).
TOOL_SUBSET_TAGS: dict[str, frozenset[str]] = {
    # github
    "fetch_github_issue": frozenset({"github"}),
    "get_issue_title": frozenset({"github"}),
    "report_bug": frozenset({"github"}),
    "prepare_issue": frozenset({"github"}),
    "enrich_issues": frozenset({"github"}),
    "claim_issue": frozenset({"github"}),
    "release_issue": frozenset({"github"}),
    "get_pr_reviews": frozenset({"github"}),
    "bulk_close_issues": frozenset({"github"}),
    "check_pr_mergeable": frozenset({"github"}),
    "push_to_remote": frozenset({"github"}),
    "create_unique_branch": frozenset({"github"}),
    "set_commit_status": frozenset({"github"}),
    # ci
    "wait_for_ci": frozenset({"ci"}),
    "wait_for_merge_queue": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    # clone
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    # telemetry
    "get_token_summary": frozenset({"telemetry"}),
    "get_timing_summary": frozenset({"telemetry"}),
    "write_telemetry_files": frozenset({"telemetry"}),
    "get_quota_events": frozenset({"telemetry"}),
}

# Categorized tool listing for the open_kitchen response.
# Each entry is (category_name, tuple_of_tool_names). Tool names must match the
# registered MCP tool names exactly.
TOOL_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Execution", ("run_cmd", "run_python", "run_skill")),
    ("Testing & Workspace", ("test_check", "reset_test_dir", "classify_fix", "reset_workspace")),
    (
        "Git Operations",
        ("merge_worktree", "create_unique_branch", "check_pr_mergeable", "set_commit_status"),
    ),
    ("Recipes", ("migrate_recipe", "list_recipes", "load_recipe", "validate_recipe")),
    ("Clone & Remote", ("clone_repo", "remove_clone", "push_to_remote")),
    (
        "GitHub",
        (
            "fetch_github_issue",
            "get_issue_title",
            "get_ci_status",
            "report_bug",
            "prepare_issue",
            "enrich_issues",
            "claim_issue",
            "release_issue",
            "wait_for_ci",
            "wait_for_merge_queue",
            "get_pr_reviews",
            "bulk_close_issues",
        ),
    ),
    (
        "Telemetry & Diagnostics",
        (
            "read_db",
            "write_telemetry_files",
            "kitchen_status",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
        ),
    ),
    ("Kitchen", ("open_kitchen", "close_kitchen")),
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
    worktree_path: str | None = None
    cli_subtype: str = field(default="")
    write_path_warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        data: dict[str, Any] = {
            "success": self.success,
            "result": self.result,
            "session_id": self.session_id,
            "subtype": self.subtype,
            "cli_subtype": self.cli_subtype,
            "is_error": self.is_error,
            "exit_code": self.exit_code,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
            "stderr": self.stderr,
            "token_usage": self.token_usage,
            "write_path_warnings": self.write_path_warnings,
        }
        if self.worktree_path is not None:
            data["worktree_path"] = self.worktree_path
        return json.dumps(data, default=lambda o: o.value if isinstance(o, Enum) else str(o))

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

    def consecutive_failures(self, skill_command: str) -> int: ...

    def record_success(self, skill_command: str) -> None: ...

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int: ...


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
        elapsed_seconds: float | None = None,
    ) -> None: ...

    def get_report(self) -> list[dict[str, Any]]: ...

    def compute_total(self) -> dict[str, Any]: ...

    def clear(self) -> None: ...

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int: ...


@runtime_checkable
class TimingStore(Protocol):
    """Protocol for per-step wall-clock timing accumulation."""

    def record(self, step_name: str, duration_seconds: float) -> None: ...

    def get_report(self) -> list[dict[str, Any]]: ...

    def compute_total(self) -> dict[str, Any]: ...

    def clear(self) -> None: ...

    def load_from_log_dir(self, log_root: Path, *, since: str = "") -> int: ...


@runtime_checkable
class McpResponseStore(Protocol):
    """Protocol for per-tool MCP response size accumulation."""

    def record(
        self,
        tool_name: str,
        response: str,
        *,
        alert_threshold_tokens: int = 0,
    ) -> bool: ...

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
        add_dirs: Sequence[ValidatedAddDir] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        expected_output_patterns: Sequence[str] = (),
    ) -> SkillResult: ...


@runtime_checkable
class RecipeRepository(Protocol):
    """Protocol for recipe discovery and loading."""

    def find(self, name: str, project_dir: Path) -> Any: ...

    def list(self, project_dir: Path) -> Any: ...

    def load_and_validate(
        self,
        name: str,
        project_dir: Any,
        *,
        suppressed: Sequence[str] | None = None,
        resolved_defaults: dict[str, str] | None = None,
        ingredient_overrides: dict[str, str] | None = None,
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
class OutputPatternResolver(Protocol):
    """Protocol for resolving expected output patterns from a skill command."""

    def __call__(self, skill_command: str) -> Sequence[str]: ...


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
        self,
        source_dir: str,
        run_name: str,
        branch: str = "",
        strategy: str = "",
        remote_url: str = "",
    ) -> dict[str, str]: ...

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]: ...

    def push_to_remote(
        self,
        clone_path: str,
        source_dir: str = "",
        branch: str = "",
        *,
        remote_url: str = "",
        protected_branches: list[str] | None = None,
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

    async def fetch_title(self, issue_url: str) -> dict[str, object]: ...

    async def add_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
    ) -> dict[str, Any]: ...

    async def remove_label(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label: str,
    ) -> dict[str, Any]: ...

    async def ensure_label(
        self,
        owner: str,
        repo: str,
        label: str,
        color: str = "ededed",
        description: str = "",
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CIRunScope:
    """Immutable scope parameters that uniquely identify which CI workflow runs are relevant.

    Passed as a single argument through the CIWatcher protocol so that adding a new
    scope axis (e.g. event: str | None) requires changing only this dataclass and the
    API params builder — not every method signature in the call chain.
    """

    workflow: str | None = None  # workflow filename, e.g. "tests.yml"
    head_sha: str | None = None  # commit SHA to pin results to


@runtime_checkable
class CIWatcher(Protocol):
    """Protocol for watching GitHub Actions CI runs.

    Implementations must never raise — all errors must be captured and
    returned in the result dict with appropriate conclusion values.
    """

    async def wait(
        self,
        branch: str,
        *,
        repo: str | None = None,
        scope: CIRunScope = CIRunScope(),
        timeout_seconds: int = 300,
        lookback_seconds: int = 120,
        cwd: str = "",
    ) -> dict[str, Any]: ...

    async def status(
        self,
        branch: str,
        *,
        repo: str | None = None,
        run_id: int | None = None,
        scope: CIRunScope = CIRunScope(),
        cwd: str = "",
    ) -> dict[str, Any]: ...


@runtime_checkable
class MergeQueueWatcher(Protocol):
    """Protocol for watching a PR's progress through GitHub's merge queue.

    Implementations must never raise — all errors must be captured and
    returned in the result dict with appropriate pr_state values.
    """

    async def wait(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
        timeout_seconds: int = 600,
        poll_interval: int = 15,
    ) -> dict[str, Any]: ...


@runtime_checkable
class SessionSkillManager(Protocol):
    """Protocol for managing per-session ephemeral skill directories."""

    def init_session(
        self,
        session_id: str,
        *,
        cook_session: bool = False,
        config: Any | None = None,
        project_dir: Path | None = None,
    ) -> ValidatedAddDir: ...

    def activate_tier2(self, session_id: str, skill_name: str) -> bool: ...

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int: ...
