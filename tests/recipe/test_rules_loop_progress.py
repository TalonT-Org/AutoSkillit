"""Tests for recipe/rules_loop_progress.py semantic rules."""

from __future__ import annotations

import pytest

import autoskillit.recipe.rules.rules_loop_progress as _rlp
from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_MOCK_MANIFEST = {
    "version": "0.1.0",
    "skills": {
        "test-skill": {
            "inputs": [],
            "outputs": [{"name": "issues_fixed", "type": "string"}],
        }
    },
}


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for loop_progress rules tests."""
    return Recipe(
        name="test-loop-progress",
        description="Test recipe for loop progress rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


# ---------------------------------------------------------------------------
# loop-body-uncaptured-output
# ---------------------------------------------------------------------------


def test_rule_fires_when_skill_in_cycle_has_no_capture(monkeypatch) -> None:
    """Rule fires ERROR when run_skill step in a cycle has no capture despite declared outputs."""
    monkeypatch.setattr(_rlp, "load_bundled_manifest", lambda: _MOCK_MANIFEST)

    recipe = _make_recipe(
        {
            "validate": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo validate"}, on_success="check_verdict"
            ),
            "check_verdict": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo check"},
                on_success="refine",
            ),
            "refine": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="validate",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "loop-body-uncaptured-output"]
    assert len(loop_findings) == 1
    assert loop_findings[0].severity == Severity.ERROR
    assert loop_findings[0].step_name == "refine"


def test_rule_no_fire_when_skill_in_cycle_has_capture(monkeypatch) -> None:
    """Rule does NOT fire when a run_skill step in a cycle has a capture block."""
    monkeypatch.setattr(_rlp, "load_bundled_manifest", lambda: _MOCK_MANIFEST)

    recipe = _make_recipe(
        {
            "validate": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo validate"}, on_success="check_verdict"
            ),
            "check_verdict": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo check"},
                on_success="refine",
            ),
            "refine": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                capture={"count": "${{ result.issues_fixed }}"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="validate",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "loop-body-uncaptured-output"]
    assert len(loop_findings) == 0


def test_rule_no_fire_when_skill_has_no_outputs(monkeypatch) -> None:
    """Rule does NOT fire when a run_skill step has empty outputs declaration."""
    mock_manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [],
            }
        },
    }

    monkeypatch.setattr(_rlp, "load_bundled_manifest", lambda: mock_manifest)

    recipe = _make_recipe(
        {
            "validate": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo validate"}, on_success="refine"
            ),
            "refine": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:test-skill"},
                on_success="check_loop",
            ),
            "check_loop": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo loop"},
                on_success="validate",
            ),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "loop-body-uncaptured-output"]
    assert len(loop_findings) == 0


def test_rule_no_fire_for_non_skill_tool_in_cycle(monkeypatch) -> None:
    """Rule does NOT fire for run_cmd steps in cycles (only run_skill)."""
    monkeypatch.setattr(_rlp, "load_bundled_manifest", lambda: _MOCK_MANIFEST)

    recipe = _make_recipe(
        {
            "a": RecipeStep(tool="run_cmd", with_args={"cmd": "echo a"}, on_success="b"),
            "b": RecipeStep(tool="run_cmd", with_args={"cmd": "echo b"}, on_success="a"),
        }
    )
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "loop-body-uncaptured-output"]
    assert len(loop_findings) == 0


def test_planner_refine_capture_compliant() -> None:
    """planner-refine step in planner.yaml now has capture, so rule must NOT fire for it."""
    from pathlib import Path

    from autoskillit.recipe.io import load_recipe

    recipe_path = (
        Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "planner.yaml"
    )
    recipe = load_recipe(recipe_path)

    # The rule should not fire for the refine step since it now has capture
    findings = run_semantic_rules(recipe)
    loop_findings = [f for f in findings if f.rule == "loop-body-uncaptured-output"]
    refine_findings = [f for f in loop_findings if f.step_name == "refine"]
    assert len(refine_findings) == 0, (
        f"loop-body-uncaptured-output fired for 'refine' step: "
        f"{[f.message for f in refine_findings]}"
    )
