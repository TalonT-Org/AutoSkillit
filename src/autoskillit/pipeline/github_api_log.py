"""Session-scoped GitHub API request accumulator.

Mirrors the tokens.py / timings.py pattern: a lock-guarded list of entries
aggregated on demand by to_usage(). Flushed to github_api_usage.json at
session log-write time via flush_session_log().
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GitHubApiEntry:
    method: str
    path: str
    status_code: int
    latency_ms: float
    rate_limit_remaining: int
    rate_limit_used: int
    rate_limit_reset: int
    timestamp: str
    source: str  # "httpx" or "gh_cli"
    subcommand: str = field(default="")


_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^/repos/[^/]+/[^/]+/issues"), "issues"),
    (re.compile(r"^/repos/[^/]+/[^/]+/pulls"), "pulls"),
    (re.compile(r"^/repos/[^/]+/[^/]+/actions"), "actions"),
    (re.compile(r"^/search/"), "search"),
    (re.compile(r"^/graphql$"), "graphql"),
]


def _categorize(path: str) -> str:
    for pattern, category in _CATEGORY_PATTERNS:
        if pattern.match(path):
            return category
    return "other"


class DefaultGitHubApiLog:
    """asyncio.Lock-guarded accumulator of GitHub API call entries."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries: list[GitHubApiEntry] = []

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
    ) -> None:
        async with self._lock:
            self._entries.append(
                GitHubApiEntry(
                    method=method,
                    path=path,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    rate_limit_remaining=rate_limit_remaining,
                    rate_limit_used=rate_limit_used,
                    rate_limit_reset=rate_limit_reset,
                    timestamp=timestamp,
                    source="httpx",
                )
            )

    async def record_gh_cli(
        self,
        *,
        subcommand: str,
        exit_code: int,
        latency_ms: float,
        timestamp: str,
    ) -> None:
        async with self._lock:
            self._entries.append(
                GitHubApiEntry(
                    method="",
                    path="",
                    status_code=exit_code,
                    latency_ms=latency_ms,
                    rate_limit_remaining=-1,
                    rate_limit_used=0,
                    rate_limit_reset=0,
                    timestamp=timestamp,
                    source="gh_cli",
                    subcommand=subcommand,
                )
            )

    def to_usage(self, session_id: str) -> dict[str, Any] | None:
        if not self._entries:
            return None
        by_category: dict[str, int] = {}
        by_source: dict[str, int] = {}
        total_latency = 0.0
        min_remaining: int | None = None
        errors: dict[str, int] = {"4xx": 0, "5xx": 0}
        timestamps = [e.timestamp for e in self._entries if e.timestamp]
        for e in self._entries:
            cat = _categorize(e.path) if e.source == "httpx" else "other"
            by_category[cat] = by_category.get(cat, 0) + 1
            by_source[e.source] = by_source.get(e.source, 0) + 1
            total_latency += e.latency_ms
            if e.source == "httpx" and e.rate_limit_remaining >= 0:
                if min_remaining is None or e.rate_limit_remaining < min_remaining:
                    min_remaining = e.rate_limit_remaining
            if e.source == "httpx":
                if 400 <= e.status_code < 500:
                    errors["4xx"] += 1
                elif 500 <= e.status_code < 600:
                    errors["5xx"] += 1
        count = len(self._entries)
        return {
            "session_id": session_id,
            "total_requests": count,
            "by_category": by_category,
            "by_source": by_source,
            "total_latency_ms": round(total_latency, 2),
            "avg_latency_ms": round(total_latency / count, 2) if count else 0.0,
            "min_rate_limit_remaining": min_remaining,
            "errors": errors,
            "first_request_ts": min(timestamps) if timestamps else None,
            "last_request_ts": max(timestamps) if timestamps else None,
        }

    def clear(self) -> None:
        self._entries = []
