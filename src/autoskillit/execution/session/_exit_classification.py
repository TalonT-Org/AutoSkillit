"""Infrastructure exit classification for headless sessions."""

from __future__ import annotations

import re

from autoskillit.core import InfraExitCategory, get_logger
from autoskillit.core.types import SubprocessResult
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
)


def _detect_api_error(stderr: str) -> bool:
    return any(p.search(stderr) for p in _API_ERROR_PATTERNS)


def classify_infra_exit(
    session: ClaudeSessionResult,
    result: SubprocessResult,
) -> InfraExitCategory:
    """Classify why a headless session exited at the infrastructure level.

    Priority order: context exhaustion > API error > process kill > completed.
    Context exhaustion takes precedence because it is more specific — an
    overloaded API response may co-occur with the final truncation error.
    """
    if session._is_context_exhausted():
        return InfraExitCategory.CONTEXT_EXHAUSTED
    if _detect_api_error(result.stderr):
        return InfraExitCategory.API_ERROR
    if result.returncode is not None and result.returncode < 0:
        return InfraExitCategory.PROCESS_KILLED
    return InfraExitCategory.COMPLETED
