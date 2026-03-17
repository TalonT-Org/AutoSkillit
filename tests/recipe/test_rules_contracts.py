"""Tests for the missing-output-patterns semantic rule."""

from __future__ import annotations

import autoskillit.recipe.rules_contracts as _rc
from autoskillit.core.paths import pkg_root
from autoskillit.core.types import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep


def test_rule_flags_skills_with_empty_output_patterns() -> None:
    """The missing-output-patterns rule exists and emits no warnings on bundled recipes."""
    recipe_path = pkg_root() / "recipes" / "implementation.yaml"
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    pattern_findings = [f for f in findings if f.rule == "missing-output-patterns"]
    # With all patterns populated, no warnings should fire
    assert not pattern_findings, (
        f"missing-output-patterns rule fired {len(pattern_findings)} warning(s): "
        + "; ".join(f.message for f in pattern_findings)
    )


def test_pattern_examples_match_rule_fires_on_mismatch(monkeypatch) -> None:
    """pattern-examples-match fires as ERROR when pattern doesn't match any example."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "audit-impl": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": ["verdict\\s*=\\s*(GO|NO GO)"],
                "pattern_examples": ["verdict = NO_GO\n%%ORDER_UP%%"],  # underscore won't match
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "run_audit": RecipeStep(
                tool="run_skill",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "pattern-examples-match"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_missing_pattern_examples_rule_fires_when_examples_absent(monkeypatch) -> None:
    """missing-pattern-examples fires as WARNING when patterns exist but examples absent."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "audit-impl": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": ["verdict\\s*=\\s*(GO|NO GO)"],
                # No pattern_examples key
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "run_audit": RecipeStep(
                tool="run_skill",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "missing-pattern-examples"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.WARNING
