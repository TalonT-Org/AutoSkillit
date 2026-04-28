"""Tests for recipe I/O — capture_list, RecipeInfo.content, version fields, and content hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from autoskillit.core.types import RecipeSource
from autoskillit.recipe.io import (
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
)
from tests.recipe.conftest import _write_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


class TestVersionField:
    """autoskillit_version field on Recipe dataclass."""

    # VER1+VER2 merged
    @pytest.mark.parametrize("version_val,expected", [(None, None), ("0.2.0", "0.2.0")])
    def test_version_field(self, version_val, expected) -> None:
        from autoskillit.recipe.io import _parse_recipe

        data = {
            "name": "v-test",
            "description": "d",
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        if version_val is not None:
            data["autoskillit_version"] = version_val
        wf = _parse_recipe(data)
        assert wf.version == expected

    # VER4
    def test_version_preserved_in_round_trip(self, tmp_path: Path) -> None:
        data = {
            "name": "version-test-recipe",
            "description": "A recipe for testing the version field",
            "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
            "autoskillit_version": "1.3.0",
        }
        path = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(path)
        assert wf.version == "1.3.0"


# ---------------------------------------------------------------------------
# capture_list field tests (D1–D3, D8–D9)
# ---------------------------------------------------------------------------


# D1
def test_recipe_step_accepts_capture_list_field() -> None:
    """RecipeStep accepts capture_list field and stores it."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
        capture={"plan_path": "${{ result.plan_path }}"},
        capture_list={"plan_parts": "${{ result.plan_parts }}"},
        on_success="verify",
    )
    assert step.capture_list == {"plan_parts": "${{ result.plan_parts }}"}


# D2
def test_recipe_step_capture_list_defaults_empty() -> None:
    """RecipeStep.capture_list defaults to an empty dict."""
    step = RecipeStep(tool="run_skill", with_args={}, on_success="done")
    assert step.capture_list == {}


# D3
def test_recipe_yaml_with_capture_list_parses(tmp_path: Path) -> None:
    """YAML recipe with capture_list key is parsed into RecipeStep.capture_list."""
    data = {
        "name": "test-recipe",
        "description": "test",
        "ingredients": {},
        "steps": {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan inputs.task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "capture_list": {"plan_parts": "${{ result.plan_parts }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        },
    }
    path = _write_yaml(tmp_path / "recipe.yaml", data)
    recipe = load_recipe(path)
    assert recipe.steps["plan"].capture_list == {"plan_parts": "${{ result.plan_parts }}"}


# D8
def test_iter_steps_with_context_includes_capture_list_keys() -> None:
    """iter_steps_with_context must include capture_list keys in available_context."""
    from autoskillit.recipe.io import iter_steps_with_context

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:make-plan t"},
                capture={"plan_path": "${{ result.plan_path }}"},
                capture_list={"plan_parts": "${{ result.plan_parts }}"},
                on_success="verify",
            ),
            "verify": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:dry-walkthrough c"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=[],
    )
    steps = list(iter_steps_with_context(recipe))
    verify_ctx = next(ctx for name, _, ctx in steps if name == "verify")
    assert "plan_parts" in verify_ctx, (
        "capture_list keys must appear in available_context for downstream steps"
    )


# D9
def test_implementation_pipeline_captures_plan_parts_as_list() -> None:
    """implementation.yaml plan step must capture plan_parts via capture_list."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    step = recipe.steps["plan"]
    assert hasattr(step, "capture_list"), "RecipeStep must have capture_list field"
    assert "plan_parts" in step.capture_list, (
        "implementation plan step must capture plan_parts via capture_list"
    )


# IO-1: RecipeInfo dataclass accepts content kwarg; defaults to None
def test_recipe_info_has_content_field_defaulting_to_none() -> None:
    """RecipeInfo.content defaults to None when not provided."""
    from autoskillit.recipe.schema import RecipeInfo

    info = RecipeInfo(
        name="x",
        description="y",
        source=RecipeSource.BUILTIN,
        path=Path("/x.yaml"),
    )
    assert info.content is None


# IO-2: list_recipes populates content field with raw YAML text
def test_list_recipes_populates_content(tmp_path: Path) -> None:
    """list_recipes() populates the content field with raw YAML text."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    raw = "name: my-recipe\ndescription: test\nsteps: {}\n"
    (recipes_dir / "my-recipe.yaml").write_text(raw)
    result = list_recipes(tmp_path)
    assert result.items, "expected at least one recipe"
    item = next(r for r in result.items if r.name == "my-recipe")
    assert item.content == raw


# IO-3: content field preserved through find_recipe_by_name
def test_find_recipe_by_name_returns_info_with_content(tmp_path: Path) -> None:
    """find_recipe_by_name() returns a RecipeInfo with content populated."""
    from autoskillit.recipe.io import find_recipe_by_name

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    raw = "name: my-recipe\ndescription: test\nsteps: {}\n"
    (recipes_dir / "my-recipe.yaml").write_text(raw)
    info = find_recipe_by_name("my-recipe", tmp_path)
    assert info is not None
    assert info.content == raw


class TestRecipeVersionField:
    def test_parse_recipe_reads_recipe_version(self, tmp_path):
        yaml_content = (
            "name: test\ndescription: d\nrecipe_version: '1.2.0'\n"
            "kitchen_rules:\n  - rule\nsteps:\n  s1:\n    tool: run_skill\n"
            "    message: hi\n    on_success: done\n"
            "  done:\n    action: stop\n    message: Done\n"
        )
        p = tmp_path / "r.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        assert recipe.recipe_version == "1.2.0"

    def test_parse_recipe_no_recipe_version(self, tmp_path):
        yaml_content = (
            "name: test\ndescription: d\nkitchen_rules:\n  - rule\n"
            "steps:\n  s1:\n    tool: run_skill\n    message: hi\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        p = tmp_path / "r.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        assert recipe.recipe_version is None

    def test_parse_recipe_rejects_float_recipe_version(self, tmp_path):
        yaml_content = (
            "name: test\ndescription: d\nrecipe_version: 1.0\n"
            "kitchen_rules:\n  - rule\nsteps:\n  s1:\n    tool: run_skill\n"
            "    message: hi\n    on_success: done\n"
            "  done:\n    action: stop\n    message: Done\n"
        )
        p = tmp_path / "r.yaml"
        p.write_text(yaml_content)
        with pytest.raises(ValueError, match="recipe_version must be a quoted string"):
            load_recipe(p)


class TestContentHash:
    def test_load_recipe_sets_content_hash(self, tmp_path):
        yaml_content = (
            "name: test\ndescription: d\nkitchen_rules:\n  - rule\n"
            "steps:\n  s1:\n    tool: run_skill\n    message: hi\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        p = tmp_path / "r.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        expected = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        assert recipe.content_hash == expected

    def test_list_recipes_populates_content_hash(self, tmp_path):
        recipe_dir = tmp_path / ".autoskillit" / "recipes"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "test.yaml").write_text(
            "name: test\ndescription: d\nkitchen_rules:\n  - rule\n"
            "steps:\n  s1:\n    tool: run_skill\n    message: hi\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        result = list_recipes(tmp_path)
        assert result.items[0].content_hash.startswith("sha256:")
