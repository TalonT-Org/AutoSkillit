"""Verify research.yaml captures and threads audit-trail variables."""

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RECIPE_PATH = Path("src/autoskillit/recipes/research.yaml")


def test_plan_visualization_captures_disambiguation_fields():
    """plan_visualization step captures disambiguation_rule_applied and tier_c_lens."""
    recipe = load_yaml(RECIPE_PATH)
    pv_step = recipe["steps"]["plan_visualization"]
    captures = pv_step.get("capture", {})
    assert "disambiguation_rule_applied" in captures
    assert "tier_c_lens" in captures


def test_review_design_captures_classification_timestamp():
    """review_design step captures classification_timestamp."""
    recipe = load_yaml(RECIPE_PATH)
    rd_step = recipe["steps"]["review_design"]
    captures = rd_step.get("capture", {})
    assert "classification_timestamp" in captures


def test_generate_report_receives_audit_fields():
    """generate_report step receives disambiguation, verdict, and timestamp flags."""
    recipe = load_yaml(RECIPE_PATH)
    gr_step = recipe["steps"]["generate_report"]
    skill_command = gr_step.get("with", {}).get("skill_command", "")
    for field in [
        "design-review-verdict",
        "disambiguation-rule-applied",
        "tier-c-lens",
        "classification-timestamp",
    ]:
        assert field in skill_command, f"generate_report skill_command must contain --{field}"
