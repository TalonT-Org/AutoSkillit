"""REQ-ENVELOPE-001: _recipe_validation_error_response must surface all error channels
from compute_recipe_validity (structural errors + semantic/contract findings).

Mirrors the test_hook_bridge_coverage pattern: when a new error channel is added to
LoadRecipeResult, this test forces the envelope to surface it.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_envelope_surfaces_both_structural_and_semantic_channels():
    """Envelope must reference at least one item from errors AND from suggestions."""
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
    assert "semantic-rule" in response["user_visible_message"]
