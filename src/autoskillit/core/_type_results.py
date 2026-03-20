"""Core result dataclasses.

Zero autoskillit imports outside this sub-package. Provides LoadResult, SkillResult,
CleanupResult, and related dataclasses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar

from ._type_enums import RetryReason, SessionOutcome

T = TypeVar("T")

__all__ = [
    "LoadReport",
    "LoadResult",
    "ValidatedAddDir",
    "WriteBehaviorSpec",
    "FailureRecord",
    "SkillResult",
    "CleanupResult",
    "CIRunScope",
]


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
            "write_call_count": self.write_call_count,
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


@dataclass(frozen=True)
class CIRunScope:
    """Immutable scope parameters that uniquely identify which CI workflow runs are relevant.

    Passed as a single argument through the CIWatcher protocol so that adding a new
    scope axis (e.g. event: str | None) requires changing only this dataclass and the
    API params builder — not every method signature in the call chain.
    """

    workflow: str | None = None  # workflow filename, e.g. "tests.yml"
    head_sha: str | None = None  # commit SHA to pin results to
