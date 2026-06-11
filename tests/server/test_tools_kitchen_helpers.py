"""Tests for tools_kitchen.py helper functions — error response envelope generation."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# _recipe_validation_error_response semantic-finding surfacing
# ---------------------------------------------------------------------------


def test_recipe_validation_error_response_surfaces_semantic_errors() -> None:
    """When result.errors is empty but suggestions contain error-severity findings,
    the response must surface the actual rule name and message in user_visible_message
    and error fields — not the generic "unknown structural error" fallback.
    """
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "errors": [],
        "suggestions": [
            {
                "severity": "error",
                "rule": "backend-incompatible-skill",
                "message": "step 'implement' requires git_metadata_writable backend",
            }
        ],
    }
    response_str = _recipe_validation_error_response("implementation", result)
    response = json.loads(response_str)

    assert "unknown structural error" not in response["user_visible_message"], (
        f"should surface semantic error, got: {response['user_visible_message']}"
    )
    assert "backend-incompatible-skill" in response["user_visible_message"], (
        f"user_visible_message should include rule name, got: {response['user_visible_message']}"
    )
    assert "implement" in response["user_visible_message"], (
        f"user_visible_message should include step name, got: {response['user_visible_message']}"
    )
    assert "unknown structural error" not in response["error"]


def test_recipe_validation_error_response_handles_missing_keys() -> None:
    """When a suggestion dict has severity=error but is missing rule or message keys,
    the function must NOT raise KeyError — must fall back gracefully with .get() defaults.
    """
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "errors": [],
        "suggestions": [
            {"severity": "error"},
            {"severity": "error", "rule": "partial-rule"},
        ],
    }
    response_str = _recipe_validation_error_response("test-recipe", result)
    response = json.loads(response_str)

    assert response["success"] is False
    assert response["kitchen"] == "failed"
    assert "unknown-rule" in response["user_visible_message"]
    assert "partial-rule" in response["user_visible_message"]


def test_recipe_validation_error_response_prefers_structural_errors() -> None:
    """When both result.errors and error-severity suggestions are present,
    structural errors take priority for the error_detail string.
    """
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "errors": ["schema: missing required field 'steps'"],
        "suggestions": [
            {
                "severity": "error",
                "rule": "backend-incompatible-skill",
                "message": "step X incompatible",
            }
        ],
    }
    response_str = _recipe_validation_error_response("test-recipe", result)
    response = json.loads(response_str)

    assert "schema: missing required field" in response["user_visible_message"]
    assert "backend-incompatible-skill" not in response["user_visible_message"]
