"""Tests for the ci-polling-inline-shell semantic rule."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for CI rule tests."""
    return Recipe(
        name="test-ci-rule",
        description="Test recipe for ci-polling-inline-shell rule.",
        version="0.2.0",
        kitchen_rules="Use wait_for_ci.",
        steps=steps,
    )


def test_inline_ci_polling_detected() -> None:
    """run_cmd step with gh run list/watch triggers ci-polling-inline-shell WARNING."""
    steps = {
        "ci_watch": RecipeStep(
            tool="run_cmd",
            with_args={
                "cmd": (
                    "run_id=$(gh run list --branch main --limit 1 "
                    '--json databaseId,status --jq ".[]" | head -1)\n'
                    'gh run watch "$run_id" --exit-status'
                ),
                "cwd": "/tmp",
            },
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 1
    assert ci_findings[0].severity == Severity.WARNING
    assert ci_findings[0].step_name == "ci_watch"
    assert "wait_for_ci" in ci_findings[0].message


def test_wait_for_ci_tool_not_flagged() -> None:
    """Steps using tool: wait_for_ci must not trigger ci-polling-inline-shell."""
    steps = {
        "ci_watch": RecipeStep(
            tool="wait_for_ci",
            with_args={"branch": "main", "timeout_seconds": 300},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_run_cmd_without_gh_not_flagged() -> None:
    """run_cmd steps without gh run commands must not trigger the rule."""
    steps = {
        "echo_step": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo hello"},
        ),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
    assert len(ci_findings) == 0


def test_bundled_recipes_no_inline_ci_polling() -> None:
    """All bundled recipes must be free of ci-polling-inline-shell findings."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        ci_findings = [f for f in findings if f.rule == "ci-polling-inline-shell"]
        assert len(ci_findings) == 0, (
            f"Recipe '{yaml_path.stem}' has inline CI polling: "
            + ", ".join(f.message for f in ci_findings)
        )
