"""REQ-ENVELOPE-001: _recipe_validation_error_response must surface all error channels
from compute_recipe_validity (structural errors + semantic/contract findings).

Mirrors the test_hook_bridge_coverage pattern: when a new error channel is added to
LoadRecipeResult, this test forces the envelope to surface it.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_envelope_prefers_structural_over_semantic_errors():
    """When both structural errors and semantic findings exist, structural errors
    take priority in user_visible_message — semantic findings are available in
    the suggestions field but do not pollute the primary error detail."""
    from autoskillit.server.tools.tools_kitchen import _recipe_validation_error_response

    result = {
        "valid": False,
        "errors": ["structural problem"],
        "suggestions": [
            {
                "severity": "error",
                "rule": "semantic-rule",
                "message": "semantic problem",
                "step": "s",
            },
        ],
    }
    response = json.loads(_recipe_validation_error_response("demo", result))
    assert "structural problem" in response["user_visible_message"]
    assert "semantic-rule" not in response["user_visible_message"]
    assert response["suggestions"][0]["rule"] == "semantic-rule"
