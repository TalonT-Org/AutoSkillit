from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_missing_callable_input_rule_fires():
    """Semantic rule must ERROR when run_python step lacks required args."""
    recipe = _make_workflow({
        "init": {
            "tool": "run_python",
            "with": {"callable": "autoskillit.planner.create_run_dir"},
            "on_success": "done",
        },
        "done": {"action": "stop", "message": "Done"},
    })
    findings = run_semantic_rules(recipe)
    missing = [f for f in findings if f.rule == "missing-callable-input"]
    assert len(missing) >= 1
    assert all(f.severity == Severity.ERROR for f in missing)


def test_callable_signature_mismatch_rule_fires():
    """Semantic rule must ERROR when recipe args keys don't match callable signature."""
    recipe = _make_workflow({
        "expand": {
            "tool": "run_python",
            "with": {
                "callable": "autoskillit.planner.expand_assignments",
                "args": {"wrong_key": "x"},
            },
            "on_success": "done",
        },
        "done": {"action": "stop", "message": "Done"},
    })
    findings = run_semantic_rules(recipe)
    mismatch = [f for f in findings if f.rule == "callable-signature-mismatch"]
    assert len(mismatch) >= 1
    assert all(f.severity == Severity.ERROR for f in mismatch)
