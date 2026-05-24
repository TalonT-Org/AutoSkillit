"""Regression test for #2919: dry-walkthrough output_dir must not exclude plan locations."""

from __future__ import annotations

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe.io import load_recipe


_RECIPES_WITH_DRY_WALKTHROUGH = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


@pytest.mark.parametrize("recipe_name", _RECIPES_WITH_DRY_WALKTHROUGH)
def test_dry_walkthrough_output_dir_does_not_scope_to_subdirectory(
    recipe_name: str,
) -> None:
    """output_dir on dry-walkthrough steps must cover make-plan/ and rectify/ locations.

    If output_dir scopes to .../dry-walkthrough/, the write guard blocks in-place
    edits to plan files that live under .../make-plan/ or .../rectify/.
    """
    recipe_path = pkg_root() / "recipes" / f"{recipe_name}.yaml"
    recipe = load_recipe(recipe_path)
    for step_name, step in recipe.steps.items():
        cmd = (step.with_args or {}).get("skill_command", "")
        if "dry-walkthrough" not in cmd:
            continue
        output_dir = (step.with_args or {}).get("output_dir", "")
        assert not output_dir.endswith(
            "/dry-walkthrough"
        ), (
            f"{recipe_name}.{step_name}: output_dir must not scope to "
            f"/dry-walkthrough subdirectory — dry-walkthrough edits plan files "
            f"at make-plan/ or rectify/ locations (issue #2919)"
        )
