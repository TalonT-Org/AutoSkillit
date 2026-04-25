"""Tests for feature-gate-tool-reference validation rule (T-FEAT-001..005)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def test_feature_gate_rule_fires_on_disabled_feature_tool() -> None:
    """Severity.ERROR when recipe uses dispatch_food_truck with fleet disabled."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="dispatch_food_truck", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset({"fleet"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert findings, "expected feature-gate-tool-reference finding for disabled fleet tool"
    assert all(f.severity == Severity.ERROR for f in findings)
    assert any("fleet" in f.message for f in findings)
    assert any("dispatch_food_truck" in f.message for f in findings)


def test_feature_gate_rule_passes_when_feature_enabled() -> None:
    """No feature-gate-tool-reference finding when disabled_features is empty."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="dispatch_food_truck", with_args={})},
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset())
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert not findings


def test_feature_gate_rule_ignores_non_feature_tools() -> None:
    """run_cmd has no feature tags — no finding even when fleet is disabled."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"s": RecipeStep(tool="run_cmd", with_args={"cmd": "echo hi"})},
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset({"fleet"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert not findings


def test_feature_gate_rule_fires_on_disabled_feature_skill(monkeypatch) -> None:
    """ERROR when recipe uses a skill from a feature's skill_categories."""
    import autoskillit.core._type_constants as _consts
    from autoskillit.core import FeatureDef, FeatureLifecycle, Severity
    from autoskillit.recipe._analysis import (
        ValidationContext,
        _build_step_graph,
        analyze_dataflow,
    )
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    # Inject a test feature with skill_categories into the registry
    test_fdef = FeatureDef(
        name="test-skill-gate",
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="Test feature gating a skill category",
        tool_tags=frozenset(),
        skill_categories=frozenset({"arch-lens"}),
        import_package=None,
    )
    monkeypatch.setitem(_consts.FEATURE_REGISTRY, "test-skill-gate", test_fdef)

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "s": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:arch-lens-c4-container"},
            )
        },
    )
    step_graph = _build_step_graph(recipe)
    ctx = ValidationContext(
        recipe=recipe,
        step_graph=step_graph,
        dataflow=analyze_dataflow(recipe, step_graph=step_graph),
        disabled_features=frozenset({"test-skill-gate"}),
        skill_category_map={"arch-lens-c4-container": frozenset({"arch-lens"})},
    )
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert findings, "expected feature-gate-tool-reference finding for disabled skill category"
    assert all(f.severity == Severity.ERROR for f in findings)
    assert any("test-skill-gate" in f.message for f in findings)


def test_feature_gate_rule_with_multiple_features(monkeypatch) -> None:
    """Each disabled feature independently flags its own tools."""
    import autoskillit.core._type_constants as _consts
    from autoskillit.core import FeatureDef, FeatureLifecycle, Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    # A second test feature that controls 'ci' tools
    test_ci_fdef = FeatureDef(
        name="test-ci-gate",
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="Test feature gating ci tools",
        tool_tags=frozenset({"ci"}),
        skill_categories=frozenset(),
        import_package=None,
    )
    monkeypatch.setitem(_consts.FEATURE_REGISTRY, "test-ci-gate", test_ci_fdef)

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "fleet_step": RecipeStep(tool="dispatch_food_truck", with_args={}),
            "ci_step": RecipeStep(tool="wait_for_ci", with_args={}),
        },
    )
    ctx = make_validation_context(
        recipe, disabled_features=frozenset({"fleet", "test-ci-gate"})
    )
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]

    step_names = {f.step_name for f in findings}
    assert len(step_names) == 2, f"expected exactly 2 flagged steps, got {step_names!r}"
    assert "fleet_step" in step_names, "fleet tool not flagged"
    assert "ci_step" in step_names, "ci tool not flagged"
    assert all(f.severity == Severity.ERROR for f in findings)
