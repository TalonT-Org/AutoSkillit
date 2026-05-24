"""Tests for recipe-loader temp-dir placeholder substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

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


# ---------------------------------------------------------------------------
# Tests for API-layer content integrity (load_and_validate result["content"])
# ---------------------------------------------------------------------------

_RECIPE_WITH_PLACEHOLDER = """\
name: temp-placeholder-test
description: test recipe with AUTOSKILLIT_TEMP placeholder
ingredients:
  task:
    description: the task
    required: true
steps:
  setup:
    tool: run_cmd
    with:
      cmd: 'mkdir -p "{{AUTOSKILLIT_TEMP}}/worktrees"'
  end:
    tool: stop
    message: done
output_dir: "{{AUTOSKILLIT_TEMP}}/review-pr"
"""


def _setup_project_recipe_with_placeholder(tmp_path: Path, name: str) -> Path:
    """Write a recipe YAML containing {{AUTOSKILLIT_TEMP}} to project recipes dir."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / f"{name}.yaml"
    recipe_path.write_text(_RECIPE_WITH_PLACEHOLDER)
    return recipe_path


def test_load_and_validate_content_has_no_raw_placeholders(tmp_path: Path) -> None:
    """result['content'] must not contain {{AUTOSKILLIT_TEMP}} after substitution."""
    from autoskillit.recipe._api import load_and_validate

    _setup_project_recipe_with_placeholder(tmp_path, "temp-placeholder-test")
    result = load_and_validate("temp-placeholder-test", project_dir=tmp_path)

    assert "{{AUTOSKILLIT_TEMP}}" not in result["content"], (
        "result['content'] must have placeholders substituted; "
        "found literal '{{AUTOSKILLIT_TEMP}}' in content"
    )
    # Verify the resolved path appears where placeholders were
    assert ".autoskillit/temp" in result["content"]


def test_load_and_validate_different_temp_dir_relpath_produces_different_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import autoskillit.recipe._api as api_mod
    import autoskillit.recipe._api_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_LOAD_CACHE", {})
    _setup_project_recipe_with_placeholder(tmp_path, "temp-placeholder-test")

    result1 = api_mod.load_and_validate(
        "temp-placeholder-test", project_dir=tmp_path, temp_dir_relpath=".autoskillit/temp"
    )
    result2 = api_mod.load_and_validate(
        "temp-placeholder-test", project_dir=tmp_path, temp_dir_relpath="custom/temp-dir"
    )

    assert ".autoskillit/temp" in result1["content"]
    assert "custom/temp-dir" in result2["content"]
    assert result1["content"] != result2["content"], (
        "Different temp_dir_relpath must produce different content"
    )


def test_assert_no_raw_placeholders_raises_on_literal_placeholder() -> None:
    """_assert_no_raw_placeholders must raise ValueError on unresolved placeholders."""
    from autoskillit.recipe.io import _assert_no_raw_placeholders

    with pytest.raises(ValueError, match="Unresolved"):
        _assert_no_raw_placeholders("some text with {{AUTOSKILLIT_TEMP}} inside")


def test_assert_no_raw_placeholders_passes_on_substituted_text() -> None:
    """_assert_no_raw_placeholders must pass silently on substituted text."""
    from autoskillit.recipe.io import _assert_no_raw_placeholders

    _assert_no_raw_placeholders("already substituted .autoskillit/temp/path")
    _assert_no_raw_placeholders("")


def test_all_bundled_recipes_content_no_raw_placeholders(
    tmp_path: Path,
) -> None:
    """Every bundled recipe loaded via load_and_validate has no raw placeholders in content."""
    from autoskillit.recipe._api import load_and_validate
    from autoskillit.recipe.io import list_recipes

    recipes_root = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "recipes"
    repo_root = Path(__file__).resolve().parents[2]
    result = list_recipes(recipes_root)

    offenders: list[str] = []
    for recipe_info in result.items:
        load_result = load_and_validate(recipe_info.name, project_dir=repo_root)
        if "error" not in load_result and "{{AUTOSKILLIT_TEMP}}" in load_result["content"]:
            offenders.append(recipe_info.name)

    assert not offenders, (
        f"The following recipes have unsubstituted {{{{AUTOSKILLIT_TEMP}}}} in "
        f"result['content']: {offenders}"
    )
