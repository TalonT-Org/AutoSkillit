"""Tests for campaign recipe loader — _parse_recipe, list_campaign_recipes,
find_campaign_by_name, load_recipes_in_packs, and validate_recipe campaign branch."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.core import pkg_root
from autoskillit.recipe.io import (
    _parse_recipe,
    find_campaign_by_name,
    list_campaign_recipes,
    load_recipe,
    load_recipes_in_packs,
)
from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeKind, RecipeStep
from autoskillit.recipe.validator import validate_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


def _campaign_data(**overrides: object) -> dict:
    base: dict = {
        "name": "my-campaign",
        "description": "A test campaign",
        "kind": "campaign",
        "kitchen_rules": ["NEVER do bad things"],
        "dispatches": [
            {
                "name": "phase-one",
                "recipe": "implementation",
                "task": "Do the thing",
                "ingredients": {"task": "Do the thing"},
                "depends_on": [],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_recipe campaign field parsing
# ---------------------------------------------------------------------------


def test_parse_recipe_reads_campaign_kind():
    data = _campaign_data()
    recipe = _parse_recipe(data)
    assert recipe.kind == RecipeKind.CAMPAIGN


def test_parse_recipe_reads_dispatches():
    data = _campaign_data(
        dispatches=[
            {
                "name": "phase-a",
                "recipe": "implementation",
                "task": "First task",
                "ingredients": {"task": "First task"},
                "depends_on": [],
            },
            {
                "name": "phase-b",
                "recipe": "research",
                "task": "Second task",
                "ingredients": {},
                "depends_on": ["phase-a"],
            },
        ]
    )
    recipe = _parse_recipe(data)
    assert len(recipe.dispatches) == 2
    assert recipe.dispatches[0].name == "phase-a"
    assert recipe.dispatches[0].recipe == "implementation"
    assert recipe.dispatches[0].task == "First task"
    assert recipe.dispatches[0].ingredients == {"task": "First task"}
    assert recipe.dispatches[0].depends_on == []
    assert recipe.dispatches[1].name == "phase-b"
    assert recipe.dispatches[1].depends_on == ["phase-a"]


def test_parse_recipe_reads_campaign_metadata_fields():
    data = _campaign_data(
        categories=["implementation-family"],
        requires_recipe_packs=["implementation-family"],
        allowed_recipes=["special-recipe"],
        continue_on_failure=True,
    )
    recipe = _parse_recipe(data)
    assert recipe.categories == ["implementation-family"]
    assert recipe.requires_recipe_packs == ["implementation-family"]
    assert recipe.allowed_recipes == ["special-recipe"]
    assert recipe.continue_on_failure is True


def test_parse_recipe_defaults_campaign_fields_when_absent():
    data = {
        "name": "standard-recipe",
        "description": "No campaign fields",
        "kitchen_rules": ["NEVER"],
        "steps": {"stop": {"action": "stop", "message": "done"}},
    }
    recipe = _parse_recipe(data)
    assert recipe.kind == RecipeKind.STANDARD
    assert recipe.dispatches == []
    assert recipe.categories == []
    assert recipe.requires_recipe_packs == []
    assert recipe.allowed_recipes == []
    assert recipe.continue_on_failure is False


# ---------------------------------------------------------------------------
# list_campaign_recipes
# ---------------------------------------------------------------------------


def test_list_campaign_recipes_scans_campaigns_dir(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "my-campaign.yaml",
        _campaign_data(name="my-campaign"),
    )
    result = list_campaign_recipes(tmp_path)
    assert len(result.items) == 1
    assert result.items[0].name == "my-campaign"


def test_list_campaign_recipes_returns_empty_when_no_dir(tmp_path: Path):
    result = list_campaign_recipes(tmp_path)
    assert result.items == []


# ---------------------------------------------------------------------------
# find_campaign_by_name
# ---------------------------------------------------------------------------


def test_find_campaign_by_name_returns_match(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "my-campaign.yaml",
        _campaign_data(name="my-campaign"),
    )
    result = find_campaign_by_name("my-campaign", tmp_path)
    assert result is not None
    assert result.name == "my-campaign"


def test_find_campaign_by_name_returns_none_when_missing(tmp_path: Path):
    result = find_campaign_by_name("nonexistent", tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# load_recipes_in_packs
# ---------------------------------------------------------------------------


def test_load_recipes_in_packs_filters_by_categories(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "impl-campaign.yaml",
        _campaign_data(name="impl-campaign", categories=["implementation-family"]),
    )
    _write_yaml(
        campaigns_dir / "research-campaign.yaml",
        _campaign_data(name="research-campaign", categories=["research-family"]),
    )
    results = load_recipes_in_packs(frozenset({"implementation-family"}), tmp_path)
    assert len(results) == 1
    assert results[0].name == "impl-campaign"


def test_load_recipes_in_packs_includes_allowed_recipes(tmp_path: Path):
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    _write_yaml(
        campaigns_dir / "special-campaign.yaml",
        _campaign_data(name="special-campaign", categories=["research-family"]),
    )
    results = load_recipes_in_packs(
        frozenset({"implementation-family"}),
        tmp_path,
        allowed_recipe_names=frozenset({"special-campaign"}),
    )
    assert len(results) == 1
    assert results[0].name == "special-campaign"


# ---------------------------------------------------------------------------
# validate_recipe campaign branch
# ---------------------------------------------------------------------------


def test_validate_recipe_skips_step_check_for_campaign():
    recipe = Recipe(
        name="my-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[
            CampaignDispatch(name="phase-one", recipe="impl", task="Do it")
        ],
        steps={},
    )
    errors = validate_recipe(recipe)
    assert not any("step" in e.lower() for e in errors)


def test_validate_recipe_requires_dispatches_for_campaign():
    recipe = Recipe(
        name="my-campaign",
        description="test",
        kind=RecipeKind.CAMPAIGN,
        dispatches=[],
        steps={},
    )
    errors = validate_recipe(recipe)
    assert any("dispatch" in e.lower() for e in errors)


def test_validate_recipe_standard_recipe_still_requires_steps():
    recipe = Recipe(
        name="standard",
        description="test",
        kind=RecipeKind.STANDARD,
        steps={},
    )
    errors = validate_recipe(recipe)
    assert any("step" in e.lower() for e in errors)


def test_bundled_example_campaign_parseable():
    example_path = pkg_root() / "recipes" / "examples" / "example-campaign.yaml"
    recipe = load_recipe(example_path)
    assert recipe.kind == RecipeKind.CAMPAIGN
    assert len(recipe.dispatches) == 2
