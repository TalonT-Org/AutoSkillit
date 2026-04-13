"""Block fingerprint drift detection tests.

Tests in this file will FAIL against the pre-Part-B state where:
- BlockFingerprint dataclass does not exist in contracts.py
- generate_recipe_card does not accept a Recipe object directly
- check_contract_staleness does not accept a stored_card parameter
- RecipeCard does not have a block_fingerprints field

They become green once contracts.py gains BlockFingerprint, the Recipe-accepting
overloads, and the stored_card comparison path.
"""

from __future__ import annotations

import copy

import pytest

from autoskillit.recipe.contracts import (
    BlockFingerprint,
    RecipeCard,
    StaleItem,
    check_contract_staleness,
    generate_recipe_card,
)
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep


def test_recipe_card_contains_block_fingerprint_for_every_declared_block():
    """generate_recipe_card(recipe) must produce a RecipeCard with block_fingerprints
    covering every block declared in the recipe's steps."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    card = generate_recipe_card(recipe)
    block_names_in_recipe = {b.name for b in recipe.blocks}
    block_names_in_card = {fp.name for fp in card.block_fingerprints}
    assert block_names_in_card == block_names_in_recipe


def test_block_fingerprint_drift_triggers_staleness():
    """Given a stored card with block_fingerprints, mutate the recipe to add a run_cmd
    step in the same block, and check_contract_staleness must return a StaleItem naming
    the block with reason='block_composition_drift'."""
    original_recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    original_card = generate_recipe_card(original_recipe)
    mutated_recipe = _add_synthetic_run_cmd_to_block(original_recipe, "pre_queue_gate")
    stale = check_contract_staleness(mutated_recipe, stored_card=original_card)
    drift_items = [s for s in stale if s.reason == "block_composition_drift"]
    assert len(drift_items) >= 1
    assert any("pre_queue_gate" in s.skill for s in drift_items)


def test_block_fingerprint_unchanged_when_unrelated_step_added():
    """Adding a step OUTSIDE the declared block must not trigger block drift."""
    original_recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    original_card = generate_recipe_card(original_recipe)
    mutated_recipe = _add_synthetic_step_outside_any_block(original_recipe)
    stale = check_contract_staleness(mutated_recipe, stored_card=original_card)
    drift_items = [s for s in stale if s.reason == "block_composition_drift"]
    assert drift_items == []


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _add_synthetic_run_cmd_to_block(recipe: Recipe, block_name: str) -> Recipe:
    """Return a copy of recipe with an extra run_cmd step in the named block.

    The new step has no routing connections (it's a dead-end) to minimise
    impact on other validation rules.
    """
    new_step_name = f"_synthetic_run_cmd_{block_name}"
    new_step = RecipeStep(
        name=new_step_name,
        tool="run_cmd",
        with_args={"cmd": "echo synthetic", "step_name": new_step_name},
        block=block_name,
    )
    new_steps = dict(recipe.steps)
    new_steps[new_step_name] = new_step
    return Recipe(
        name=recipe.name,
        description=recipe.description,
        summary=recipe.summary,
        ingredients=recipe.ingredients,
        steps=new_steps,
        kitchen_rules=recipe.kitchen_rules,
        version=recipe.version,
        experimental=recipe.experimental,
        requires_packs=recipe.requires_packs,
    )


def _add_synthetic_step_outside_any_block(recipe: Recipe) -> Recipe:
    """Return a copy of recipe with an extra step that has NO block annotation.

    Adding an unblocked step must not affect any block fingerprints.
    """
    new_step_name = "_synthetic_unblocked_step"
    new_step = RecipeStep(
        name=new_step_name,
        action="stop",
        message="synthetic step outside any block",
    )
    new_steps = dict(recipe.steps)
    new_steps[new_step_name] = new_step
    return Recipe(
        name=recipe.name,
        description=recipe.description,
        summary=recipe.summary,
        ingredients=recipe.ingredients,
        steps=new_steps,
        kitchen_rules=recipe.kitchen_rules,
        version=recipe.version,
        experimental=recipe.experimental,
        requires_packs=recipe.requires_packs,
    )
