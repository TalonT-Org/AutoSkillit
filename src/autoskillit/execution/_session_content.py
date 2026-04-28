"""Session content validation functions extracted from session.py.

Private sub-module — import from autoskillit.execution.session for public API.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from autoskillit.core import get_logger
from autoskillit.execution._session_model import (
    FAILURE_SUBTYPES,
    ClaudeSessionResult,
    ContentState,
)

logger = get_logger(__name__)

# Compiled once at module level — no per-call overhead
_MARKDOWN_TOKEN_RE: re.Pattern[str] = re.compile(r"\*{1,2}(\w[\w_-]*)\*{1,2}(\s*=)", re.MULTILINE)


def _strip_markdown_from_tokens(text: str) -> str:
    """Remove bold/italic markdown decorators from structured output token names.

    Transforms model output like:
        **plan_path** = /abs/path/plan.md
    into the canonical form:
        plan_path = /abs/path/plan.md

    Applied before regex pattern matching to make adjudication tolerant of the
    model's choice to visually style its output summary. Only `*word*` and
    `**word**` patterns adjacent to `=` are normalized — decorators elsewhere
    in the text are left unchanged.
    """
    return _MARKDOWN_TOKEN_RE.sub(r"\1\2", text)


def _check_expected_patterns(result: str, patterns: Sequence[str]) -> bool:
    """Return True if ALL expected_output_patterns are found in result, or if
    no patterns are configured. This check MUST run on all session outcome paths,
    including the Channel B bypass path.

    AND semantics are intentional: patterns represent content contracts (e.g.,
    block start/end delimiters) that must all be present simultaneously.

    Normalizes bold/italic markdown decorators on token names before matching,
    so ``**plan_path** = /path`` is treated identically to ``plan_path = /path``.

    If any pattern is an invalid regex, returns False rather than raising.
    """
    if not patterns:
        return True
    normalized = _strip_markdown_from_tokens(result)
    for p in patterns:
        try:
            if not re.search(p, normalized):
                return False
        except re.error:
            logger.warning("invalid_expected_output_pattern", pattern=p)
            return False
    return True


def _check_session_content(
    session: ClaudeSessionResult,
    completion_marker: str,
    expected_output_patterns: Sequence[str] = (),
) -> bool:
    """Validate session content fields after termination-specific gates pass."""
    if session.is_error:
        logger.debug("content_check_failed", reason="is_error", is_error=True)
        return False
    if not session.result.strip():
        logger.debug("content_check_failed", reason="empty_result")
        return False
    if session.subtype in FAILURE_SUBTYPES:
        logger.debug("content_check_failed", reason="failure_subtype", subtype=session.subtype)
        return False
    if completion_marker:
        result_text = session.result.strip()
        marker_stripped = result_text.replace(completion_marker, "").strip()
        if not marker_stripped:
            logger.debug("content_check_failed", reason="result_is_only_marker")
            return False
        if completion_marker not in result_text:
            logger.debug(
                "content_check_failed",
                reason="completion_marker_absent",
                result_tail=result_text[-200:] if len(result_text) > 200 else result_text,
            )
            return False
    if not _check_expected_patterns(session.result.strip(), expected_output_patterns):
        logger.warning(
            "content_check_failed",
            reason="expected_artifact_absent",
            patterns=list(expected_output_patterns),
        )
        return False
    logger.debug("content_check_passed")
    return True


def _evaluate_content_state(
    session: ClaudeSessionResult,
    completion_marker: str,
    expected_output_patterns: Sequence[str],
) -> ContentState:
    """Classify the content completeness and contract compliance of a session result.

    Returns:
        ContentState.COMPLETE: Result is non-empty, marker present (if configured),
            and all expected_output_patterns match. Session is fully successful.
        ContentState.ABSENT: Result is empty OR completion marker is absent from a
            non-empty result. Indicates a drain-race artifact — the session may have
            completed but stdout was not fully flushed. Retriable.
        ContentState.CONTRACT_VIOLATION: Result is non-empty and contains the marker,
            but one or more expected_output_patterns are absent. The session ran to
            completion but the model did not produce the required output tokens.
            Terminal — retrying will not produce different output.
        ContentState.SESSION_ERROR: The CLI session itself reported an error
            (is_error=True) or produced a failure subtype. Terminal.
    """
    # Process-level / CLI-level failure — terminal regardless of content
    if session.is_error:
        return ContentState.SESSION_ERROR

    result = session.result.strip()

    # Empty result — drain-race candidate regardless of content requirements.
    # This must come before the "no requirements" shortcut so that CHANNEL_A
    # dead-end guard can detect drain-race artifacts even when no marker or
    # patterns are configured.
    if not result:
        return ContentState.ABSENT

    # No content requirements configured and result is non-empty: CHANNEL_B
    # confirmation alone is sufficient. Returning COMPLETE here preserves the
    # existing behaviour for skills that produce non-empty output without a
    # marker (e.g. fire-and-forget commands with plain text output).
    if not completion_marker and not expected_output_patterns:
        return ContentState.COMPLETE

    # Marker absent — partial drain candidate
    if completion_marker and completion_marker not in result:
        return ContentState.ABSENT

    # Result non-empty, marker present (or not configured) — check pattern contract
    if expected_output_patterns and not _check_expected_patterns(result, expected_output_patterns):
        return ContentState.CONTRACT_VIOLATION

    return ContentState.COMPLETE
