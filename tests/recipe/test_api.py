"""Tests for recipe/_api.py: load_and_validate kitchen_rules surface."""

from __future__ import annotations

from pathlib import Path

# Minimal recipe YAML with kitchen_rules
_RECIPE_WITH_RULES = """\
name: test-recipe-with-rules
description: A test recipe
autoskillit_version: "0.3.0"
kitchen_rules:
  - "Never use native tools"
  - "Route failures to on_failure"
ingredients:
  task:
    description: The task
    required: true
steps:
  stop:
    action: stop
    message: "done"
"""

# Minimal recipe YAML without kitchen_rules
_RECIPE_NO_RULES = """\
name: test-recipe-no-rules
description: A test recipe without rules
autoskillit_version: "0.3.0"
ingredients:
  task:
    description: The task
    required: true
steps:
  stop:
    action: stop
    message: "done"
"""


def _setup_project_recipe(tmp_path: Path, name: str, content: str) -> Path:
    """Write a recipe YAML to tmp_path/.autoskillit/recipes/<name>.yaml."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / f"{name}.yaml"
    recipe_path.write_text(content)
    return recipe_path


# T4a
def test_load_and_validate_includes_kitchen_rules(tmp_path):
    """Response has top-level 'kitchen_rules' key with rule strings."""
    from autoskillit.recipe._api import load_and_validate

    _setup_project_recipe(tmp_path, "test-recipe-with-rules", _RECIPE_WITH_RULES)
    result = load_and_validate("test-recipe-with-rules", project_dir=tmp_path)

    assert "kitchen_rules" in result, "kitchen_rules should be present when recipe has rules"
    assert isinstance(result["kitchen_rules"], list)
    assert len(result["kitchen_rules"]) == 2
    assert "Never use native tools" in result["kitchen_rules"]


# T4b
def test_load_and_validate_omits_kitchen_rules_when_empty(tmp_path):
    """Response has no 'kitchen_rules' key when recipe has none."""
    from autoskillit.recipe._api import load_and_validate

    _setup_project_recipe(tmp_path, "test-recipe-no-rules", _RECIPE_NO_RULES)
    result = load_and_validate("test-recipe-no-rules", project_dir=tmp_path)

    assert "kitchen_rules" not in result, "kitchen_rules should be absent when recipe has none"
