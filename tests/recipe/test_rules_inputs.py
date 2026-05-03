"""Tests for rules_inputs.py structural contracts."""

from __future__ import annotations

import ast
import pathlib

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe_with_skill_step(skill_command: str) -> Recipe:
    """Build a minimal Recipe with a single run_skill step."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": skill_command},
    )
    return Recipe(
        name="test-recipe",
        description="Test recipe for rule validation",
        steps={"review_step": step},
    )


def test_missing_recommended_input_fires_warning_when_not_passed():
    """A skill with recommended=True inputs should produce a WARNING
    when the recipe step's skill_command does not contain `name=` for them."""
    recipe = _make_recipe_with_skill_step(
        "/autoskillit:review-pr ${{ context.merge_target }} ${{ inputs.base_branch }}"
    )
    findings = run_semantic_rules(recipe)
    rec_findings = [f for f in findings if f.rule == "missing-recommended-input"]
    assert len(rec_findings) > 0, (
        "Expected at least one missing-recommended-input WARNING when "
        "annotated_diff_path= is not in skill_command"
    )
    assert all(f.severity == Severity.WARNING for f in rec_findings)


def test_missing_recommended_input_passes_when_input_provided():
    """No WARNING when the step's skill_command contains the recommended input as name=."""
    recipe = _make_recipe_with_skill_step(
        "/autoskillit:review-pr ${{ context.merge_target }} ${{ inputs.base_branch }} "
        "annotated_diff_path=${{ context.annotated_diff_path }} "
        "hunk_ranges_path=${{ context.hunk_ranges_path }}"
    )
    findings = run_semantic_rules(recipe)
    rec_findings = [f for f in findings if f.rule == "missing-recommended-input"]
    assert rec_findings == [], (
        f"Expected no missing-recommended-input findings when inputs are provided, "
        f"got: {rec_findings}"
    )


def test_rules_inputs_terminal_targets_match_schema():
    """rules_inputs.py unreachable-step rule uses the same sentinel set as schema."""
    from autoskillit.recipe.schema import _TERMINAL_TARGETS  # noqa: PLC0415

    # Verify schema has the expected sentinels (belt-and-suspenders check).
    assert "done" in _TERMINAL_TARGETS
    assert "escalate" in _TERMINAL_TARGETS

    # Structural check: rules_inputs.py must NOT hardcode sentinel strings via
    # .discard("done") or .discard("escalate"). It must use _TERMINAL_TARGETS instead.
    src_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "src/autoskillit/recipe/rules/rules_inputs.py"
    )
    src = src_path.read_text()
    tree = ast.parse(src)
    hardcoded_discards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "discard"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in ("escalate", "done")
    ]
    assert hardcoded_discards == [], (
        f"rules_inputs.py hardcodes {len(hardcoded_discards)} sentinel string(s) via "
        ".discard(). Use _TERMINAL_TARGETS from schema instead."
    )


def test_required_ingredient_without_default_emits_warning():
    """A recipe ingredient with required=True and no default must produce
    a validator WARNING, not pass silently."""
    from autoskillit.recipe.schema import RecipeIngredient

    recipe = Recipe(
        name="test-recipe",
        description="Test recipe",
        ingredients={
            "source_dir": RecipeIngredient(
                description="A required input",
                required=True,
                default=None,
                hidden=False,
            )
        },
        steps={
            "step1": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/test"},
            )
        },
    )
    findings = run_semantic_rules(recipe)
    warning_findings = [f for f in findings if f.rule == "required-ingredient-no-default"]
    assert len(warning_findings) > 0
    assert all(f.severity == Severity.WARNING for f in warning_findings)


def test_required_ingredient_with_default_no_warning():
    """A recipe ingredient with required=True and a default value should NOT
    produce a required-ingredient-no-default warning."""
    from autoskillit.recipe.schema import RecipeIngredient

    recipe = Recipe(
        name="test-recipe",
        description="Test recipe",
        ingredients={
            "source_dir": RecipeIngredient(
                description="A required input with default",
                required=True,
                default="some-value",
                hidden=False,
            )
        },
        steps={
            "step1": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/test"},
            )
        },
    )
    findings = run_semantic_rules(recipe)
    warning_findings = [f for f in findings if f.rule == "required-ingredient-no-default"]
    assert len(warning_findings) == 0


def test_required_hidden_ingredient_without_default_no_warning():
    """A hidden ingredient with required=True and no default should NOT trigger
    the warning because hidden ingredients are not shown to the agent."""
    from autoskillit.recipe.schema import RecipeIngredient

    recipe = Recipe(
        name="test-recipe",
        description="Test recipe",
        ingredients={
            "source_dir": RecipeIngredient(
                description="A hidden required input",
                required=True,
                default=None,
                hidden=True,
            )
        },
        steps={
            "step1": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/test"},
            )
        },
    )
    findings = run_semantic_rules(recipe)
    warning_findings = [f for f in findings if f.rule == "required-ingredient-no-default"]
    assert len(warning_findings) == 0


class TestResearchOutputModeEnum:
    def test_bogus_default_fires_error(self):
        recipe = Recipe(
            name="research",
            description="Test research recipe",
            ingredients={
                "output_mode": RecipeIngredient(
                    description="Output mode for research results",
                    default="bogus",
                )
            },
            steps={"done": RecipeStep(action="stop", message="Research complete.")},
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "research-output-mode-enum"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.ERROR

    def test_valid_pr_default_is_clean(self):
        recipe = Recipe(
            name="research",
            description="Test research recipe",
            ingredients={
                "output_mode": RecipeIngredient(
                    description="Output mode for research results",
                    default="pr",
                )
            },
            steps={"done": RecipeStep(action="stop", message="Research complete.")},
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "research-output-mode-enum"]
        assert len(findings) == 0

    def test_no_output_mode_ingredient_is_clean(self):
        recipe = Recipe(
            name="research",
            description="Test research recipe",
            ingredients={},
            steps={"done": RecipeStep(action="stop", message="Research complete.")},
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "research-output-mode-enum"]
        assert len(findings) == 0

    def test_non_research_recipe_is_clean(self):
        recipe = Recipe(
            name="other",
            description="Test other recipe",
            ingredients={
                "output_mode": RecipeIngredient(
                    description="Output mode",
                    default="bogus",
                )
            },
            steps={"done": RecipeStep(action="stop", message="Other recipe complete.")},
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "research-output-mode-enum"]
        assert len(findings) == 0
