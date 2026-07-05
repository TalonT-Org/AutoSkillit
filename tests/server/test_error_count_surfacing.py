"""Tests for error count surfacing — the +N more indicator in validation responses.

R4: ensure that when validation produces >3 error-severity findings, the
user_visible_message includes a '+N more errors' indicator instead of silently
truncating beyond the first three.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_suggestions(n: int) -> list[dict[str, str]]:
    return [
        {
            "rule": f"rule-{i}",
            "severity": "error",
            "message": f"error number {i}",
        }
        for i in range(n)
    ]


def test_validation_error_response_surfaces_plus_n_more_for_excess_semantic_errors():
    """When >3 error-severity suggestions exist, message should indicate overflow."""
    suggestions = _make_suggestions(5)
    result: dict[str, object] = {"errors": [], "suggestions": suggestions}
    envelope = json.loads(_recipe_validation_error_response("recipe_x", result))
    msg = envelope["user_visible_message"]
    assert "+2 more errors" in msg, f"Expected '+2 more errors' indicator, got: {msg!r}"


def test_validation_error_response_no_indicator_at_exactly_three():
    """At exactly 3 errors, no +N more indicator is needed."""
    suggestions = _make_suggestions(3)
    result: dict[str, object] = {"errors": [], "suggestions": suggestions}
    envelope = json.loads(_recipe_validation_error_response("recipe_x", result))
    msg = envelope["user_visible_message"]
    assert "more errors" not in msg, f"Did not expect overflow indicator, got: {msg!r}"


def test_validation_error_response_counts_structural_errors_too():
    """Structural errors also contribute to the overflow count."""
    result: dict[str, object] = {
        "errors": ["e1", "e2", "e3", "e4", "e5"],
        "suggestions": [],
    }
    envelope = json.loads(_recipe_validation_error_response("recipe_x", result))
    msg = envelope["user_visible_message"]
    assert "+2 more errors" in msg, (
        f"Expected '+2 more errors' for 5 structural errors, got: {msg!r}"
    )


def test_validation_error_response_includes_one_when_4_structural_errors():
    """Boundary case: 4 structural errors yields '+1 more errors'."""
    result: dict[str, object] = {
        "errors": ["e1", "e2", "e3", "e4"],
        "suggestions": [],
    }
    envelope = json.loads(_recipe_validation_error_response("recipe_x", result))
    msg = envelope["user_visible_message"]
    assert "+1 more errors" in msg, f"Expected '+1 more errors' for 4 structural, got: {msg!r}"
