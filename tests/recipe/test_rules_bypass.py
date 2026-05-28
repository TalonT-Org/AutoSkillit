"""Tests for skip_when_false bypass routing semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

# ---------------------------------------------------------------------------
# skip_when_false bypass routing tests
# ---------------------------------------------------------------------------


def test_optional_without_skip_when_fires_error() -> None:
    """optional: true without skip_when_false must be an ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
                note="Optional step.",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_optional_with_skip_when_does_not_fire() -> None:
    """optional: true WITH skip_when_false must not fire the optional-without-skip-when rule."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.run_audit",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "run_audit": RecipeIngredient(description="", required=False, default="true")
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "optional-without-skip-when"]
    assert rule_findings == []


def test_skip_when_false_referencing_undeclared_ingredient_fires() -> None:
    """skip_when_false must reference a declared ingredient; undeclared must fire ERROR."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="opt_step"),
            "opt_step": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.nonexistent_ingredient",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        # "nonexistent_ingredient" is NOT in ingredients
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    rule_findings = [v for v in violations if v.rule == "skip-when-false-undeclared"]
    assert len(rule_findings) == 1


def test_skip_when_false_on_hidden_ingredient_fires_warning() -> None:
    """skip-when-false-on-hidden rule fires WARNING when ingredient is hidden: true."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="guarded"),
            "guarded": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.bar",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:foo /tmp/x.md", "cwd": "/tmp"},
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "bar": RecipeIngredient(description="Hidden flag", default="false", hidden=True)
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    findings = [v for v in violations if v.rule == "skip-when-false-on-hidden"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING


def test_skip_when_false_on_visible_ingredient_no_warning() -> None:
    """skip-when-false-on-hidden rule does NOT fire when ingredient is visible."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="guarded"),
            "guarded": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.bar",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:foo /tmp/x.md", "cwd": "/tmp"},
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "bar": RecipeIngredient(description="Visible flag", default="false", hidden=False)
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    findings = [v for v in violations if v.rule == "skip-when-false-on-hidden"]
    assert findings == []


# ---------------------------------------------------------------------------
# skip-when-false-on-non-boolean tests
# ---------------------------------------------------------------------------


def test_skip_when_false_on_non_boolean_ingredient_fires_warning() -> None:
    """skip-when-false-on-non-boolean fires WARNING when default is not 'true'/'false'."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="guarded"),
            "guarded": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.issue_url",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:foo /tmp/x.md", "cwd": "/tmp"},
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "issue_url": RecipeIngredient(
                description="GitHub issue URL", required=False, default=None
            )
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    findings = [v for v in violations if v.rule == "skip-when-false-on-non-boolean"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARNING
    assert "issue_url" in findings[0].message


def test_skip_when_false_on_boolean_ingredient_no_warning() -> None:
    """skip-when-false-on-non-boolean does NOT fire when default is 'true' or 'false'."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="guarded"),
            "guarded": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.open_pr",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:foo /tmp/x.md", "cwd": "/tmp"},
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "open_pr": RecipeIngredient(description="Open a PR", required=False, default="true")
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    findings = [v for v in violations if v.rule == "skip-when-false-on-non-boolean"]
    assert findings == []


def test_skip_when_false_on_required_no_default_no_warning() -> None:
    """skip-when-false-on-non-boolean does NOT fire when ingredient is required with no default."""
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="guarded"),
            "guarded": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.target",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:foo /tmp/x.md", "cwd": "/tmp"},
                on_context_limit="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        ingredients={
            "target": RecipeIngredient(description="Target", required=True, default=None)
        },
        kitchen_rules=["test"],
    )
    violations = run_semantic_rules(recipe)
    findings = [v for v in violations if v.rule == "skip-when-false-on-non-boolean"]
    assert findings == []
