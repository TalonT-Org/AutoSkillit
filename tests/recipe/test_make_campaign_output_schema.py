"""Tests that example output from the make-campaign skill passes campaign validation.

Validates the validation paths that the skill exercises: structural, semantic
(depends-on-acyclic, dispatch-ingredients-keys-in-target-schema), and that a
minimal valid campaign YAML passes all rules.
"""

from __future__ import annotations

import pytest

import autoskillit.recipe  # noqa: F401 -- triggers rule registration
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _dispatch(name: str, recipe: str, task: str, **kwargs) -> CampaignDispatch:
    return CampaignDispatch(name=name, recipe=recipe, task=task, **kwargs)


def _minimal_campaign(**overrides) -> Recipe:
    defaults: dict = {
        "name": "my-feature-campaign",
        "description": "Campaign to implement and verify a feature",
        "kind": RecipeKind.CAMPAIGN,
        "dispatches": [
            _dispatch(
                name="phase-1-implement",
                recipe="implementation",
                task="Implement the core feature",
                ingredients={"task": "Implement the core feature"},
                depends_on=[],
            ),
            _dispatch(
                name="phase-2-verify",
                recipe="implementation",
                task="Add tests for the core feature",
                ingredients={"task": "Add tests for the core feature"},
                depends_on=["phase-1-implement"],
            ),
        ],
        "requires_recipe_packs": ["implementation-family"],
        "continue_on_failure": False,
        "kitchen_rules": ["NEVER modify files outside the project directory"],
    }
    defaults.update(overrides)
    return Recipe(**defaults)


def _findings_for_rule(recipe: Recipe, rule: str, **ctx_kwargs) -> list:
    ctx = make_validation_context(recipe, **ctx_kwargs)
    return [f for f in run_semantic_rules(ctx) if f.rule == rule]


def _error_findings(recipe: Recipe, **ctx_kwargs) -> list:
    """Return only ERROR-severity semantic findings."""
    from autoskillit.core import Severity

    ctx = make_validation_context(recipe, **ctx_kwargs)
    return [f for f in run_semantic_rules(ctx) if f.severity == Severity.ERROR]


# ---------------------------------------------------------------------------
# Test 12: minimal valid campaign passes structural + semantic validation
# ---------------------------------------------------------------------------


def test_minimal_campaign_yaml_validates() -> None:
    """Minimal campaign YAML matching skill output format passes structural validation."""
    campaign = _minimal_campaign()
    # Provide the recipe name as available so dispatch-recipe-exists doesn't fire
    errors = _error_findings(
        campaign, available_recipes=frozenset({"implementation"})
    )
    # Only remaining potential error would be dispatch-ingredients-keys-in-target-schema,
    # which is skipped when project_dir is None (no recipe file on disk to inspect).
    assert not errors, (
        f"Minimal campaign YAML must pass all semantic rules with no ERROR findings.\n"
        f"Got errors: {[(f.rule, f.message) for f in errors]}"
    )


# ---------------------------------------------------------------------------
# Test 13: cyclic depends_on triggers depends-on-acyclic rule
# ---------------------------------------------------------------------------


def test_campaign_with_cycle_fails_semantic_validation() -> None:
    """Cyclic depends_on detected by semantic rules (depends-on-acyclic)."""
    campaign = _minimal_campaign(
        dispatches=[
            _dispatch(
                name="phase-1",
                recipe="implementation",
                task="Step one",
                depends_on=["phase-2"],
            ),
            _dispatch(
                name="phase-2",
                recipe="implementation",
                task="Step two",
                depends_on=["phase-1"],
            ),
        ]
    )
    findings = _findings_for_rule(campaign, "depends-on-acyclic")
    assert findings, "Cyclic depends_on must trigger depends-on-acyclic rule"
    from autoskillit.core import Severity

    assert findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# Test 14: invalid ingredient key triggers dispatch-ingredients-keys-in-target-schema
# ---------------------------------------------------------------------------


def test_campaign_invalid_ingredient_key_detected(tmp_path) -> None:
    """Invalid ingredient key caught by semantic rule when available_recipes provided."""
    import yaml

    # Write a minimal target recipe to disk so _load_dispatch_target can inspect it
    # find_recipe_by_name looks in project_dir / ".autoskillit" / "recipes"
    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    target_recipe = {
        "name": "target-recipe",
        "description": "A simple recipe",
        "autoskillit_version": "0.9.0",
        "ingredients": {
            "task": {
                "description": "The task to perform",
                "required": True,
            },
        },
        "steps": {
            "run": {
                "tool": "run_cmd",
                "with": {"command": "echo done"},
                "on_success": "stop",
                "on_failure": "stop",
            },
            "stop": {"action": "stop", "message": "done"},
        },
        "kitchen_rules": ["NEVER"],
    }
    (recipe_dir / "target-recipe.yaml").write_text(
        yaml.dump(target_recipe, default_flow_style=False), encoding="utf-8"
    )

    campaign = _minimal_campaign(
        dispatches=[
            _dispatch(
                name="phase-1",
                recipe="target-recipe",
                task="Do the thing",
                ingredients={
                    "task": "Do it",
                    "nonexistent_key": "some-value",  # invalid key
                },
            ),
        ],
        requires_recipe_packs=["implementation-family"],
    )

    findings = _findings_for_rule(
        campaign,
        "dispatch-ingredients-keys-in-target-schema",
        available_recipes=frozenset({"target-recipe"}),
        project_dir=tmp_path,
    )
    assert findings, (
        "Invalid ingredient key must trigger dispatch-ingredients-keys-in-target-schema rule"
    )
    from autoskillit.core import Severity

    assert findings[0].severity == Severity.ERROR
    assert "nonexistent_key" in findings[0].message
