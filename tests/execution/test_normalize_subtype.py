"""Unit tests for the _normalize_subtype() normalization gate."""

from __future__ import annotations

import pytest

from autoskillit.core.types import (
    SessionOutcome,
)
from autoskillit.execution.session import ClaudeSessionResult, _normalize_subtype

pytestmark = [pytest.mark.layer("execution")]


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
    result = _normalize_subtype("empty_output", SessionOutcome.SUCCEEDED, session, "")
    assert result == "success"


def test_normalize_succeeded_success_passthrough():
    """SUCCEEDED + 'success' → 'success' (passthrough)."""
    session = _session(subtype="success", result="output")
    result = _normalize_subtype("success", SessionOutcome.SUCCEEDED, session, "")
    assert result == "success"


# ---------------------------------------------------------------------------
# Non-"success" cli_subtype: passthrough regardless of outcome
# ---------------------------------------------------------------------------


def test_normalize_failed_error_during_execution_passthrough():
    """FAILED + 'error_during_execution' → 'error_during_execution' (passthrough)."""
    session = _session(subtype="error_during_execution", result="", is_error=True)
    result = _normalize_subtype("error_during_execution", SessionOutcome.FAILED, session, "")
    assert result == "error_during_execution"


def test_normalize_retriable_error_max_turns_passthrough():
    """RETRIABLE + 'error_max_turns' → 'error_max_turns' (passthrough)."""
    session = _session(subtype="error_max_turns", result="partial")
    result = _normalize_subtype("error_max_turns", SessionOutcome.RETRIABLE, session, "")
    assert result == "error_max_turns"


# ---------------------------------------------------------------------------
# Class 1 downward normalization: non-SUCCEEDED + "success" cli_subtype → synthetic
# ---------------------------------------------------------------------------


def test_normalize_failed_success_empty_result_returns_empty_result():
    """FAILED + 'success' + empty result → 'empty_result' (Class 1 fix)."""
    session = _session(subtype="success", result="", is_error=False)
    result = _normalize_subtype("success", SessionOutcome.FAILED, session, "")
    assert result == "empty_result"


def test_normalize_failed_success_missing_marker_returns_missing_completion_marker():
    """FAILED + 'success' + non-empty result + missing marker → 'missing_completion_marker'."""
    session = _session(subtype="success", result="I did the work.", is_error=False)
    result = _normalize_subtype("success", SessionOutcome.FAILED, session, "%%ORDER_UP%%")
    assert result == "missing_completion_marker"


def test_normalize_retriable_success_empty_result_returns_empty_result():
    """RETRIABLE + 'success' + empty result → 'empty_result'."""
    session = _session(subtype="success", result="", is_error=False)
    result = _normalize_subtype("success", SessionOutcome.RETRIABLE, session, "")
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
    result = _normalize_subtype("success", SessionOutcome.RETRIABLE, session, "%%ORDER_UP%%")
    assert result == "context_exhausted", (
        f"Expected 'context_exhausted', got '{result}'. "
        "JSONL-detected context exhaustion must not be masked as missing_completion_marker."
    )
