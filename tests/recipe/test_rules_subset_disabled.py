"""Tests for subset-disabled-skill and subset-disabled-tool validation rules (T-VAL-001..007)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe")]


def _make_skill_recipe(skill_command: str):
    from autoskillit.recipe.schema import Recipe, RecipeStep

    return Recipe(
        name="test",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="run_skill", with_args={"skill_command": skill_command})},
    )


def test_subset_disabled_tool_finding() -> None:
    """T-VAL-001: subset-disabled-tool WARNING when github tool used with github disabled."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="fetch_github_issue", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_subsets=frozenset({"github"}))
    findings = run_semantic_rules(ctx)
    disabled = [f for f in findings if f.rule == "subset-disabled-tool"]
    assert disabled
    assert all(f.severity == Severity.WARNING for f in disabled)
    assert any("github" in f.message for f in disabled)


def test_subset_disabled_skill_finding() -> None:
    """T-VAL-002: subset-disabled-skill WARNING when github skill used with github disabled."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = _make_skill_recipe("/autoskillit:prepare-pr")
    ctx = make_validation_context(recipe, disabled_subsets=frozenset({"github"}))
    findings = run_semantic_rules(ctx)
    disabled = [f for f in findings if f.rule == "subset-disabled-skill"]
    assert disabled
    assert all(f.severity == Severity.WARNING for f in disabled)
    assert any("github" in f.message for f in disabled)


def test_no_finding_when_subset_not_referenced() -> None:
    """T-VAL-003: No subset-disabled finding when disabled subset not referenced by recipe."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules

    recipe = _make_skill_recipe("/autoskillit:investigate")  # no github category
    ctx = make_validation_context(recipe, disabled_subsets=frozenset({"github"}))
    findings = run_semantic_rules(ctx)
    assert not [f for f in findings if f.rule in ("subset-disabled-skill", "subset-disabled-tool")]


def test_unknown_tool_still_error_when_no_subsets_disabled() -> None:
    """T-VAL-004: unknown-tool ERROR for truly unknown tool unchanged when no subsets disabled."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="totally_unknown_tool", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_subsets=frozenset())
    findings = run_semantic_rules(ctx)
    unknown = [f for f in findings if f.rule == "unknown-tool"]
    assert unknown
    assert all(f.severity == Severity.ERROR for f in unknown)


def test_no_subset_disabled_tool_when_subset_enabled() -> None:
    """T-VAL-005: subset-disabled-tool not emitted when disabled_subsets is empty."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="fetch_github_issue", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_subsets=frozenset())
    findings = run_semantic_rules(ctx)
    assert not [f for f in findings if f.rule == "subset-disabled-tool"]


def test_disabled_subset_skill_vs_unknown_skill() -> None:
    """T-VAL-006: subset-disabled-skill vs unknown-skill-command are mutually exclusive."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules

    # A known skill in disabled subset → subset-disabled-skill, not unknown-skill-command
    recipe_disabled = _make_skill_recipe("/autoskillit:prepare-pr")
    ctx1 = make_validation_context(recipe_disabled, disabled_subsets=frozenset({"github"}))
    findings1 = run_semantic_rules(ctx1)
    rule_names1 = {f.rule for f in findings1}
    assert "subset-disabled-skill" in rule_names1
    assert "unknown-skill-command" not in rule_names1

    # A truly unknown skill → unknown-skill-command, not subset-disabled-skill
    recipe_unknown = _make_skill_recipe("/autoskillit:nonexistent-skill-xyz")
    ctx2 = make_validation_context(recipe_unknown, disabled_subsets=frozenset({"github"}))
    findings2 = run_semantic_rules(ctx2)
    rule_names2 = {f.rule for f in findings2}
    assert "unknown-skill-command" in rule_names2
    assert "subset-disabled-skill" not in rule_names2


def test_ci_subset_tool_disabled() -> None:
    """T-VAL-007: ci-subset tools produce subset-disabled-tool when ci is disabled."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="wait_for_ci", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_subsets=frozenset({"ci"}))
    findings = run_semantic_rules(ctx)
    disabled = [f for f in findings if f.rule == "subset-disabled-tool"]
    assert disabled
    assert any("ci" in f.message for f in disabled)
