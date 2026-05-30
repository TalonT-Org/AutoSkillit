"""Session content validation functions extracted from session.py.

Private sub-module — import from autoskillit.execution.session for public API.
"""

from __future__ import annotations

from collections.abc import Sequence

import regex as re

from autoskillit.core import get_logger
from autoskillit.execution.session._session_model import (
    FAILURE_SUBTYPES,
    ClaudeSessionResult,
    ContentState,
)

logger = get_logger(__name__)

# Bold + equals: **key** = value → key = value
_MARKDOWN_BOLD_EQUALS_RE: re.Pattern[str] = re.compile(
    r"\*\*{1,2}(\w[\w_-]*)\*{1,2}(\s*=)", re.MULTILINE
)
# Bold + colon: **key:** value OR **key**: value OR **key** : value → key = value
# Handles colon inside bold (**key:**), immediately after (**key**:), or spaced (**key** :).
_MARKDOWN_BOLD_COLON_RE: re.Pattern[str] = re.compile(
    r"\*\*(\w[\w_-]*)(?:\*{0,2}:\*{0,2}|\*{1,2}\s*:)\s+", re.MULTILINE
)
# Italic + equals: *key* = value → key = value
_MARKDOWN_ITALIC_EQUALS_RE: re.Pattern[str] = re.compile(
    r"(?<!\*)\*(\w[\w_-]*)\*(\s*=)", re.MULTILINE
)
# Italic + colon: *key*: value → key = value
_MARKDOWN_ITALIC_COLON_RE: re.Pattern[str] = re.compile(
    r"(?<!\*)\*(\w[\w_-]*)\*:\s+", re.MULTILINE
)
# Backtick: `key` = value → key = value
_MARKDOWN_BACKTICK_RE: re.Pattern[str] = re.compile(r"`(\w[\w_-]*)`(\s*=)", re.MULTILINE)
# HR-split delimiter: ---\n[/]name--- → ---[/]name--- (open and close delimiter variants)
_HR_SPLIT_DELIMITER_RE: re.Pattern[str] = re.compile(r"---\n+(/?\w[\w:.-]*---)")
# Bold-wrapped delimiter: **---[/]name---** or *---[/]name---* → ---[/]name---
_DELIMITER_BOLD_RE: re.Pattern[str] = re.compile(r"\*{1,2}(---/?\w[\w:.-]*---)\*{1,2}")
# Backtick-wrapped delimiter: `---[/]name---` → ---[/]name---
_DELIMITER_BACKTICK_RE: re.Pattern[str] = re.compile(r"`(---/?\w[\w:.-]*---)`")


def _collapse_hr_split_delimiters(text: str) -> str:
    """Collapse markdown HR + delimiter suffix into a contiguous delimiter token.

    When a model emits a markdown horizontal rule (``---``) on its own line
    immediately before the name portion of a ``---X---`` or ``---/X---`` token,
    the three leading hyphens land on a separate line and pattern matching fails.
    This function rejoins them so downstream regex searches find the full token.
    """
    return _HR_SPLIT_DELIMITER_RE.sub(r"---\1", text)


def _normalize_model_output(text: str) -> str:
    """Normalize model output formatting variations that interfere with pattern matching.

    Applies three sequential normalization stages:

    Stage 1 — HR-split collapse: rejoins ``---\\n[/]name---`` into ``---[/]name---``
    when the model emits a markdown horizontal rule immediately before a delimiter
    token name on a separate line.

    Stage 2 — Key=value decorator strip: removes bold/italic/backtick decorators
    from ``key = value`` structured output token names so ``**plan_path** = /path``
    is treated identically to ``plan_path = /path``.

    Stage 3 — Delimiter decorator strip: removes bold and backtick wrapping from
    ``---X---`` delimiter tokens so ``**---pipeline-health-result---**`` is treated
    identically to ``---pipeline-health-result---``.
    """
    text = _collapse_hr_split_delimiters(text)
    text = _MARKDOWN_BOLD_EQUALS_RE.sub(lambda m: m.group(1).lower() + m.group(2), text)
    text = _MARKDOWN_BOLD_COLON_RE.sub(lambda m: m.group(1).lower() + " = ", text)
    text = _MARKDOWN_ITALIC_EQUALS_RE.sub(lambda m: m.group(1).lower() + m.group(2), text)
    text = _MARKDOWN_ITALIC_COLON_RE.sub(lambda m: m.group(1).lower() + " = ", text)
    text = _MARKDOWN_BACKTICK_RE.sub(lambda m: m.group(1).lower() + m.group(2), text)
    text = _DELIMITER_BOLD_RE.sub(r"\1", text)
    text = _DELIMITER_BACKTICK_RE.sub(r"\1", text)
    return text


def _check_expected_patterns(result: str, patterns: Sequence[str]) -> bool:
    """Return True if ALL expected_output_patterns are found in result, or if
    no patterns are configured. This check MUST run on all session outcome paths,
    including the Channel B bypass path.

    AND semantics are intentional: patterns represent content contracts (e.g.,
    block start/end delimiters) that must all be present simultaneously.

    Normalizes model formatting variations on token names before matching,
    so ``**plan_path** = /path`` is treated identically to ``plan_path = /path``
    and ``---\\npipeline-health-result---`` is treated identically to
    ``---pipeline-health-result---``.

    If any pattern is an invalid regex, returns False rather than raising.
    """
    if not patterns:
        return True
    normalized = _normalize_model_output(result)
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
    prior_completion_markers: Sequence[str] | None = None,
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
        if completion_marker in result_text:
            marker_stripped = result_text.replace(completion_marker, "").strip()
            if not marker_stripped:
                logger.debug("content_check_failed", reason="result_is_only_marker")
                return False
        else:
            found_prior = False
            if prior_completion_markers:
                for prior_marker in prior_completion_markers:
                    if prior_marker and prior_marker in result_text:
                        marker_stripped = result_text.replace(prior_marker, "").strip()
                        if marker_stripped:
                            found_prior = True
                            break
            if not found_prior:
                if expected_output_patterns and _check_expected_patterns(
                    result_text, expected_output_patterns
                ):
                    logger.info(
                        "pattern_gated_success",
                        reason="marker_absent_contract_met",
                        pattern_count=len(expected_output_patterns),
                    )
                else:
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
    prior_completion_markers: Sequence[str] | None = None,
) -> ContentState:
    """Classify the content completeness and contract compliance of a session result.

    Returns:
        ContentState.COMPLETE: Result is non-empty, marker present (if configured),
            and all expected_output_patterns match. Session is fully successful.
        ContentState.ABSENT: Result is empty OR completion marker is absent from a
            non-empty result without pattern match. Drain-race artifact — retriable.
        ContentState.MARKER_ABSENT_CONTRACT_MET: Completion marker is absent but all
            expected_output_patterns match. Content contract satisfied despite missing
            marker — treated as successful by upstream callers.
        ContentState.CONTRACT_VIOLATION: Result is non-empty and contains the marker,
            but one or more expected_output_patterns are absent. The session ran to
            completion but the model did not produce the required output tokens.
            Terminal — retrying will not produce different output.
        ContentState.SESSION_ERROR: The CLI session itself reported an error
            (is_error=True) or produced a failure subtype. Terminal.
    """
    if session.is_error:
        return ContentState.SESSION_ERROR

    result = session.result.strip()

    if not result:
        return ContentState.ABSENT

    if not completion_marker and not expected_output_patterns:
        return ContentState.COMPLETE

    if completion_marker and completion_marker not in result:
        if not (
            prior_completion_markers
            and any(pm and pm in result for pm in prior_completion_markers)
        ):
            if expected_output_patterns and _check_expected_patterns(
                result, expected_output_patterns
            ):
                return ContentState.MARKER_ABSENT_CONTRACT_MET
            return ContentState.ABSENT

    if expected_output_patterns and not _check_expected_patterns(result, expected_output_patterns):
        return ContentState.CONTRACT_VIOLATION

    return ContentState.COMPLETE
