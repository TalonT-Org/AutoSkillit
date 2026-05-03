"""GitHub integration protocol definitions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ._type_results import CIRunScope

__all__ = ["GitHubFetcher", "CIWatcher", "MergeQueueWatcher"]


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
