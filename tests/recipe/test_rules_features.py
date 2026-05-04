"""Tests for feature semantic rules: provider-requires-profile."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def test_provider_requires_profile_fires_on_missing_provider() -> None:
    recipe = _make_recipe(
        {
            "run_step": RecipeStep(
                tool="run_skill",
                provider="custom-provider",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        }
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"other-provider"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert "custom-provider" in findings[0].message


def test_provider_requires_profile_passes_when_profile_matches() -> None:
    recipe = _make_recipe(
        {
            "run_step": RecipeStep(
                tool="run_skill",
                provider="my-provider",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        }
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"my-provider"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_skips_when_no_profiles_configured() -> None:
    recipe = _make_recipe(
        {
            "run_step": RecipeStep(
                tool="run_skill",
                provider="any-provider",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        }
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset())
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_skips_none_provider_steps() -> None:
    recipe = _make_recipe(
        {
            "run_step": RecipeStep(
                tool="run_skill",
                provider=None,
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        }
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"some-profile"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert findings == []


def test_provider_requires_profile_multiple_steps_mixed() -> None:
    recipe = _make_recipe(
        {
            "no_provider_step": RecipeStep(
                tool="run_skill",
                provider=None,
                on_success="valid_step",
            ),
            "valid_step": RecipeStep(
                tool="run_skill",
                provider="valid",
                on_success="invalid_step",
            ),
            "invalid_step": RecipeStep(
                tool="run_skill",
                provider="invalid",
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        }
    )
    ctx = make_validation_context(recipe, provider_profiles=frozenset({"valid"}))
    findings = [f for f in run_semantic_rules(ctx) if f.rule == "provider-requires-profile"]
    assert len(findings) == 1
    assert findings[0].step_name == "invalid_step"


def test_validation_context_provider_profiles_default() -> None:
    recipe = _make_recipe({"done": RecipeStep(action="stop", message="Done.")})
    ctx = make_validation_context(recipe)
    assert ctx.provider_profiles == frozenset()
