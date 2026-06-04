"""Tests for the phoropter-phase-order and phoropter-step-interleaving semantic rules."""

from __future__ import annotations

from pathlib import Path

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


def test_non_canonical_phase_between_canonical_phases_is_transparent():
    """A run_python step with phoropter_family set but a non-canonical step key between
    dial and apply must NOT produce a phase-order or interleaving finding."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "select_review_dimensions": RecipeStep(tool="run_python", phoropter_family="test-fam"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "done": RecipeStep(action="stop"),
        }
    )
    phoropter_rules = {"phoropter-phase-order", "phoropter-step-interleaving"}
    findings = [f for f in run_semantic_rules(recipe) if f.rule in phoropter_rules]
    assert len(findings) == 0


# --- family prefix loader tests ---


def test_load_family_prefixes_vis_lens_has_prefix():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import (
        _PREFIXES_CACHE,
        _load_family_prefixes,
    )

    _PREFIXES_CACHE.clear()
    result = _load_family_prefixes()
    assert result["vis-lens"] == "vis"
    _PREFIXES_CACHE.clear()


def test_load_family_prefixes_review_design_key_absent():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import (
        _PREFIXES_CACHE,
        _load_family_prefixes,
    )

    _PREFIXES_CACHE.clear()
    result = _load_family_prefixes()
    assert result.get("review-design") is None
    _PREFIXES_CACHE.clear()


def test_load_family_prefixes_file_not_found_returns_empty(monkeypatch):
    from autoskillit.recipe.rules.rules_phoropter_adjacency import (
        _PREFIXES_CACHE,
        _load_family_prefixes,
    )

    def _raise_file_not_found(_):
        raise FileNotFoundError("mock")

    _PREFIXES_CACHE.clear()
    monkeypatch.setattr(
        "autoskillit.recipe.rules.rules_phoropter_adjacency.load_yaml",
        _raise_file_not_found,
    )
    result = _load_family_prefixes()
    assert result == {}
    _PREFIXES_CACHE.clear()


def test_load_family_prefixes_cache_hit(monkeypatch):
    from autoskillit.recipe.rules.rules_phoropter_adjacency import (
        _PREFIXES_CACHE,
        _load_family_prefixes,
        _load_registry_yaml,
    )

    _PREFIXES_CACHE.clear()
    call_count = 0
    original = _load_registry_yaml

    def counting_loader(path: Path) -> dict[str, str | None]:
        nonlocal call_count
        call_count += 1
        return original(path)

    monkeypatch.setattr(
        "autoskillit.recipe.rules.rules_phoropter_adjacency._load_registry_yaml",
        counting_loader,
    )
    _load_family_prefixes()
    _load_family_prefixes()
    assert call_count == 1
    _PREFIXES_CACHE.clear()


# --- _canonical_phase_for_step unit tests ---


def test_canonical_step_name_any_family():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": "vis"}
    assert _canonical_phase_for_step("dial", "vis-lens", prefixes) == "dial"
    assert _canonical_phase_for_step("apply", "vis-lens", prefixes) == "apply"
    assert _canonical_phase_for_step("synthesize", "vis-lens", prefixes) == "synthesize"


def test_canonical_step_name_family_none():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    assert _canonical_phase_for_step("dial", None, {}) == "dial"
    assert _canonical_phase_for_step("apply", None, {}) == "apply"
    assert _canonical_phase_for_step("synthesize", None, {}) == "synthesize"


def test_prefixed_match_correct_family():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": "vis"}
    assert _canonical_phase_for_step("vis_dial", "vis-lens", prefixes) == "dial"
    assert _canonical_phase_for_step("vis_apply", "vis-lens", prefixes) == "apply"
    assert _canonical_phase_for_step("vis_synthesize", "vis-lens", prefixes) == "synthesize"


def test_prefixed_name_none_prefix_entry():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": None}
    assert _canonical_phase_for_step("vis_dial", "vis-lens", prefixes) is None


def test_prefixed_name_family_none():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": "vis"}
    assert _canonical_phase_for_step("vis_dial", None, prefixes) is None


def test_unrecognized_step_name_returns_none():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": "vis"}
    assert _canonical_phase_for_step("select_review_dimensions", "vis-lens", prefixes) is None


# --- iterative-discovery pattern ---


def test_iterative_discovery_pattern():
    from autoskillit.recipe.rules.rules_phoropter_adjacency import _canonical_phase_for_step

    prefixes = {"vis-lens": "vis", "arch-lens": None}
    step_name = "vis_dial"
    found_family = None
    found_phase = None
    for candidate_family in prefixes:
        phase = _canonical_phase_for_step(step_name, candidate_family, prefixes)
        if phase is not None:
            found_family = candidate_family
            found_phase = phase
            break
    assert found_family == "vis-lens"
    assert found_phase == "dial"


# --- prefixed step phase-order tests ---


def test_prefixed_steps_correct_order_no_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "vis_dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "vis_apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "vis_synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_prefixed_steps_wrong_order_error_with_canonical():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "vis_apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "vis_dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "vis_synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert findings[0].step_name == "vis_apply"
    assert "vis_apply" in findings[0].message
    assert "apply" in findings[0].message


def test_prefixed_steps_family_none_transparency():
    """Prefixed steps with family=None are transparent via the outer family guard,
    not via _canonical_phase_for_step."""
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "vis_dial": RecipeStep(tool="run_skill"),
            "vis_apply": RecipeStep(tool="run_skill"),
            "vis_synthesize": RecipeStep(tool="run_skill"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_canonical_steps_regression_guard():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="review-design"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="review-design"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="review-design"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


def test_mixed_two_family_correct_order_no_findings():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="review-design"),
            "vis_dial": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="review-design"),
            "vis_apply": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="review-design"),
            "vis_synthesize": RecipeStep(tool="run_skill", phoropter_family="vis-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-phase-order"]
    assert not findings


# --- canonical-entry interleaving tests ---


def test_non_canonical_family_step_no_interleaving():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "select_review_dimensions": RecipeStep(tool="run_python", phoropter_family="test-fam"),
            "plain-step": RecipeStep(tool="run_cmd"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert not findings


def test_sequential_family_exemption():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="fam-a"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="fam-a"),
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="fam-a"),
            "routing-step": RecipeStep(tool="run_cmd"),
            "refactor_dial": RecipeStep(tool="run_skill", phoropter_family="refactor-lens"),
            "refactor_apply": RecipeStep(tool="run_skill", phoropter_family="refactor-lens"),
            "refactor_synthesize": RecipeStep(tool="run_skill", phoropter_family="refactor-lens"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert not findings


def test_in_progress_interleaving_still_fires():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "dial": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "apply": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "plain-step": RecipeStep(tool="run_cmd"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 1
    assert findings[0].step_name == "plain-step"
    assert "test-fam" in findings[0].message


def test_standalone_synthesize_does_not_complete_family():
    import autoskillit.recipe  # noqa: F401

    recipe = _make_recipe(
        {
            "synthesize": RecipeStep(tool="run_skill", phoropter_family="test-fam"),
            "plain-step": RecipeStep(tool="run_cmd"),
        }
    )
    findings = [f for f in run_semantic_rules(recipe) if f.rule == "phoropter-step-interleaving"]
    assert len(findings) == 1
    assert findings[0].step_name == "plain-step"
    assert "test-fam" in findings[0].message
