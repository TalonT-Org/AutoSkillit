"""Core Protocol definitions.

Zero autoskillit imports outside this sub-package. Provides all Protocol classes
for dependency injection and structural typing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe, RecipeInfo

from ._type_results import (
    CIRunScope,
    CleanupResult,
    CloneResult,
    FailureRecord,
    LoadResult,
    SkillResult,
    TestResult,
    ValidatedAddDir,
    WriteBehaviorSpec,
)

__all__ = [
    "GateState",
    "AuditLog",
    "TokenLog",
    "TimingLog",
    "McpResponseLog",
    "GitHubApiLog",
    "TestRunner",
    "HeadlessExecutor",
    "RecipeRepository",
    "MigrationService",
    "DatabaseReader",
    "OutputPatternResolver",
    "WriteExpectedResolver",
    "ReadOnlyResolver",
    "WorkspaceManager",
    "CloneManager",
    "GitHubFetcher",
    "CIWatcher",
    "MergeQueueWatcher",
    "SessionSkillManager",
    "SkillLister",
    "SkillResolver",
    "BackgroundSupervisor",
    "FleetLock",
    "QuotaRefreshTask",
    "TokenFactory",
    "SupportsLogger",
    "SupportsDebug",
    "CampaignProtector",
]


@runtime_checkable
class GateState(Protocol):
    """Protocol for gate enable/disable state."""

    @property
    def enabled(self) -> bool: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...


@runtime_checkable
class AuditLog(Protocol):
    """Protocol for pipeline failure accumulation."""

    def record_failure(self, record: FailureRecord) -> None: ...

    def get_report(self) -> list[FailureRecord]: ...

    def get_report_as_dicts(self) -> list[dict[str, Any]]: ...

    def clear(self) -> None: ...

    def consecutive_failures(self, skill_command: str) -> int: ...

    def record_success(self, skill_command: str) -> None: ...

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
    ) -> int: ...


@runtime_checkable
class TokenLog(Protocol):
    """Protocol for per-step token usage accumulation."""

    def record(
        self,
        step_name: str,
        token_usage: dict[str, Any] | None,
        *,
        start_ts: str = "",
        end_ts: str = "",
        elapsed_seconds: float | None = None,
        order_id: str = "",
    ) -> None: ...

    def get_report(self, *, order_id: str = "") -> list[dict[str, Any]]: ...

    def compute_total(self, *, order_id: str = "") -> dict[str, Any]: ...

    def clear(self) -> None: ...

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
    ) -> int: ...


@runtime_checkable
class TimingLog(Protocol):
    """Protocol for per-step wall-clock timing accumulation."""

    def record(self, step_name: str, duration_seconds: float, *, order_id: str = "") -> None: ...

    def get_report(self, *, order_id: str = "") -> list[dict[str, Any]]: ...

    def compute_total(self, *, order_id: str = "") -> dict[str, Any]: ...

    def clear(self) -> None: ...

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
    ) -> int: ...


@runtime_checkable
class McpResponseLog(Protocol):
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
class GitHubApiLog(Protocol):
    """Protocol for session-scoped GitHub API request accumulation."""

    async def record_httpx(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        latency_ms: float,
        rate_limit_remaining: int,
        rate_limit_used: int,
        rate_limit_reset: int,
        timestamp: str,
    ) -> None: ...

    async def record_gh_cli(
        self,
        *,
        subcommand: str,
        exit_code: int,
        latency_ms: float,
        timestamp: str,
    ) -> None: ...

    def to_usage(self, session_id: str) -> dict[str, Any] | None: ...

    def clear(self) -> None: ...


@runtime_checkable
class TestRunner(Protocol):
    """Protocol for running a test suite and reporting pass/fail.

    Returns a TestResult with passed, stdout, and stderr from the test run.
    """

    async def run(self, cwd: Path) -> TestResult: ...


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
        kitchen_id: str = "",
        order_id: str = "",
        add_dirs: Sequence[ValidatedAddDir] = (),
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        expected_output_patterns: Sequence[str] = (),
        write_behavior: WriteBehaviorSpec | None = None,
        completion_marker: str = "",
        recipe_name: str = "",
        recipe_content_hash: str = "",
        recipe_composite_hash: str = "",
        recipe_version: str = "",
        allowed_write_prefix: str = "",
        readonly_skill: bool = False,
    ) -> SkillResult: ...

    async def dispatch_food_truck(
        self,
        orchestrator_prompt: str,
        cwd: str,
        *,
        completion_marker: str,
        model: str = "",
        step_name: str = "",
        kitchen_id: str = "",
        order_id: str = "",
        campaign_id: str = "",
        dispatch_id: str = "",
        project_dir: str = "",
        timeout: float | None = None,
        stale_threshold: float | None = None,
        idle_output_timeout: float | None = None,
        env_extras: Mapping[str, str] | None = None,
        requires_packs: Sequence[str] = (),
        on_spawn: Callable[[int, int], None] | None = None,
    ) -> SkillResult: ...


class SupportsDebug(Protocol):
    """Structural logger protocol — only the debug() method is required."""

    def debug(self, event: str, **kwargs: Any) -> None: ...


class SupportsLogger(SupportsDebug, Protocol):
    """Structural logger protocol — debug() and error() methods required."""

    def error(self, event: str, **kwargs: Any) -> None: ...


@runtime_checkable
class RecipeRepository(Protocol):
    """Protocol for recipe discovery and loading."""

    def find(self, name: str, project_dir: Path) -> RecipeInfo | None: ...

    def load(self, path: Path) -> Recipe: ...

    def list(self, project_dir: Path) -> LoadResult[RecipeInfo]: ...

    def load_and_validate(
        self,
        name: str,
        project_dir: Any,
        *,
        suppressed: Sequence[str] | None = None,
        resolved_defaults: dict[str, str] | None = None,
        ingredient_overrides: dict[str, str] | None = None,
        temp_dir: Path | None = None,
        temp_dir_relpath: str | None = None,
    ) -> dict[str, Any]: ...

    def validate_from_path(
        self, script_path: Any, temp_dir_relpath: str = ".autoskillit/temp"
    ) -> dict[str, Any]: ...

    def list_all(
        self,
        project_dir: Any | None = None,
        *,
        features: dict[str, bool] | None = None,
    ) -> dict[str, Any]: ...

    async def apply_triage_gate(
        self,
        result: dict[str, Any],
        recipe_name: str,
        recipe_info: Any,
        temp_dir: Path,
        logger: SupportsDebug,
        triage_fn: Callable[..., Awaitable[Sequence[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]: ...


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
class ReadOnlyResolver(Protocol):
    """Protocol for resolving whether a skill is read-only from skill contracts."""

    def __call__(self, skill_command: str) -> bool: ...


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
    ) -> CloneResult: ...

    def remove_clone(self, clone_path: str, keep: str = "false") -> dict[str, str]: ...

    def push_to_remote(
        self,
        clone_path: str,
        source_dir: str = "",
        branch: str = "",
        *,
        remote_url: str = "",
        protected_branches: list[str] | None = None,
        force: bool = False,
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
        issue_ref_or_owner: str,
        repo: str | None = None,
        number: int | None = None,
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

    async def update_issue_body(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        new_body: str,
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

    async def swap_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        remove_labels: list[str],
        add_labels: list[str],
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
        max_inconclusive_retries: int = 5,
        auto_merge_available: bool = True,
    ) -> dict[str, Any]: ...

    async def toggle(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
    ) -> dict[str, Any]: ...

    async def enqueue(
        self,
        pr_number: int,
        target_branch: str,
        repo: str | None = None,
        cwd: str = ".",
        auto_merge_available: bool = True,
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
        recipe_packs: frozenset[str] | None = None,
        allow_only: frozenset[str] | None = None,
    ) -> ValidatedAddDir: ...

    def compute_skill_closure(self, skill_name: str) -> frozenset[str]: ...

    def activate_skill_deps(self, session_id: str, skill_name: str) -> bool: ...

    def cleanup_session(self, session_id: str) -> bool: ...

    def cleanup_stale(self, max_age_seconds: int = 86400) -> int: ...


@runtime_checkable
class SkillResolver(Protocol):
    """Protocol for resolving skill names to their source tier."""

    def resolve(self, name: str) -> Any: ...


@runtime_checkable
class SkillLister(Protocol):
    """L0 contract for listing all available skills.

    Allows L2 recipe rules to type their skill-listing dependency
    against an L0 protocol instead of binding to the L1 workspace
    concrete class. The default implementation lives at
    autoskillit.workspace.skills.DefaultSkillResolver and satisfies this
    protocol structurally.
    """

    def list_all(self) -> list[Any]: ...


@runtime_checkable
class BackgroundSupervisor(Protocol):
    """Protocol for supervised background task execution."""

    @property
    def pending_count(self) -> int: ...

    def submit(
        self,
        coro: Any,
        *,
        on_exception: Any | None = None,
        status_path: Any | None = None,
        label: str = "",
    ) -> Any: ...

    async def drain(self) -> None: ...


@runtime_checkable
class QuotaRefreshTask(Protocol):
    """Protocol for a cancellable background task handle.

    Satisfied by asyncio.Task — used to type the kitchen-scoped quota
    refresh task stored in ToolContext without leaking asyncio.Task into the
    core layer.
    """

    def cancel(self, msg: Any = None) -> bool: ...


@runtime_checkable
class FleetLock(Protocol):
    """Protocol for a semaphore-style fleet dispatch guard.

    Default implementation is FleetSemaphore in server/_factory.py.
    """

    def at_capacity(self) -> bool: ...

    async def acquire(self) -> None: ...

    def release(self) -> None: ...

    @property
    def active_count(self) -> int: ...

    @property
    def max_concurrent(self) -> int: ...


@runtime_checkable
class TokenFactory(Protocol):
    """Protocol for resolving a GitHub token via the config → env → CLI fallback chain.

    Satisfied by any zero-argument callable that returns a token string or None.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self) -> str | None: ...


class CampaignProtector(Protocol):
    """Protocol for resolving the set of protected campaign IDs for session retention.

    Satisfied by any callable that accepts a project root Path and returns a frozenset
    of campaign ID strings that should not be purged during log retention.
    Set by make_context() on ToolContext; None in test ToolContext instances unless
    explicitly provided.
    """

    def __call__(self, project_dir: Path) -> frozenset[str]: ...
