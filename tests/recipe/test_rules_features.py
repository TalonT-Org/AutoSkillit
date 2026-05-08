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
    import autoskillit.core.types._type_constants as _consts
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
    import autoskillit.core.types._type_constants as _consts
    from autoskillit.core import FeatureDef, FeatureLifecycle, Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    # A second test feature that controls 'ci' tools
    test_ci_fdef = FeatureDef(
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
    ctx = make_validation_context(recipe, disabled_features=frozenset({"fleet", "test-ci-gate"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]

    step_names = {f.step_name for f in findings}
    assert len(step_names) == 2, f"expected exactly 2 flagged steps, got {step_names!r}"
    assert "fleet_step" in step_names, "fleet tool not flagged"
    assert "ci_step" in step_names, "ci tool not flagged"
    assert all(f.severity == Severity.ERROR for f in findings)


def test_feature_gate_rule_fires_on_run_python_planner_callable() -> None:
    """ERROR when run_python callable starts with planner import_package and planner disabled."""
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "plan": RecipeStep(
                tool="run_python",
                with_args={"callable": "autoskillit.planner.validate_plan"},
            )
        },
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset({"planner"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert findings, "expected feature-gate-tool-reference finding for disabled planner callable"
    assert all(f.severity == Severity.ERROR for f in findings)
    assert any("planner" in f.message for f in findings)
    assert any("plan" in f.step_name for f in findings)


def test_feature_gate_rule_no_false_positive_run_python_non_feature_callable() -> None:
    """No finding when run_python callable does not belong to any disabled feature."""
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "smoke": RecipeStep(
                tool="run_python",
                with_args={"callable": "autoskillit.smoke_utils.check_something"},
            )
        },
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset({"planner"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert not findings, f"unexpected finding for non-feature callable: {findings}"


def test_feature_gate_run_python_no_finding_when_fdef_has_no_import_package(monkeypatch) -> None:
    """No false positive when feature has no import_package (import_package=None)."""
    import autoskillit.core.types._type_constants as _consts
    from autoskillit.core import FeatureDef, FeatureLifecycle
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    no_pkg_fdef = FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="Feature with no import_package",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
    )
    monkeypatch.setitem(_consts.FEATURE_REGISTRY, "no-pkg-feature", no_pkg_fdef)

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "s": RecipeStep(
                tool="run_python",
                with_args={"callable": "autoskillit.planner.validate_plan"},
            )
        },
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset({"no-pkg-feature"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "feature-gate-tool-reference"]
    assert not findings, f"unexpected finding when import_package is None: {findings}"


def test_provider_requires_profile_fires_on_missing_provider() -> None:
    from autoskillit.core import Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run_step": RecipeStep(
                tool="run_skill", provider="custom-provider", on_success="done"
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"other-provider"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert "custom-provider" in findings[0].message


def test_provider_requires_profile_passes_when_profile_matches() -> None:
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run_step": RecipeStep(tool="run_skill", provider="my-provider", on_success="done"),
            "done": RecipeStep(action="stop", message="Done."),
        },
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"my-provider"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_skips_when_no_profiles_configured() -> None:
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run_step": RecipeStep(tool="run_skill", provider="any-provider", on_success="done"),
            "done": RecipeStep(action="stop", message="Done."),
        },
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset())
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_skips_none_provider_steps() -> None:
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "run_step": RecipeStep(tool="run_skill", provider=None, on_success="done"),
            "done": RecipeStep(action="stop", message="Done."),
        },
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"some-profile"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_multiple_steps_mixed() -> None:
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={
            "no_provider_step": RecipeStep(
                tool="run_skill", provider=None, on_success="valid_step"
            ),
            "valid_step": RecipeStep(
                tool="run_skill", provider="valid", on_success="invalid_step"
            ),
            "invalid_step": RecipeStep(tool="run_skill", provider="invalid", on_success="done"),
            "done": RecipeStep(action="stop", message="Done."),
        },
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"valid"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert len(findings) == 1
    assert findings[0].step_name == "invalid_step"


def test_validation_context_provider_profiles_default() -> None:
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.schema import Recipe, RecipeStep

    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        steps={"done": RecipeStep(action="stop", message="Done.")},
    )
    ctx = make_validation_context(recipe)
    assert ctx.provider_profiles == frozenset()


def test_feature_gate_rule_fires_on_undeclared_requires_features() -> None:
    """Recipe using planner skill_command without requires_features: [planner] gets ERROR."""
    from autoskillit.core import SKILL_TOOLS, Severity
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    skill_tool = next(iter(SKILL_TOOLS))
    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        requires_features=[],
        steps={
            "s": RecipeStep(
                tool=skill_tool,
                with_args={"skill_command": "/autoskillit:planner-analyze"},
            )
        },
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset())
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "undeclared-feature-requirement"]
    assert findings, (
        "expected undeclared-feature-requirement finding for planner skill "
        "without requires_features"
    )
    assert all(f.severity == Severity.ERROR for f in findings)


def test_feature_gate_rule_passes_when_requires_features_declared() -> None:
    """No undeclared-feature-requirement finding when requires_features includes the feature."""
    from autoskillit.core import SKILL_TOOLS
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.registry import run_semantic_rules
    from autoskillit.recipe.schema import Recipe, RecipeStep

    skill_tool = next(iter(SKILL_TOOLS))
    recipe = Recipe(
        name="r",
        description="d",
        version="0.2.0",
        kitchen_rules="k",
        requires_features=["planner"],
        steps={
            "s": RecipeStep(
                tool=skill_tool,
                with_args={"skill_command": "/autoskillit:planner-analyze"},
            )
        },
    )
    ctx = make_validation_context(recipe, disabled_features=frozenset())
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "undeclared-feature-requirement"]
    assert not findings, f"unexpected findings: {findings}"
