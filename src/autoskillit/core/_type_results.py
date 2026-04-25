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

from ._type_enums import KillReason, RetryReason, SessionOutcome

T = TypeVar("T")

__all__ = [
    "LoadReport",
    "LoadResult",
    "TestResult",
    "ValidatedAddDir",
    "WriteBehaviorSpec",
    "FailureRecord",
    "SkillResult",
    "CleanupResult",
    "CIRunScope",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
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
    """True when the L2 session invoked at least one MCP tool (heuristic for server lifespan)."""

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
            "last_stop_reason": self.last_stop_reason,
            "lifespan_started": self.lifespan_started,
        }
        if self.worktree_path is not None:
            data["worktree_path"] = self.worktree_path
        if self.order_id:
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
