"""Session-scoped GitHub API request accumulator.

Mirrors the tokens.py / timings.py pattern: a lock-guarded dict of entries keyed
by (order_id, step_name), aggregated on demand by to_usage(). Flushed to
github_api_usage.json at session log-write time via flush_session_log().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import regex as re

from autoskillit.pipeline.tokens import canonical_step_name


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
    step_name: str = field(default="")
    order_id: str = field(default="")


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


def _aggregate_entries(entries: list[GitHubApiEntry], session_id: str) -> dict[str, Any] | None:
    if not entries:
        return None
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    total_latency = 0.0
    min_remaining: int | None = None
    errors: dict[str, int] = {"4xx": 0, "5xx": 0}
    timestamps = [e.timestamp for e in entries if e.timestamp]
    for e in entries:
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
    count = len(entries)
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


class DefaultGitHubApiLog:
    """asyncio.Lock-guarded accumulator of GitHub API call entries."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries: dict[tuple[str, str], list[GitHubApiEntry]] = {}

    def _key(self, order_id: str, step_name: str) -> tuple[str, str]:
        return (order_id, canonical_step_name(step_name))

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
        step_name: str = "",
        order_id: str = "",
    ) -> None:
        key = self._key(order_id, step_name)
        entry = GitHubApiEntry(
            method=method,
            path=path,
            status_code=status_code,
            latency_ms=latency_ms,
            rate_limit_remaining=rate_limit_remaining,
            rate_limit_used=rate_limit_used,
            rate_limit_reset=rate_limit_reset,
            timestamp=timestamp,
            source="httpx",
            step_name=canonical_step_name(step_name),
            order_id=order_id,
        )
        async with self._lock:
            self._entries.setdefault(key, []).append(entry)

    async def record_gh_cli(
        self,
        *,
        subcommand: str,
        exit_code: int,
        latency_ms: float,
        timestamp: str,
        step_name: str = "",
        order_id: str = "",
    ) -> None:
        key = self._key(order_id, step_name)
        entry = GitHubApiEntry(
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
            step_name=canonical_step_name(step_name),
            order_id=order_id,
        )
        async with self._lock:
            self._entries.setdefault(key, []).append(entry)

    def to_usage(self, session_id: str) -> dict[str, Any] | None:
        all_entries: list[GitHubApiEntry] = [
            e for bucket in self._entries.values() for e in bucket
        ]
        return _aggregate_entries(all_entries, session_id)

    def to_usage_for_step(
        self, session_id: str, step_name: str, order_id: str
    ) -> dict[str, Any] | None:
        key = self._key(order_id, step_name)
        entries = self._entries.get(key, [])
        return _aggregate_entries(entries, session_id)

    def drain(self, session_id: str) -> dict[str, Any] | None:
        """Snapshot all entries as a usage dict and clear the accumulator.

        Dict replacement (not .clear()) is used so a concurrent record_* call that
        inserted into the old dict after drain() snapshotted it will appear in the
        next drain rather than being silently dropped.
        """
        usage = self.to_usage(session_id)
        self._entries = {}
        return usage

    def drain_step(self, session_id: str, step_name: str, order_id: str) -> dict[str, Any] | None:
        """Snapshot and remove only the entries for a specific (order_id, step_name) key."""
        key = self._key(order_id, step_name)
        entries = self._entries.pop(key, [])
        return _aggregate_entries(entries, session_id)

    def clear(self) -> None:
        self._entries = {}
