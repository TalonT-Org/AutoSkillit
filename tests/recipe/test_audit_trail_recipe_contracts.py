"""Verify research.yaml captures and threads audit-trail variables."""

from pathlib import Path

import yaml

RECIPE_PATH = Path("src/autoskillit/recipes/research.yaml")


def test_plan_visualization_captures_disambiguation_fields():
    """plan_visualization step captures disambiguation_rule_applied and tier_c_lens."""
    recipe = yaml.safe_load(RECIPE_PATH.read_text())
    pv_step = recipe["steps"]["plan_visualization"]
    captures = pv_step.get("capture", {})
    assert "disambiguation_rule_applied" in captures
    assert "tier_c_lens" in captures


def test_review_design_captures_classification_timestamp():
    """review_design step captures classification_timestamp."""
    recipe = yaml.safe_load(RECIPE_PATH.read_text())
    rd_step = recipe["steps"]["review_design"]
    captures = rd_step.get("capture", {})
    assert "classification_timestamp" in captures


def test_generate_report_receives_audit_fields():
    """generate_report step receives disambiguation, verdict, and timestamp flags."""
    recipe = yaml.safe_load(RECIPE_PATH.read_text())
    gr_step = recipe["steps"]["generate_report"]
    skill_command = gr_step.get("with", {}).get("skill_command", "")
    for field in [
        "design-review-verdict",
        "disambiguation-rule-applied",
        "tier-c-lens",
        "classification-timestamp",
    ]:
        assert field in skill_command, f"generate_report skill_command must contain --{field}"
