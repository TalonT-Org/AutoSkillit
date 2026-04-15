"""Tests for recipe-loader temp-dir placeholder substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe

_RECIPE_TEMPLATE = """\
name: temp_subst_demo
description: demo recipe for temp dir substitution
ingredients:
  task:
    description: free-form task description
    required: true
steps:
  setup:
    tool: run_cmd
    with:
      cmd: 'mkdir -p "{{AUTOSKILLIT_TEMP}}/worktrees"'
    on_success: end
    on_failure: end
  end:
    tool: run_cmd
    with:
      cmd: echo done
output_dir: "{{AUTOSKILLIT_TEMP}}/review-pr"
"""


def _write_recipe(tmp_path: Path) -> Path:
    p = tmp_path / "demo.yaml"
    p.write_text(_RECIPE_TEMPLATE)
    return p


def test_load_recipe_substitutes_placeholder_in_string_value(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    recipe = load_recipe(path)
    # Default substitution → ".autoskillit/temp"
    assert "{{AUTOSKILLIT_TEMP}}" not in str(recipe.steps["setup"].with_args)
    assert ".autoskillit/temp/worktrees" in str(recipe.steps["setup"].with_args)


def test_load_recipe_substitutes_placeholder_in_inline_shell(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    recipe = load_recipe(path)
    cmd = recipe.steps["setup"].with_args["cmd"]
    assert cmd == 'mkdir -p ".autoskillit/temp/worktrees"'


def test_load_recipe_custom_temp_dir_substituted(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    recipe = load_recipe(path, temp_dir_relpath="custom/x")
    cmd = recipe.steps["setup"].with_args["cmd"]
    assert cmd == 'mkdir -p "custom/x/worktrees"'


def test_load_recipe_rejects_yaml_unsafe_temp_dir_relpath(tmp_path: Path) -> None:
    path = _write_recipe(tmp_path)
    with pytest.raises(ValueError, match="YAML-unsafe"):
        load_recipe(path, temp_dir_relpath="bad\nvalue")
    with pytest.raises(ValueError, match="YAML-unsafe"):
        load_recipe(path, temp_dir_relpath="bad: value")


def test_no_recipe_yaml_contains_literal_temp_path() -> None:
    """Bundled recipe YAMLs must use {{AUTOSKILLIT_TEMP}}, never the literal."""
    recipes_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipes"
    assert recipes_root.is_dir(), f"recipes root not found: {recipes_root}"

    offenders: list[str] = []
    for yaml_path in recipes_root.rglob("*.yaml"):
        if ".autoskillit/temp" in yaml_path.read_text(encoding="utf-8"):
            offenders.append(yaml_path.relative_to(recipes_root).as_posix())
    assert not offenders, (
        f"Recipe YAMLs contain literal '.autoskillit/temp' (must use "
        f"'{{{{AUTOSKILLIT_TEMP}}}}' instead): {offenders}"
    )
