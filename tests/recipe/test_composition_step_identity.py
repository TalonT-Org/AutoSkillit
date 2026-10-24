"""Composition tests for re-keyed step identities."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.ingredients._recipe_composition import _build_active_recipe
from autoskillit.recipe.ingredients._recipe_merge import _merge_sub_recipe
from autoskillit.recipe.io import load_recipe
from tests.recipe.conftest import identity_suggestions as _identity_suggestions

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPE_NAME = "composed-projection-parent"
_COMPOSED_STEP_NAME = "composed_projection_child_child_step"
_CHILD_IDENTITY_LINE = "      step_name: child_step\n"


def _install_composed_recipe(tmp_path: Path, *, child_identity: str | None) -> tuple[Path, Path]:
    fixture_dir = Path(__file__).parent / "fixtures"
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    parent_path = recipes_dir / f"{_RECIPE_NAME}.yaml"
    shutil.copy2(fixture_dir / "composed_projection_parent.yaml", parent_path)
    sub_recipe_dir = recipes_dir / "sub-recipes"
    sub_recipe_dir.mkdir()
    child_path = sub_recipe_dir / "composed-projection-child.yaml"
    child_content = (fixture_dir / "composed_projection_child.yaml").read_text()
    assert child_content.count(_CHILD_IDENTITY_LINE) == 1
    replacement = "" if child_identity is None else f"      step_name: {child_identity}\n"
    child_path.write_text(child_content.replace(_CHILD_IDENTITY_LINE, replacement))
    return parent_path, child_path


@pytest.mark.parametrize(
    ("child_identity", "declared"),
    [("child_step", True), (None, False)],
    ids=["declared", "omitted"],
)
def test_composed_step_carries_one_identity(
    tmp_path: Path, child_identity: str | None, declared: bool
) -> None:
    parent_path, _ = _install_composed_recipe(tmp_path, child_identity=child_identity)
    active = _build_active_recipe(load_recipe(parent_path), {}, tmp_path)[0]
    step = active.steps[_COMPOSED_STEP_NAME]

    assert step.name == _COMPOSED_STEP_NAME
    if declared:
        assert step.with_args["step_name"] == _COMPOSED_STEP_NAME
        assert step.declared_with_args is not None
        assert step.declared_with_args["step_name"] == _COMPOSED_STEP_NAME
    else:
        assert "step_name" not in step.with_args
        assert step.declared_with_args is None or "step_name" not in step.declared_with_args


def test_divergent_child_identity_is_still_reported(tmp_path: Path) -> None:
    _install_composed_recipe(tmp_path, child_identity="other")

    result = load_and_validate(_RECIPE_NAME, project_dir=tmp_path)
    findings = _identity_suggestions(result)

    assert result["valid"] is False
    assert len(findings) == 1
    assert findings[0]["step"] == _COMPOSED_STEP_NAME


def test_composition_does_not_mutate_child_step_maps(tmp_path: Path) -> None:
    parent_path, child_path = _install_composed_recipe(tmp_path, child_identity="child_step")
    parent = load_recipe(parent_path)
    child = load_recipe(child_path)
    source_step = child.steps["child_step"]
    original_name = source_step.name
    original_with_args = dict(source_step.with_args)
    original_declared_with_args = dict(source_step.declared_with_args or {})

    merged = _merge_sub_recipe(parent, "child_placeholder", child)

    assert merged.steps[_COMPOSED_STEP_NAME] is not source_step
    assert source_step.name == original_name
    assert source_step.with_args == original_with_args
    assert source_step.declared_with_args == original_declared_with_args
