"""Infrastructure exit classification for headless sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import (
    CODEX_CONTEXT_EXHAUSTION_MARKER,
    InfraExitCategory,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.core import BackendCapabilities, SubprocessResult
    from autoskillit.execution.session._session_model import ClaudeSessionResult

logger = get_logger(__name__)


__all__ = [
    "_CODEX_API_ERROR_PATTERNS",
    "_CODEX_CONTEXT_EXHAUSTION_PATTERN",
    "_CODEX_ERROR_CODE_API_STATUS",
    "_KNOWN_API_ERROR_PATTERNS",
    "_RATE_LIMIT_PATTERNS",
    "_RETRIABLE_API_STATUSES",
    "_API_ERROR_PATTERNS",
    "_has_model_capacity_error",
    "_all_text_sources",
    "classify_api_status",
    "classify_infra_exit",
    "has_rate_limit_signal",
    "is_signal_death_code",
]

_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"\b529\b"),
    re.compile(r"\b503\b"),
    re.compile(r"ECONNRESET", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"socket hang up", re.IGNORECASE),
    re.compile(r"network error", re.IGNORECASE),
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"socket connection was closed", re.IGNORECASE),  # Bun HTTP client
    re.compile(r"rate[\s_\-]limited", re.IGNORECASE),
)

_CODEX_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
    re.compile(r"\bserver_error\b", re.IGNORECASE),
    re.compile(r"insufficient_quota", re.IGNORECASE),
    re.compile(r"model_not_found", re.IGNORECASE),
)
_CODEX_ERROR_CODE_API_STATUS: dict[str, int] = {
    "rate_limit_exceeded": 429,
    "server_error": 500,
    "insufficient_quota": 429,
    "model_not_found": 404,
}

_KNOWN_API_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    _API_ERROR_PATTERNS + _CODEX_API_ERROR_PATTERNS
)

# Additive subset of _KNOWN_API_ERROR_PATTERNS covering transient rate-limit signals.
# Must NOT remove these patterns from _API_ERROR_PATTERNS or _CODEX_API_ERROR_PATTERNS —
# _session_model._has_api_error() imports _KNOWN_API_ERROR_PATTERNS and relies on the
# full union. This tuple exists only for the RATE_LIMITED classification branch.
_RATE_LIMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rate[\s_\-]limited", re.IGNORECASE),
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
)

_CODEX_CONTEXT_EXHAUSTION_PATTERN: re.Pattern[str] = re.compile(
    CODEX_CONTEXT_EXHAUSTION_MARKER, re.IGNORECASE
)
_MODEL_CAPACITY_ERROR: str = "Selected model is at capacity. Please try a different model."
_RETRIABLE_API_STATUSES: frozenset[int] = frozenset({408, 409})


def _has_model_capacity_error(
    session: ClaudeSessionResult,
    result: SubprocessResult,
    capabilities: BackendCapabilities,
) -> bool:
    """Match exact provider capacity evidence from backend-owned error channels."""
    if not capabilities.supports_model_capacity_error_detection:
        return False
    expected = _MODEL_CAPACITY_ERROR.casefold()
    return any(error.strip().casefold() == expected for error in session.errors) or any(
        line.strip().casefold() == expected for line in result.stderr.splitlines()
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


def has_rate_limit_signal(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> bool:
    """Check whether a session exhibits rate-limit signals (HTTP 429 or text patterns)."""
    if session.api_error_status == 429:
        return True
    return any(
        p.search(msg) for p in _RATE_LIMIT_PATTERNS for msg in _all_text_sources(session, result)
    )


def classify_api_status(status: int) -> InfraExitCategory:
    """Classify a provider HTTP status with an explicit retriable allowlist.

    A status below 400 is treated as a precondition violation (not an API
    error); callers that invoke this for transport-level signals must
    pre-filter on ``status >= 400``. The function returns
    ``API_ERROR_TERMINAL`` for that case so misuse is visible in tests rather
    than silently mis-classifying success responses.
    """
    if status < 400:
        return InfraExitCategory.API_ERROR_TERMINAL
    if status == 429:
        return InfraExitCategory.RATE_LIMITED
    if status in _RETRIABLE_API_STATUSES or status >= 500:
        return InfraExitCategory.API_ERROR
    return InfraExitCategory.API_ERROR_TERMINAL


_SHELL_SIGNAL_MAX = 192  # 128 + SIGRTMAX (64 on Linux)


def is_signal_death_code(returncode: int) -> bool:
    """Return True if returncode indicates death by signal.

    Covers both Python convention (negative: -(signal_number)) and shell
    convention (positive: 128 + signal_number, range 129–192).
    """
    return returncode < 0 or (128 < returncode <= _SHELL_SIGNAL_MAX)


def classify_infra_exit(
    session: ClaudeSessionResult,
    result: SubprocessResult,
    *,
    capabilities: BackendCapabilities,
) -> InfraExitCategory:
    """Classify why a headless session exited at the infrastructure level.

    Context exhaustion takes precedence. Structured provider status and error-code
    evidence follow, then rate-limit and API text patterns, process death, and the
    failed-session tail. This keeps terminal numeric evidence from being shadowed by
    broad substring matches.

    Context-exhaustion detection is gated by ``capabilities.supports_context_exhaustion_detection``
    so backends that do not implement it fall through to downstream classification.
    """
    if capabilities.supports_context_exhaustion_detection:
        if session._is_context_exhausted():
            return InfraExitCategory.CONTEXT_EXHAUSTED
        if _CODEX_CONTEXT_EXHAUSTION_PATTERN.search(result.stderr):
            return InfraExitCategory.CONTEXT_EXHAUSTED
    if session.api_error_status is not None and session.api_error_status >= 400:
        return classify_api_status(session.api_error_status)
    # Provider-error-code evidence: codes mapped in _CODEX_ERROR_CODE_API_STATUS
    # are routed through the API-status classifier so a transient
    # ``insufficient_quota``/``rate_limit_exceeded`` is not forced terminal.
    # Unmapped codes (e.g. ``authentication_failed``) remain terminal because
    # there is no known recovery path and downstream retries would waste budget.
    if session.provider_error_code:
        if session.provider_error_code in _CODEX_ERROR_CODE_API_STATUS:
            return classify_api_status(_CODEX_ERROR_CODE_API_STATUS[session.provider_error_code])
        return InfraExitCategory.API_ERROR_TERMINAL
    # Rate limit text remains useful when structured provider evidence is absent.
    if any(
        p.search(msg) for p in _RATE_LIMIT_PATTERNS for msg in _all_text_sources(session, result)
    ):
        return InfraExitCategory.RATE_LIMITED
    if _has_model_capacity_error(session, result, capabilities):
        return InfraExitCategory.API_ERROR
    if session._has_api_error() or any(p.search(result.stderr) for p in _KNOWN_API_ERROR_PATTERNS):
        return InfraExitCategory.API_ERROR
    # Separate guard: _has_api_error() scans message text for known patterns, but
    # api_retry_last_error can be "unknown" or another value not in _KNOWN_API_ERROR_PATTERNS.
    # In that case _has_api_error() returns False while api_retry_exhausted is still True.
    if session.api_retry_exhausted:
        return InfraExitCategory.API_ERROR
    if result.returncode is not None and is_signal_death_code(result.returncode):
        return InfraExitCategory.PROCESS_KILLED
    if session.is_error:
        return InfraExitCategory.UNCLASSIFIED
    return InfraExitCategory.COMPLETED
