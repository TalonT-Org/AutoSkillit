"""Bundled-recipe regression guard: every bundled recipe must validate cleanly.

Reproduces the capture-inversion false positive bug that blocked open_kitchen
when every skip_when_false ingredient resolves to "true". With R1's strict
forward-path dominance check, these recipes validate cleanly. R3 adds the
complementary default-pruned configuration to guard against regressions in the
non-truthy path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from autoskillit.recipe import all_validated_recipe_names, load_and_validate

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_ALL_TRUTHY_SKIP_INGREDIENTS: dict[str, str] = {
    "is_fleet_dispatch": "true",
    "adversarial_review_level": "true",
    "local_review_rounds": "true",
    "base_branch": "true",
    "post_run_diagnostics": "true",
}


def _all_truthy_overrides() -> dict[str, Any]:
    """Build overrides dict that forces every skip_when_false ingredient truthy."""
    overrides: dict[str, Any] = dict(_ALL_TRUTHY_SKIP_INGREDIENTS)
    overrides["task"] = "test task"
    overrides["issue_url"] = "https://github.com/test/test/issues/1"
    overrides["source_dir"] = str(_PROJECT_ROOT)
    return overrides


def _default_overrides() -> dict[str, Any]:
    """Build overrides dict with only required fields — skip_when_false ingredients at defaults."""
    return {
        "task": "test task",
        "issue_url": "https://github.com/test/test/issues/1",
        "source_dir": str(_PROJECT_ROOT),
    }


def _recipe_names() -> list[str]:
    return sorted(all_validated_recipe_names(_PROJECT_ROOT))


def _parametrize_recipes() -> list:
    return [pytest.param(n) for n in _recipe_names()]


@pytest.mark.parametrize("recipe_name", _parametrize_recipes(), ids=lambda n: n)
def test_bundled_recipe_validates_with_all_truthy_ingredients(
    recipe_name: str, tmp_path: Path
) -> None:
    """Every bundled recipe must validate under the all-truthy config."""
    cwd_before = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = load_and_validate(
            recipe_name,
            project_dir=_PROJECT_ROOT,
            ingredient_overrides=_all_truthy_overrides(),
        )
        errors = [s for s in result.get("suggestions", []) if s.get("severity") == "error"]
        assert result["valid"] is True, (
            f"{recipe_name} produced errors under all-truthy config: {errors}\n"
            f"result keys: {sorted(result.keys())}"
        )
        assert not errors, (
            f"{recipe_name} has error-severity findings under all-truthy config:\n"
            + "\n".join(f"  [{s.get('rule')}] {s.get('message')}" for s in errors)
        )
    finally:
        os.chdir(cwd_before)


@pytest.mark.parametrize("recipe_name", _parametrize_recipes(), ids=lambda n: n)
def test_bundled_recipe_validates_with_default_ingredients(
    recipe_name: str, tmp_path: Path
) -> None:
    """Every bundled recipe must validate under the default (non-truthy) config."""
    cwd_before = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = load_and_validate(
            recipe_name,
            project_dir=_PROJECT_ROOT,
            ingredient_overrides=_default_overrides(),
        )
        errors = [s for s in result.get("suggestions", []) if s.get("severity") == "error"]
        assert result["valid"] is True, (
            f"{recipe_name} produced errors under default config: {errors}\n"
            f"result keys: {sorted(result.keys())}"
        )
        assert not errors, (
            f"{recipe_name} has error-severity findings under default config:\n"
            + "\n".join(f"  [{s.get('rule')}] {s.get('message')}" for s in errors)
        )
    finally:
        os.chdir(cwd_before)
