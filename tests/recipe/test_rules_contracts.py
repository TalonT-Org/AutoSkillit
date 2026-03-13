"""Tests for the missing-output-patterns semantic rule."""

from __future__ import annotations

from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules


def test_rule_flags_skills_with_empty_output_patterns() -> None:
    """The missing-output-patterns rule exists and emits no warnings on bundled recipes."""
    recipe = load_recipe("implementation")
    if recipe is None:
        import pytest

        pytest.skip("implementation recipe not found")

    findings = run_semantic_rules(recipe)
    pattern_findings = [f for f in findings if f.rule == "missing-output-patterns"]
    assert isinstance(pattern_findings, list)
    # With all patterns populated, no warnings should fire
    assert not pattern_findings, (
        f"missing-output-patterns rule fired {len(pattern_findings)} warning(s): "
        + "; ".join(f.message for f in pattern_findings)
    )
