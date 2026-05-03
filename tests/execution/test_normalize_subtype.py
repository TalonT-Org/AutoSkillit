"""Unit tests for ClaudeSessionResult.normalize_subtype() normalization gate."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    SessionOutcome,
)
from autoskillit.execution.session import ClaudeSessionResult

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _session(
    subtype: str = "success", result: str = "done", is_error: bool = False
) -> ClaudeSessionResult:
    return ClaudeSessionResult(
        subtype=subtype,
        result=result,
        is_error=is_error,
        session_id="s1",
    )


# ---------------------------------------------------------------------------
# Class 2 upward normalization: SUCCEEDED + error/diagnostic subtypes → "success"
# ---------------------------------------------------------------------------


def test_normalize_succeeded_empty_output_returns_success():
    """SUCCEEDED + 'empty_output' → 'success' (Class 2 fix)."""
    session = _session(subtype="empty_output", result="", is_error=False)
    result = session.normalize_subtype(SessionOutcome.SUCCEEDED, "")
    assert result == "success"


def test_normalize_succeeded_success_passthrough():
    """SUCCEEDED + 'success' → 'success' (passthrough)."""
    session = _session(subtype="success", result="output")
    result = session.normalize_subtype(SessionOutcome.SUCCEEDED, "")
    assert result == "success"


# ---------------------------------------------------------------------------
# Non-"success" cli_subtype: passthrough regardless of outcome
# ---------------------------------------------------------------------------


def test_normalize_failed_error_during_execution_passthrough():
    """FAILED + 'error_during_execution' → 'error_during_execution' (passthrough)."""
    session = _session(subtype="error_during_execution", result="", is_error=True)
    result = session.normalize_subtype(SessionOutcome.FAILED, "")
    assert result == "error_during_execution"


def test_normalize_retriable_error_max_turns_passthrough():
    """RETRIABLE + 'error_max_turns' → 'error_max_turns' (passthrough)."""
    session = _session(subtype="error_max_turns", result="partial")
    result = session.normalize_subtype(SessionOutcome.RETRIABLE, "")
    assert result == "error_max_turns"


# ---------------------------------------------------------------------------
# Class 1 downward normalization: non-SUCCEEDED + "success" cli_subtype → synthetic
# ---------------------------------------------------------------------------


def test_normalize_failed_success_empty_result_returns_empty_result():
    """FAILED + 'success' + empty result → 'empty_result' (Class 1 fix)."""
    session = _session(subtype="success", result="", is_error=False)
    result = session.normalize_subtype(SessionOutcome.FAILED, "")
    assert result == "empty_result"


def test_normalize_failed_success_missing_marker_returns_missing_completion_marker():
    """FAILED + 'success' + non-empty result + missing marker → 'missing_completion_marker'."""
    session = _session(subtype="success", result="I did the work.", is_error=False)
    result = session.normalize_subtype(SessionOutcome.FAILED, "%%ORDER_UP%%")
    assert result == "missing_completion_marker"


def test_normalize_retriable_success_empty_result_returns_empty_result():
    """RETRIABLE + 'success' + empty result → 'empty_result'."""
    session = _session(subtype="success", result="", is_error=False)
    result = session.normalize_subtype(SessionOutcome.RETRIABLE, "")
    assert result == "empty_result"


def test_normalize_retriable_success_jsonl_exhausted_returns_context_exhausted():
    """RETRIABLE + 'success' + jsonl_context_exhausted=True → 'context_exhausted'.

    Without this fix, the missing completion marker path returns 'missing_completion_marker',
    masking the true cause (context window exhaustion).
    """
    session = ClaudeSessionResult(
        subtype="success",
        is_error=False,
        result="partial work done but no marker",
        session_id="s1",
        jsonl_context_exhausted=True,
    )
    result = session.normalize_subtype(SessionOutcome.RETRIABLE, "%%ORDER_UP%%")
    assert result == "context_exhausted", (
        f"Expected 'context_exhausted', got '{result}'. "
        "JSONL-detected context exhaustion must not be masked as missing_completion_marker."
    )


# ---------------------------------------------------------------------------
# Path 2: is_error + errors list contains marker → 'context_exhausted'
# ---------------------------------------------------------------------------


def test_normalize_retriable_success_context_exhausted_via_errors_list():
    """Path 2: is_error=True + errors list contains marker → 'context_exhausted'."""
    from autoskillit.core.types._type_constants import CONTEXT_EXHAUSTION_MARKER

    session = ClaudeSessionResult(
        subtype="success",
        result="partial work but no completion marker",
        is_error=True,
        session_id="s1",
        errors=[f"Request failed: {CONTEXT_EXHAUSTION_MARKER}"],
        jsonl_context_exhausted=False,
    )
    assert session._is_context_exhausted(), "Path 2 must fire on this fixture"
    result = session.normalize_subtype(SessionOutcome.RETRIABLE, "%%ORDER_UP%%")
    assert result == "context_exhausted"


# ---------------------------------------------------------------------------
# Path 3: is_error + result text contains marker → 'context_exhausted'
# ---------------------------------------------------------------------------


def test_normalize_retriable_success_context_exhausted_via_result_text():
    """Path 3: is_error=True + result contains marker + subtype=success → 'context_exhausted'.

    This is the bug scenario: jsonl_context_exhausted is False (wrapped message format
    bypassed the flat-record scan), but _is_context_exhausted() fires via Path 3.
    The old code checked jsonl_context_exhausted directly and returned 'missing_completion_marker'.
    The new code delegates to _is_context_exhausted() and returns 'context_exhausted'.
    """
    from autoskillit.core.types._type_constants import CONTEXT_EXHAUSTION_MARKER

    session = ClaudeSessionResult(
        subtype="success",
        result=CONTEXT_EXHAUSTION_MARKER.capitalize(),  # "Prompt is too long"
        is_error=True,
        session_id="s1",
        jsonl_context_exhausted=False,  # Path 1 deliberately absent
    )
    assert session._is_context_exhausted(), "Path 3 must fire on this fixture"
    result = session.normalize_subtype(SessionOutcome.RETRIABLE, "%%ORDER_UP%%")
    assert result == "context_exhausted", (
        f"Expected 'context_exhausted', got '{result}'. "
        "normalize_subtype must delegate to _is_context_exhausted(), not read "
        "jsonl_context_exhausted directly."
    )
