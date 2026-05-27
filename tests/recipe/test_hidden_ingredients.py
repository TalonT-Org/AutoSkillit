"""Tests for hidden: true ingredient behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._api import format_ingredients_table
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(**ingredients: RecipeIngredient) -> Recipe:
    steps = {
        "do_something": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "echo hi"},
            on_success="done",
            on_exhausted="escalate",
        )
    }
    return Recipe(
        name="test-recipe",
        description="Test recipe",
        ingredients=ingredients,
        steps=steps,
        kitchen_rules=["do not use native tools"],
    )


def test_hidden_ingredient_parsed() -> None:
    """hidden: true is stored on RecipeIngredient.hidden."""
    from autoskillit.recipe.io import _parse_recipe

    data = {
        "name": "test",
        "description": "test",
        "kitchen_rules": ["no native tools"],
        "ingredients": {
            "secret_flag": {
                "description": "Enable secret flag",
                "default": "false",
                "hidden": True,
            }
        },
        "steps": {"do_it": {"tool": "run_cmd", "with": {"cmd": "echo hi"}, "on_success": "done"}},
    }
    recipe = _parse_recipe(data)
    assert recipe.ingredients["secret_flag"].hidden is True


def test_hidden_ingredient_default_false() -> None:
    """Ingredients without hidden: are parsed with hidden=False."""
    from autoskillit.recipe.io import _parse_recipe

    data = {
        "name": "test",
        "description": "test",
        "kitchen_rules": ["no native tools"],
        "ingredients": {
            "task": {
                "description": "What to implement",
                "required": True,
            }
        },
        "steps": {"do_it": {"tool": "run_cmd", "with": {"cmd": "echo hi"}, "on_success": "done"}},
    }
    recipe = _parse_recipe(data)
    assert recipe.ingredients["task"].hidden is False


def test_hidden_ingredient_excluded_from_table() -> None:
    """format_ingredients_table omits hidden ingredients."""
    recipe = _make_recipe(
        secret_flag=RecipeIngredient(
            description="Enable secret flag",
            default="false",
            hidden=True,
        ),
        task=RecipeIngredient(
            description="What to implement",
            required=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is not None
    assert "secret_flag" not in table


def test_non_hidden_ingredient_included_in_table() -> None:
    """Non-hidden ingredients still appear in the table."""
    recipe = _make_recipe(
        secret_flag=RecipeIngredient(
            description="Enable secret flag",
            default="false",
            hidden=True,
        ),
        task=RecipeIngredient(
            description="What to implement",
            required=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is not None
    assert "task" in table


def test_all_hidden_ingredients_returns_none() -> None:
    """format_ingredients_table returns None when all ingredients are hidden."""
    recipe = _make_recipe(
        secret_flag=RecipeIngredient(
            description="Hidden flag",
            default="false",
            hidden=True,
        ),
    )
    table = format_ingredients_table(recipe)
    assert table is None


def test_prune_skipped_steps_truthy_clears_field() -> None:
    """_prune_skipped_steps keeps step and clears skip_when_false when override is true."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "post_run_diagnostics": RecipeIngredient(
                description="Enable post-run diagnostics",
                default="false",
                hidden=True,
            )
        },
        steps={
            "main_step": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="diag"
            ),
            "diag": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.post_run_diagnostics",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, resolutions = _prune_skipped_steps(
        recipe, ingredient_overrides={"post_run_diagnostics": "true"}
    )
    assert "diag" in pruned.steps
    assert pruned.steps["diag"].skip_when_false is None
    assert resolutions["diag"] is True

    pruned2, resolutions2 = _prune_skipped_steps(
        recipe, ingredient_overrides={"post_run_diagnostics": "false"}
    )
    assert "diag" not in pruned2.steps
    assert resolutions2["diag"] is False


def test_prune_skipped_steps_removes_step_and_cleans_routes() -> None:
    """_prune_skipped_steps removes the step and repairs upstream routes when falsy."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "post_run_diagnostics": RecipeIngredient(
                description="Enable diagnostics",
                default="false",
                hidden=True,
            )
        },
        steps={
            "upstream": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="diag"
            ),
            "diag": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.post_run_diagnostics",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, _ = _prune_skipped_steps(
        recipe, ingredient_overrides={"post_run_diagnostics": "false"}
    )
    assert "diag" not in pruned.steps
    # Route repaired: upstream.on_success now points to diag's on_success (done)
    assert pruned.steps["upstream"].on_success == "done"
    # Pruned step name does not appear as any route target
    for step in pruned.steps.values():
        assert step.on_success != "diag"
        assert step.on_failure != "diag"


def test_prune_skipped_steps_url_string_is_truthy() -> None:
    """_prune_skipped_steps keeps step when override is a URL (non-boolean truthy string)."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "issue_url": RecipeIngredient(
                description="URL of the issue",
                required=False,
            )
        },
        steps={
            "claim_and_resolve": RecipeStep(
                tool="claim_and_resolve_issue",
                optional=True,
                skip_when_false="inputs.issue_url",
                on_success="done",
                on_failure="done",
                with_args={"issue_url": "inputs.issue_url"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, resolutions = _prune_skipped_steps(
        recipe, ingredient_overrides={"issue_url": "https://github.com/org/repo/issues/42"}
    )
    assert "claim_and_resolve" in pruned.steps
    assert resolutions["claim_and_resolve"] is True
    assert pruned.steps["claim_and_resolve"].skip_when_false is None


def test_prune_skipped_steps_empty_string_is_falsy() -> None:
    """_prune_skipped_steps prunes step for empty string, absent, 'false', and 'no' overrides."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "issue_url": RecipeIngredient(
                description="URL of the issue",
                required=False,
            )
        },
        steps={
            "claim_and_resolve": RecipeStep(
                tool="claim_and_resolve_issue",
                optional=True,
                skip_when_false="inputs.issue_url",
                on_success="done",
                on_failure="done",
                with_args={"issue_url": "inputs.issue_url"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    for falsy_value in ("", "false", "no"):
        pruned, resolutions = _prune_skipped_steps(
            recipe, ingredient_overrides={"issue_url": falsy_value}
        )
        assert "claim_and_resolve" not in pruned.steps, (
            f"Expected pruned for value={falsy_value!r}"
        )
        assert resolutions["claim_and_resolve"] is False

    # Absent override (no default on ingredient) — also falsy
    pruned_absent, resolutions_absent = _prune_skipped_steps(recipe, ingredient_overrides={})
    assert "claim_and_resolve" not in pruned_absent.steps
    assert resolutions_absent["claim_and_resolve"] is False


@pytest.mark.parametrize(
    "value,expected_truthy",
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("Yes", True),
        ("https://github.com/org/repo/issues/42", True),
        ("/path/to/file.md", True),
        ("some-branch-name", True),
        ("enabled", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("No", False),
        ("", False),
    ],
)
def test_prune_skipped_steps_truthiness_boundary(value: str, expected_truthy: bool) -> None:
    """Full truthiness contract for _prune_skipped_steps ingredient evaluation."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={"flag": RecipeIngredient(description="A flag", required=False)},
        steps={
            "guarded": RecipeStep(
                tool="run_cmd",
                optional=True,
                skip_when_false="inputs.flag",
                on_success="done",
                on_failure="done",
                with_args={"cmd": "echo hi"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    pruned, resolutions = _prune_skipped_steps(recipe, ingredient_overrides={"flag": value})
    assert resolutions["guarded"] is expected_truthy, (
        f"value={value!r}: expected truthy={expected_truthy}, got {resolutions['guarded']}"
    )
    if expected_truthy:
        assert "guarded" in pruned.steps
        assert pruned.steps["guarded"].skip_when_false is None
    else:
        assert "guarded" not in pruned.steps


def test_load_and_validate_resolves_skip_guards_in_content(tmp_path: Path) -> None:
    """load_and_validate strips/resolves skip_when_false lines in content."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    yaml_text = """
name: test-skip-guards
description: Test skip guards
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  post_run_diagnostics:
    description: Enable diagnostics
    default: "false"
    hidden: true
steps:
  main_step:
    tool: run_cmd
    with:
      cmd: echo hi
    on_success: diag
  diag:
    tool: run_skill
    optional: true
    skip_when_false: inputs.post_run_diagnostics
    with:
      skill_command: /autoskillit:diagnose /tmp/x.md
      cwd: /tmp
    on_success: done
    on_failure: done
    on_context_limit: done
  done:
    action: stop
    message: done
"""
    (recipe_dir / "test-skip-guards.yaml").write_text(yaml_text)

    # When true: skip_when_false line should be stripped from content
    result = load_and_validate(
        "test-skip-guards",
        project_dir=tmp_path,
        ingredient_overrides={"post_run_diagnostics": "true"},
    )
    assert "inputs.post_run_diagnostics" not in result["content"]

    # When false: skip_when_false should be resolved to literal "false"
    result2 = load_and_validate(
        "test-skip-guards",
        project_dir=tmp_path,
        ingredient_overrides={"post_run_diagnostics": "false"},
    )
    assert "inputs.post_run_diagnostics" not in result2["content"]
    assert 'skip_when_false: "false"' in result2["content"]
