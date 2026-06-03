"""Tests for the phoropter-phase-order and phoropter-step-interleaving semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test",
        description="test recipe",
        steps=steps,
    )


# --- phoropter-phase-order tests ---


def test_correct_order_produces_no_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_apply_before_dial_produces_error():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "apply"
    assert "expected phase 'dial'" in findings[0].message


def test_synthesize_before_apply_produces_error():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "synthesize"
    assert "expected phase 'apply'" in findings[0].message


def test_wrong_order_dial_synthesize_apply():
    """dial→synthesize→apply: synthesize arrives when apply is expected."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "synthesize"
    assert "expected phase 'apply'" in findings[0].message


def test_no_phoropter_family_produces_no_phase_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "step-a": RecipeStep(tool="run_cmd"),
            "step-b": RecipeStep(tool="run_skill"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_correct_order_with_trailing_non_family_step_no_findings():
    """Correctly-ordered family followed by a non-family step produces no phase-order findings."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "post-process": RecipeStep(tool="run_cmd"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_misordered_does_not_cascade():
    """A single misordering produces exactly one finding, not cascading errors."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "apply": RecipeStep(tool="run_skill", phoropter_family="f"),
            "dial": RecipeStep(tool="run_skill", phoropter_family="f"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="f"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 1
    assert findings[0].step_name == "apply"


# --- phoropter-step-interleaving tests ---


def test_non_family_step_between_dial_and_apply_produces_error():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "plain-step": RecipeStep(tool="run_cmd"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "plain-step"
    assert "vis-lens" in findings[0].message


def test_non_family_step_between_apply_and_synthesize_produces_error():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "plain-step": RecipeStep(tool="run_cmd"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "plain-step"
    assert "vis-lens" in findings[0].message


def test_no_interleaving_produces_no_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert not findings


def test_interleaving_after_synthesize_is_ok():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "plain-step": RecipeStep(tool="run_cmd"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert not findings


def test_no_phoropter_family_interleaving_no_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "step-a": RecipeStep(tool="run_cmd"),
            "step-b": RecipeStep(tool="run_skill"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert not findings


def test_out_of_order_synthesize_does_not_clear_family():
    """An out-of-order synthesize must not prematurely complete the family."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "plain-step": RecipeStep(tool="run_cmd"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 1
    assert findings[0].step_name == "plain-step"
    assert "vis-lens" in findings[0].message


# --- combined regression test ---


def test_minimal_recipe_no_phoropter_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "stop": RecipeStep(action="stop"),
        }
    )
    phoropter_rules = {"phoropter-phase-order", "phoropter-step-interleaving"}
    findings = [f for f in run_semantic_rules(recipe) if f.rule in phoropter_rules]
    assert not findings


# --- route-action transparency tests ---


def test_route_action_between_phases_is_transparent():
    """A route-action step between dial and apply within the same phoropter family
    must NOT produce a phase-order finding — it is transparent to phase tracking."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "gate": RecipeStep(action="route", phoropter_family="test-fam"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "done": RecipeStep(action="stop"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 0


def test_route_action_does_not_trigger_interleaving():
    """The phoropter-step-interleaving rule must NOT produce a false positive
    when a route-action step is inserted between dial and apply within the same family."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "gate": RecipeStep(action="route", phoropter_family="test-fam"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "done": RecipeStep(action="stop"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 0
