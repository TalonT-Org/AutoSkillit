"""Tests for the optional-capture-requires-guard semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.core.types import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules import rules_optional_capture as _r
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for optional-capture-guard rule tests."""
    return Recipe(
        name="test-optional-capture-guard",
        description="Test recipe for optional-capture-requires-guard rule.",
        version="0.1.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def test_detects_optional_pattern_without_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule fires when a step with optional capture group routes on_success without a truthiness guard."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [{"name": "pr_url", "type": "string"}],
                "expected_output_patterns": ["pr_url\\s*=\\s*(https://.*)?"],
                "pattern_examples": ["pr_url = https://example.com/pull/1"],
                "write_behavior": "conditional",
                "write_expected_when": ["pr_url\\s*=\\s*https://"],
            }
        },
    }
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    recipe = _make_recipe(
        {
            "producer": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                capture={"pr_url": "${{ result.pr_url }}"},
                on_success="consumer",
            ),
            "consumer": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo ${{ context.pr_url }}"},
            ),
        }
    )

    findings = run_semantic_rules(recipe)
    guard_findings = [f for f in findings if f.rule == "optional-capture-requires-guard"]
    assert len(guard_findings) == 1, (
        f"expected 1 optional-capture-requires-guard finding, got {[f.message for f in guard_findings]}"
    )
    assert guard_findings[0].severity == Severity.WARNING
    assert guard_findings[0].step_name == "producer"
    assert "producer" in guard_findings[0].message
    assert "consumer" in guard_findings[0].message


def test_allows_optional_pattern_with_guard() -> None:
    """Rule does NOT fire when a guard step with truthiness check is interposed."""
    recipe = _make_recipe(
        {
            "producer": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                capture={"pr_url": "${{ result.pr_url }}"},
                on_success="guard_step",
            ),
            "guard_step": RecipeStep(
                action="route",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ context.pr_url }}", route="consumer"),
                        StepResultCondition(route="fallback"),
                    ]
                ),
            ),
            "consumer": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo ${{ context.pr_url }}"},
            ),
            "fallback": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo no pr"},
            ),
        }
    )

    findings = run_semantic_rules(recipe)
    guard_findings = [f for f in findings if f.rule == "optional-capture-requires-guard"]
    assert len(guard_findings) == 0, (
        f"expected 0 findings with guard interposed, got {[f.message for f in guard_findings]}"
    )


def test_allows_mandatory_pattern_without_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule does NOT fire when the skill's pattern has no optional group."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [{"name": "pr_url", "type": "string"}],
                "expected_output_patterns": ["pr_url\\s*=\\s*https://github\\.com/.+"],
                "pattern_examples": ["pr_url = https://github.com/owner/repo/pull/42"],
                "write_behavior": "always",
            }
        },
    }
    monkeypatch.setattr(_r, "load_bundled_manifest", lambda: manifest)

    recipe = _make_recipe(
        {
            "producer": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                capture={"pr_url": "${{ result.pr_url }}"},
                on_success="consumer",
            ),
            "consumer": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo ${{ context.pr_url }}"},
            ),
        }
    )

    findings = run_semantic_rules(recipe)
    guard_findings = [f for f in findings if f.rule == "optional-capture-requires-guard"]
    assert len(guard_findings) == 0, (
        f"expected 0 findings for mandatory pattern, got {[f.message for f in guard_findings]}"
    )


def test_implementation_yaml_no_diagnostic_after_guard_added() -> None:
    """Load implementation.yaml and assert no optional-capture-requires-guard diagnostic fires.

    After the guard_pr_url step is inserted, compose_pr should be protected by the guard.
    """
    recipe_path = pkg_root() / "recipes" / "implementation.yaml"
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    guard_findings = [f for f in findings if f.rule == "optional-capture-requires-guard"]
    assert len(guard_findings) == 0, (
        "expected 0 optional-capture-requires-guard findings on implementation.yaml, got "
        + "; ".join(f"[{f.step_name}]: {f.message}" for f in guard_findings)
    )
