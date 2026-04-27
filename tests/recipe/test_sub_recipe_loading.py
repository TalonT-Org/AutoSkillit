"""Tests for sub-recipe lazy-loading and merge behavior."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from autoskillit.recipe._api import _build_active_recipe
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_parent_recipe(
    gate_default: str = "false",
    on_success: str = "clone",
    on_failure: str = "escalate",
) -> Recipe:
    """Parent recipe with a test_entry sub_recipe placeholder step."""
    return Recipe(
        name="implementation",
        description="Main recipe",
        ingredients={
            "flag_mode": RecipeIngredient(
                description="Enable sprint mode", default=gate_default, hidden=True
            ),
            "task": RecipeIngredient(description="Task description", required=True),
        },
        steps={
            "test_entry": RecipeStep(
                sub_recipe="test-sub",
                gate="flag_mode",
                on_success=on_success,
                on_failure=on_failure,
                on_exhausted="escalate",
            ),
            "clone": RecipeStep(
                tool="clone_repo",
                with_args={"task": "${{ inputs.task }}"},
                on_success="done",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools"],
    )


def _make_sub_recipe() -> Recipe:
    """A simple sub-recipe with two steps."""
    return Recipe(
        name="test-sub",
        description="Sprint setup prefix",
        ingredients={
            "sprint_branch": RecipeIngredient(description="Branch to use", default="main"),
        },
        steps={
            "check_sprint": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "git status"},
                on_success="setup_sprint",
                on_failure="escalate",
                on_exhausted="escalate",
            ),
            "setup_sprint": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo ready"},
                on_success="done",
                on_failure="escalate",
                on_exhausted="escalate",
            ),
        },
        kitchen_rules=["no native tools in sub-recipe"],
    )


def test_load_with_gate_false_drops_sub_recipe_step(tmp_path: Path) -> None:
    """When gate ingredient is false, sub_recipe step is absent from served Recipe."""
    parent = _make_parent_recipe(gate_default="false")
    active, combined = _build_active_recipe(parent, None, tmp_path)
    assert "test_entry" not in active.steps
    assert combined is None


def test_load_with_gate_false_is_identical_to_standalone(tmp_path: Path) -> None:
    """Served recipe with gate=false is content-identical to a recipe without sub_recipe step."""
    parent = _make_parent_recipe(gate_default="false")
    active, _ = _build_active_recipe(parent, None, tmp_path)
    # Steps should be identical to parent minus the placeholder
    assert set(active.steps.keys()) == {"clone"}
    assert active.ingredients == parent.ingredients


def test_load_with_gate_true_merges_steps(tmp_path: Path) -> None:
    """When gate ingredient is true, sub-recipe steps appear merged in served Recipe."""
    # Write sub-recipe to disk in tmp dir
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_recipe_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: git status
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_recipe_content)

    parent = _make_parent_recipe(gate_default="false")
    active, combined = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    assert combined is not None
    # Placeholder step gone; sub-recipe steps present with prefix
    assert "test_entry" not in active.steps
    assert any("check_sprint" in name for name in active.steps)


def test_merged_steps_have_prefixed_names(tmp_path: Path) -> None:
    """Merged sub-recipe steps are prefixed to avoid name collisions."""
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe()
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    # All merged step names should have the sub-recipe prefix
    sub_step_names = [n for n in active.steps if n != "clone"]
    assert all(name.startswith("test_sub_") for name in sub_step_names)


def test_merged_step_done_routes_to_on_success(tmp_path: Path) -> None:
    """Sub-recipe step routing 'done' is rewritten to the placeholder's on_success."""
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe(on_success="clone")
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    # The sub-recipe step's on_success (was "done") should now route to parent's on_success
    sprint_step = next((s for name, s in active.steps.items() if "check_sprint" in name), None)
    assert sprint_step is not None
    assert sprint_step.on_success == "clone"


def test_merged_step_escalate_routes_to_on_failure(tmp_path: Path) -> None:
    """Sub-recipe step routing 'escalate' is rewritten to the placeholder's on_failure."""
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe(on_failure="escalate")
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    sprint_step = next((s for name, s in active.steps.items() if "check_sprint" in name), None)
    assert sprint_step is not None
    assert sprint_step.on_failure == "escalate"


def test_merged_ingredients_include_sub_recipe_ingredients(tmp_path: Path) -> None:
    """Combined recipe includes sub-recipe's non-hidden ingredients."""
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        ingredients:
          sprint_branch:
            description: Branch name
            default: main
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe()
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    assert "sprint_branch" in active.ingredients


def test_merged_kitchen_rules_union(tmp_path: Path) -> None:
    """Combined recipe kitchen_rules is union of parent and sub-recipe kitchen_rules."""
    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - sub recipe rule
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe()
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    assert "no native tools" in active.kitchen_rules
    assert "sub recipe rule" in active.kitchen_rules


def test_sub_recipe_hidden_ingredients_remain_hidden(tmp_path: Path) -> None:
    """Sub-recipe hidden ingredients are excluded from combined ingredients table."""
    from autoskillit.recipe._api import format_ingredients_table

    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    sub_content = textwrap.dedent("""
        name: test-sub
        description: Sprint setup
        kitchen_rules:
          - no native tools
        ingredients:
          hidden_flag:
            description: Internal flag
            default: "false"
            hidden: true
        steps:
          check_sprint:
            tool: run_cmd
            with:
              cmd: echo hi
            on_success: done
            on_failure: escalate
    """)
    (sub_dir / "test-sub.yaml").write_text(sub_content)

    parent = _make_parent_recipe()
    active, _ = _build_active_recipe(parent, {"flag_mode": "true"}, tmp_path)
    # hidden sub-recipe ingredients must not be merged into the parent at all
    assert "hidden_flag" not in active.ingredients
    table = format_ingredients_table(active)
    assert table is None or "hidden_flag" not in (table or "")


def test_sprint_prefix_sub_recipe_does_not_exist() -> None:
    """sprint-prefix sub-recipe must not exist in the bundled sub-recipe directory."""
    from autoskillit.recipe.io import list_recipes

    sub_recipes = list_recipes(sub_recipes=True)
    names = [r.name for r in sub_recipes]
    assert "sprint-prefix" not in names
