"""Logging and observer protocol definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_results import FailureRecord

__all__ = [
    "AuditLog",
    "TokenLog",
    "TimingLog",
    "McpResponseLog",
    "GitHubApiLog",
    "SupportsDebug",
    "SupportsLogger",
]


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
        loc_insertions: int = 0,
        loc_deletions: int = 0,
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

    def drain(self, session_id: str) -> dict[str, Any] | None: ...

    def clear(self) -> None: ...


# Not @runtime_checkable: structural (duck-typing) protocol; isinstance() checks not needed.
class SupportsDebug(Protocol):
    """Structural logger protocol — only the debug() method is required."""

    def debug(self, event: str, **kwargs: Any) -> None: ...


# Not @runtime_checkable: structural (duck-typing) protocol; isinstance() checks not needed.
class SupportsLogger(SupportsDebug, Protocol):
    """Structural logger protocol — debug() and error() methods required."""

    def error(self, event: str, **kwargs: Any) -> None: ...
