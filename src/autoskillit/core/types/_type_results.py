"""Core result dataclasses.

Zero autoskillit imports outside this sub-package. Provides LoadResult, SkillResult,
CleanupResult, and related dataclasses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypedDict, TypeVar

from ._type_constants import KNOWN_CI_EVENTS
from ._type_enums import KillReason, RetryReason, SessionOutcome

T = TypeVar("T")

__all__ = [
    "LoadReport",
    "LoadResult",
    "TestResult",
    "ValidatedAddDir",
    "WriteBehaviorSpec",
    "FailureRecord",
    "SessionTelemetry",
    "SkillResult",
    "CleanupResult",
    "CIRunScope",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "RunSkillResult",
    "RunCmdResult",
    "TestCheckResult",
    "MergeWorktreeResult",
    "TokenSummaryResult",
    "TimingSummaryResult",
    "KitchenStatusResult",
    "TokenUsageFileEntry",
    "SessionIndexEntry",
]


@dataclass
class TestResult:
    """Result of a test runner invocation."""

    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float | None = None
    tests_selected: int | None = None
    tests_deselected: int | None = None
    filter_mode: str | None = None
    full_run_reason: str | None = None


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


@dataclass(frozen=True)
class ValidatedAddDir:
    """An --add-dir path validated for Claude Code convention compliance.

    Cannot be constructed directly — use ``validate_add_dir()`` or obtain from
    ``DefaultSessionSkillManager.init_session()``.

    Implements ``__str__``, ``__fspath__``, and ``__truediv__`` so it works
    transparently with ``str(d)`` (used by ``build_interactive_cmd``),
    ``shutil.rmtree`` (used by cook), and ``d / "subdir"`` (path
    composition in tests and production code).
    """

    path: str

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def __truediv__(self, other: str | Path) -> Path:
        return Path(self.path) / other

    def exists(self) -> bool:
        return Path(self.path).exists()

    def is_dir(self) -> bool:
        return Path(self.path).is_dir()

    def glob(self, pattern: str) -> list[Path]:
        return list(Path(self.path).glob(pattern))


@dataclass(frozen=True)
class WriteBehaviorSpec:
    """Write-expectation metadata resolved from skill contracts.

    mode:
        None  — no write expectation (gate inactive)
        "always" — writes are always expected (gate active unconditionally)
        "conditional" — writes expected only when expected_when patterns match
    expected_when:
        Regex patterns matched against session output. Only meaningful when
        mode="conditional". If any pattern matches, writes are expected.
    """

    mode: str | None = None
    expected_when: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class SessionTelemetry:
    """Typed bundle of all per-session telemetry fields passed to flush_session_log.

    All fields are required — constructing without any field is a TypeError,
    making omissions visible at construction time rather than silently defaulting
    to None and skipping the corresponding write gate inside flush_session_log.
    """

    token_usage: dict[str, Any] | None
    timing_seconds: float | None
    audit_record: dict[str, Any] | None
    github_api_usage: dict[str, Any] | None
    github_api_requests: int
    loc_insertions: int
    loc_deletions: int

    @classmethod
    def empty(cls) -> SessionTelemetry:
        """Zero-value sentinel for error paths where no telemetry is available."""
        return cls(
            token_usage=None,
            timing_seconds=None,
            audit_record=None,
            github_api_usage=None,
            github_api_requests=0,
            loc_insertions=0,
            loc_deletions=0,
        )


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
    write_call_count: int = 0
    fs_writes_detected: bool = False
    order_id: str = ""
    kill_reason: KillReason = KillReason.NATURAL_EXIT
    """Why the subprocess was (or was not) killed after the race loop.

    Surfaces from SubprocessResult so the formatter can annotate exit_code
    with the kill cause, resolving the "success=True + exit_code=-9" contradiction.
    """
    last_stop_reason: str = ""
    lifespan_started: bool = False
    """True when the L3 session invoked at least one MCP tool (heuristic for server lifespan)."""
    provider_used: str = field(default="")
    """Provider identifier stamped by _build_skill_result (e.g. 'anthropic', 'vertex')."""
    provider_fallback: bool = False
    """True when this result was produced by a fallback provider (not the primary)."""
    infra_exit_category: str = ""
    """Infrastructure exit classification (InfraExitCategory value)."""

    def to_json(self) -> str:
        data: dict[str, Any] = {
            "success": self.success,
            "result": self.result,
            "session_id": self.session_id,
            "subtype": self.subtype,
            "cli_subtype": self.cli_subtype,
            "is_error": self.is_error,
            "exit_code": self.exit_code,
            "kill_reason": self.kill_reason.value,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
            "stderr": self.stderr,
            "token_usage": self.token_usage,
            "write_path_warnings": self.write_path_warnings,
            "write_call_count": self.write_call_count,
            "fs_writes_detected": self.fs_writes_detected,
            "last_stop_reason": self.last_stop_reason,
            "lifespan_started": self.lifespan_started,
            "provider_fallback": self.provider_fallback,
        }
        if self.worktree_path is not None:
            data["worktree_path"] = self.worktree_path
        if self.provider_used:
            data["provider_used"] = self.provider_used
        data["infra_exit_category"] = self.infra_exit_category
        data["order_id"] = self.order_id
        return json.dumps(data, default=lambda o: o.value if isinstance(o, Enum) else str(o))

    @classmethod
    def crashed(
        cls,
        exception: Exception,
        skill_command: str = "",
        session_id: str = "",
        order_id: str = "",
    ) -> SkillResult:
        """Construct a SkillResult for a runner crash (pre-launch or mid-flight exception).

        Produces the same 13+ field envelope as _build_skill_result, ensuring
        pipeline orchestrators can route crash responses without schema inspection.
        """
        _result = f"{type(exception).__name__}: {exception}"
        if skill_command:
            _result += f" | skill_command={skill_command!r}"
        return cls(
            success=False,
            result=_result,
            session_id=session_id,
            subtype="crashed",
            is_error=True,
            exit_code=-1,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
            kill_reason=KillReason.EXCEPTION,
            order_id=order_id,
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


@dataclass(frozen=True)
class CIRunScope:
    """Immutable scope parameters that uniquely identify which CI workflow runs are relevant.

    Passed as a single argument through the CIWatcher protocol so that adding a new
    scope axis requires changing only this dataclass and the API params builder —
    not every method signature in the call chain.
    """

    workflow: str | None = None  # workflow filename, e.g. "tests.yml"
    head_sha: str | None = None  # commit SHA to pin results to
    event: str | None = None  # trigger event, e.g. "push", "pull_request"

    def __post_init__(self) -> None:
        if self.event is not None and self.event not in KNOWN_CI_EVENTS:
            raise ValueError(
                f"Invalid CI event {self.event!r}. Valid events: {sorted(KNOWN_CI_EVENTS)}"
            )


class CloneSuccessResult(TypedDict):
    """Typed return contract for a successful clone_repo invocation.

    Precedent: PRFetchState(TypedDict) in execution/merge_queue.py for
    typed discriminated returns in the same codebase.
    """

    clone_path: str
    source_dir: str
    remote_url: str
    clone_source_type: Literal["remote", "local"]
    clone_source_reason: str


class CloneGateUncommitted(TypedDict):
    """Returned by clone_repo when uncommitted changes are detected (strategy="")."""

    uncommitted_changes: Literal["true"]
    source_dir: str
    branch: str
    changed_files: str
    total_changed: str


class CloneGateUnpublished(TypedDict):
    """Returned by clone_repo when the branch is unpublished (strategy="")."""

    unpublished_branch: Literal["true"]
    branch: str
    source_dir: str


CloneResult = CloneSuccessResult | CloneGateUncommitted | CloneGateUnpublished


class _RunSkillResultBase(TypedDict):
    """Required fields always present in every run_skill response."""

    success: bool
    exit_code: int


class RunSkillResult(_RunSkillResultBase, total=False):
    """Typed return contract for run_skill — mirrors SkillResult.to_json() output keys."""

    result: str
    session_id: str
    subtype: str
    cli_subtype: str
    is_error: bool
    kill_reason: str
    needs_retry: bool
    retry_reason: str
    stderr: str
    token_usage: dict[str, Any] | None
    write_path_warnings: list[str]
    write_call_count: int
    fs_writes_detected: bool
    last_stop_reason: str
    lifespan_started: bool
    worktree_path: str
    order_id: str
    infra_exit_category: str


class _RunCmdResultBase(TypedDict):
    """Required fields always present in every run_cmd response."""

    success: bool
    exit_code: int


class RunCmdResult(_RunCmdResultBase, total=False):
    """Typed return contract for run_cmd."""

    stdout: str
    stderr: str
    error: str


class _TestCheckResultBase(TypedDict):
    """Required field always present in every test_check response."""

    passed: bool


class TestCheckResult(_TestCheckResultBase, total=False):
    """Typed return contract for test_check."""

    stdout: str
    stderr: str
    duration_seconds: float
    filter_mode: str
    tests_selected: int
    tests_deselected: int
    full_run_reason: str
    error: str


class MergeWorktreeResult(TypedDict, total=False):
    """Typed return contract for merge_worktree — union of all success and error path keys."""

    merge_succeeded: bool
    merged_branch: str
    into_branch: str
    worktree_removed: bool
    branch_deleted: bool
    cleanup_succeeded: bool
    error: str
    failed_step: str
    state: str
    worktree_path: str
    stderr: str
    base_branch: str
    dirty_files: list[str]
    merge_commits: list[str]
    test_stdout: str
    test_stderr: str
    abort_failed: bool
    abort_stderr: str
    poisoned_installs: list[str]


class TokenSummaryResult(TypedDict, total=False):
    """Typed return contract for get_token_summary (JSON payload path)."""

    steps: list[dict[str, Any]]
    total: dict[str, Any]
    mcp_responses: dict[str, Any]
    success: bool
    error: str


class TimingSummaryResult(TypedDict, total=False):
    """Typed return contract for get_timing_summary (JSON payload path)."""

    steps: list[dict[str, Any]]
    total: dict[str, Any]
    success: bool
    error: str


class KitchenStatusResult(TypedDict, total=False):
    """Typed return contract for kitchen_status."""

    package_version: str
    plugin_json_version: str
    versions_match: bool
    tools_enabled: bool
    token_usage_verbosity: str
    quota_guard_enabled: bool
    github_token_configured: bool
    github_default_repo: str
    warning: str
    success: bool
    error: str


class TokenUsageFileEntry(TypedDict):
    """Schema contract for token_usage.json written by flush_session_log."""

    session_label: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    peak_context: int
    turn_count: int
    timing_seconds: float
    order_id: str
    loc_insertions: int
    loc_deletions: int
    provider_used: str


class SessionIndexEntry(TypedDict):
    """Schema contract for sessions.jsonl entries written by flush_session_log."""

    session_id: str
    dir_name: str
    timestamp: str
    cwd: str
    kitchen_id: str
    order_id: str
    campaign_id: str
    dispatch_id: str
    claude_code_log: str
    skill_command: str
    success: bool
    subtype: str
    cli_subtype: str
    exit_code: int
    snapshot_count: int
    anomaly_count: int
    peak_rss_kb: int
    peak_oom_score: int
    step_name: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    write_call_count: int
    tracked_comm: str | None
    tracked_comm_drift: bool
    autoskillit_version: str
    claude_code_version: str
    recipe_name: str
    recipe_content_hash: str
    recipe_composite_hash: str
    recipe_version: str
    duration_seconds: float
    github_api_requests: int
