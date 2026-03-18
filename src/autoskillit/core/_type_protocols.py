"""Core Protocol definitions.

Zero autoskillit imports outside this sub-package. Provides all Protocol classes
for dependency injection and structural typing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_results import (
    CIRunScope,
    CleanupResult,
    FailureRecord,
    SkillResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
)
from ._type_subprocess import SubprocessResult

__all__ = [
    "GatePolicy",
    "AuditStore",
    "TokenStore",
    "TimingStore",
    "McpResponseStore",
    "TestRunner",
    "HeadlessExecutor",
    "RecipeRepository",
    "MigrationService",
    "DatabaseReader",
    "OutputPatternResolver",
    "WriteExpectedResolver",
    "WorkspaceManager",
    "CloneManager",
    "GitHubFetcher",
    "CIWatcher",
    "MergeQueueWatcher",
    "SessionSkillManager",
    "TargetSkillResolver",
]


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
        write_behavior: WriteBehaviorSpec | None = None,
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
class WriteExpectedResolver(Protocol):
    """Protocol for resolving write-expectation metadata from skill contracts."""

    def __call__(self, skill_command: str) -> WriteBehaviorSpec: ...


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
        stall_grace_period: int = 60,
        max_stall_retries: int = 3,
        not_in_queue_confirmation_cycles: int = 2,
    ) -> dict[str, Any]: ...

    async def toggle(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
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


@runtime_checkable
class TargetSkillResolver(Protocol):
    """Protocol for resolving skill names to their source tier."""

    def resolve(self, name: str) -> Any: ...
