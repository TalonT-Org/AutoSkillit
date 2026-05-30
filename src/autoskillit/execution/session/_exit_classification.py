"""Infrastructure exit classification for headless sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import CODEX_CONTEXT_EXHAUSTION_MARKER, InfraExitCategory, get_logger

if TYPE_CHECKING:
    from autoskillit.core import SubprocessResult
    from autoskillit.execution.session._session_model import ClaudeSessionResult

logger = get_logger(__name__)

_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"\b529\b"),
    re.compile(r"\b503\b"),
    re.compile(r"ECONNRESET", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"socket hang up", re.IGNORECASE),
    re.compile(r"network error", re.IGNORECASE),
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"rate.limited", re.IGNORECASE),
)

_CODEX_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
    re.compile(r"\bserver_error\b", re.IGNORECASE),
    re.compile(r"insufficient_quota", re.IGNORECASE),
    re.compile(r"model_not_found", re.IGNORECASE),
)

_KNOWN_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    _API_ERROR_PATTERNS + _CODEX_API_ERROR_PATTERNS
)

# Additive subset of _KNOWN_API_ERROR_PATTERNS covering transient rate-limit signals.
# Must NOT remove these patterns from _API_ERROR_PATTERNS or _CODEX_API_ERROR_PATTERNS —
# _session_model._has_api_error() imports _KNOWN_API_ERROR_PATTERNS and relies on the
# full union. This tuple exists only for the RATE_LIMITED classification branch.
_RATE_LIMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate.limited", re.IGNORECASE),
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
)

_CODEX_CONTEXT_EXHAUSTION_PATTERN: re.Pattern[str] = re.compile(
    CODEX_CONTEXT_EXHAUSTION_MARKER, re.IGNORECASE
)


def _all_text_sources(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> list[str]:
    """Collect all searchable text from a session and subprocess result."""
    sources: list[str] = list(session.assistant_messages)
    if session.errors:
        sources.extend(session.errors)
    if session.result:
        sources.append(session.result)
    if result.stderr:
        sources.append(result.stderr)
    return sources


def classify_infra_exit(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> InfraExitCategory:
    """Classify why a headless session exited at the infrastructure level.

    Priority order: context exhaustion > rate limit > API error > process kill > completed.
    Context exhaustion takes precedence because it is more specific. Rate limit
    (HTTP 429) is checked before other API errors so transient rate limits are
    distinguished from structural failures and routed to on_rate_limit instead of
    on_context_limit.
    """
    if session._is_context_exhausted():
        return InfraExitCategory.CONTEXT_EXHAUSTED
    if _CODEX_CONTEXT_EXHAUSTION_PATTERN.search(result.stderr):
        return InfraExitCategory.CONTEXT_EXHAUSTED
    # Rate limit detection — must precede all API_ERROR checks (including
    # api_retry_exhausted) so 429s are classified as RATE_LIMITED even
    # when retries are exhausted.
    if session.api_error_status == 429:
        return InfraExitCategory.RATE_LIMITED
    if any(
        p.search(msg) for p in _RATE_LIMIT_PATTERNS for msg in _all_text_sources(session, result)
    ):
        return InfraExitCategory.RATE_LIMITED
    if session._has_api_error() or any(p.search(result.stderr) for p in _KNOWN_API_ERROR_PATTERNS):
        return InfraExitCategory.API_ERROR
    # Separate guard: _has_api_error() scans message text for known patterns, but
    # api_retry_last_error can be "unknown" or another value not in _KNOWN_API_ERROR_PATTERNS.
    # In that case _has_api_error() returns False while api_retry_exhausted is still True.
    if session.api_retry_exhausted:
        return InfraExitCategory.API_ERROR
    if session.api_error_status is not None and session.api_error_status >= 400:
        return InfraExitCategory.API_ERROR
    if result.returncode is not None and result.returncode < 0:
        return InfraExitCategory.PROCESS_KILLED
    return InfraExitCategory.COMPLETED
