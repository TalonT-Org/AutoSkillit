"""Core result dataclasses — universal types.

Execution-scoped types (SessionTelemetry, RecipeIdentity, CIRunScope) live in
_type_results_execution.py for narrower test cascade. ProviderOutcome stays here
because SkillResult.provider references it, and SkillResult is consumed by 13+
directories — a cross-import would undermine the cascade narrowing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal, TypedDict, TypeVar

from ._type_enums import KillReason, RetryReason, SessionOutcome

T = TypeVar("T")

__all__ = [
    "LoadReport",
    "LoadResult",
    "TestResult",
    "ValidatedAddDir",
    "ValidatedWorktreePath",
    "WriteBehaviorSpec",
    "WriteEvidence",
    "FailureRecord",
    "ProviderOutcome",
    "InfraOutcome",
    "ApiRetryOutcome",
    "SkillResult",
    "CleanupResult",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "ModelTotalEntry",
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


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class ValidatedWorktreePath:
    """A worktree path validated as absolute and existing on disk.

    Cannot be constructed directly — use ``validate_worktree_path()``.
    """

    path: str

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def __truediv__(self, other: str | Path) -> Path:
        return Path(self.path) / other

    def is_dir(self) -> bool:
        return Path(self.path).is_dir()


@dataclass(frozen=True, slots=True)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "skill_command": self.skill_command,
            "exit_code": self.exit_code,
            "subtype": self.subtype,
            "needs_retry": self.needs_retry,
            "retry_reason": self.retry_reason,
            "stderr": self.stderr,
        }


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """Typed bundle of provider execution outcome fields.

    All fields are required — constructing without any field is a TypeError,
    making omissions visible at construction time rather than silently defaulting.
    """

    provider_used: str
    fallback_activated: bool

    @classmethod
    def none_used(cls) -> ProviderOutcome:
        """Sentinel for paths where no provider selection occurred."""
        return cls(provider_used="", fallback_activated=False)


@dataclass(frozen=True, slots=True)
class InfraOutcome:
    """Infrastructure exit classification bundle."""

    exit_category: str = ""


@dataclass(frozen=True, slots=True)
class ApiRetryOutcome:
    """API retry event accumulation bundle."""

    count: int = 0
    last_error: str = ""
    last_status: int | None = None
    exhausted: bool = False


@dataclass(frozen=True, slots=True)
class WriteEvidence:
    """Bundled write evidence signals — either explicitly constructed or absent."""

    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int = 0

    @classmethod
    def none_observed(cls) -> WriteEvidence:
        return cls(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=0,
        )

    @property
    def has_evidence(self) -> bool:
        return (
            self.write_call_count >= 1
            or self.fs_writes_detected
            or self.git_writes_detected
            or self.file_changes_count >= 1
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
    evidence: WriteEvidence = field(default_factory=WriteEvidence.none_observed)
    order_id: str = ""
    kill_reason: KillReason = KillReason.NATURAL_EXIT
    """Why the subprocess was (or was not) killed after the race loop.

    Surfaces from SubprocessResult so the formatter can annotate exit_code
    with the kill cause, resolving the "success=True + exit_code=-9" contradiction.
    """
    last_stop_reason: str = ""
    lifespan_started: bool = False
    """True when the headless session called an MCP tool (heuristic for server lifespan)."""
    provider: ProviderOutcome = field(default_factory=ProviderOutcome.none_used)
    """Provider execution outcome bundle."""
    infra: InfraOutcome = field(default_factory=InfraOutcome)
    """Infrastructure exit classification bundle."""
    api_retry: ApiRetryOutcome = field(default_factory=ApiRetryOutcome)
    """API retry event accumulation bundle."""

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
            "write_call_count": self.evidence.write_call_count,
            "fs_writes_detected": self.evidence.fs_writes_detected,
            "git_writes_detected": self.evidence.git_writes_detected,
            "file_changes_count": self.evidence.file_changes_count,
            "has_progress_evidence": self.has_progress_evidence,
            "last_stop_reason": self.last_stop_reason,
            "lifespan_started": self.lifespan_started,
            "provider_fallback": self.provider.fallback_activated,
            "provider_used": self.provider.provider_used,
            "infra_exit_category": self.infra.exit_category,
            "api_retry_count": self.api_retry.count,
            "api_retry_last_error": self.api_retry.last_error,
            "api_retry_last_status": self.api_retry.last_status,
            "api_retry_exhausted": self.api_retry.exhausted,
        }
        if self.worktree_path is not None:
            data["worktree_path"] = self.worktree_path
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
            evidence=WriteEvidence.none_observed(),
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

    @property
    def has_progress_evidence(self) -> bool:
        """Whether any evidence of meaningful progress exists."""
        return self.worktree_path is not None or self.evidence.has_evidence


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


class ModelTotalEntry(TypedDict):
    """Per-model aggregate token counts produced by compute_model_totals().

    In-memory only — never written to or read from disk; use TokenUsageFileEntry
    for the on-disk schema. Unlike TokenUsageFileEntry, this type carries only
    v2 canonical cache keys (cache_write_tokens, cache_read_tokens).
    """

    model: str
    step_count: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    elapsed_seconds: float


class TokenUsageFileEntry(TypedDict):
    """Schema contract for token_usage.json written by flush_session_log."""

    session_label: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    peak_context: int
    turn_count: int
    timing_seconds: float
    order_id: str
    loc_insertions: int
    loc_deletions: int
    provider_used: str
    model_identifier: str
    dispatch_id: str
    campaign_id: str
    schema_version: int


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
    codex_log: str | None  # path to Codex rollout NDJSON, or None for non-Codex sessions
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
    cache_write_tokens: int
    cache_read_tokens: int
    write_call_count: int
    fs_writes_detected: bool
    git_writes_detected: bool
    file_changes_count: int
    tracked_comm: str | None
    tracked_comm_drift: bool
    autoskillit_version: str
    claude_code_version: str
    codex_version: str
    recipe_name: str
    recipe_content_hash: str
    recipe_composite_hash: str
    recipe_version: str
    duration_seconds: float
    github_api_requests: int
    provider_used: str
    provider_fallback: bool
    caller_session_id: str
    api_retry_count: int
    api_retry_exhausted: bool
    api_retry_last_error: str
    api_retry_last_status: int | None
    schema_version: int
