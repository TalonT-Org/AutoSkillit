"""Structural tests: all six consuming recipes must wire closure ingredients to audit_impl."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "recipes"

_RECIPE_FILES = [
    "implementation.yaml",
    "implementation-groups.yaml",
    "remediation.yaml",
    "merge-prs.yaml",
    "research.yaml",
    "research-implement.yaml",
]


def _load_recipe(name: str) -> dict:
    path = _RECIPES_DIR / name
    data = load_yaml(path)
    assert isinstance(data, dict), f"{name} must parse as a mapping"
    return data


def _find_audit_impl_step(data: dict) -> dict:
    steps = data.get("steps", {})
    for step_def in steps.values():
        if not isinstance(step_def, dict):
            continue
        with_block = step_def.get("with", {})
        if not isinstance(with_block, dict):
            continue
        skill_command = with_block.get("skill_command", "")
        if isinstance(skill_command, str) and "audit-impl" in skill_command:
            return step_def
    raise AssertionError("No audit_impl step found")


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_all_six_recipes_have_closure_ingredients(recipe_name: str) -> None:
    """Each recipe has closure ingredients with empty string defaults."""
    data = _load_recipe(recipe_name)
    ingredients = data.get("ingredients", {})
    assert "closure_authority_path" in ingredients, (
        f"{recipe_name} must declare 'closure_authority_path' ingredient"
    )
    assert "closure_authority_hash" in ingredients, (
        f"{recipe_name} must declare 'closure_authority_hash' ingredient"
    )
    assert ingredients["closure_authority_path"].get("default") == "", (
        f"{recipe_name}: closure_authority_path default must be empty string"
    )
    assert ingredients["closure_authority_hash"].get("default") == "", (
        f"{recipe_name}: closure_authority_hash default must be empty string"
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_audit_impl_step_has_closure_with_params(recipe_name: str) -> None:
    """audit_impl step's with: block contains closure_authority_path and closure_authority_hash."""
    data = _load_recipe(recipe_name)
    step = _find_audit_impl_step(data)
    with_block = step.get("with", {})
    assert "closure_authority_path" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_authority_path' in with: block"
    )
    assert "closure_authority_hash" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_authority_hash' in with: block"
    )
    assert "closure_plan_paths" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_plan_paths' in with: block"
    )
    assert "closure_base_sha" in with_block, (
        f"{recipe_name} audit_impl step must have 'closure_base_sha' in with: block"
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_FILES)
def test_non_closure_preserves_routing(recipe_name: str) -> None:
    """When closure ingredients are empty, on_result routing is preserved."""
    data = _load_recipe(recipe_name)
    step = _find_audit_impl_step(data)
    on_result = step.get("on_result", [])
    assert isinstance(on_result, list), (
        f"{recipe_name} audit_impl step must have on_result as list"
    )
    assert len(on_result) > 0, f"{recipe_name} audit_impl step must have at least one routing rule"
