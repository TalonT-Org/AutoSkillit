"""Tests for hidden: true ingredient behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe._api import format_ingredients_table
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


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
        pytest.param("auto", True, id="auto-sentinel-truthy"),
        pytest.param("none", True, id="none-sentinel-truthy"),
        pytest.param("default", True, id="default-sentinel-truthy"),
        pytest.param("inherit", True, id="inherit-sentinel-truthy"),
        pytest.param("AUTO", True, id="auto-upper-sentinel-truthy"),
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


def test_prune_investigate_auto_default_evaluates_truthy() -> None:
    """The 'auto' sentinel evaluates truthy — investigate step is kept when
    no override is provided. Direct invocation with investigate='auto' always runs the step."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "investigate": RecipeIngredient(
                description="Run the investigate step",
                default="auto",
            )
        },
        steps={
            "investigate": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.investigate",
                on_success="done",
                on_failure="done",
                on_context_limit="done",
                with_args={"skill_command": "/autoskillit:investigate plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, resolutions = _prune_skipped_steps(recipe, ingredient_overrides={})
    assert resolutions["investigate"] is True
    assert "investigate" in pruned.steps
    assert pruned.steps["investigate"].skip_when_false is None


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
    content = result["content"]
    diag_block_start = content.index("  diag:\n")
    next_step_start = content.find("\n  done:\n", diag_block_start)
    diag_block = content[diag_block_start:next_step_start]
    assert "optional: true" not in diag_block

    # When false: entire step block is stripped from content
    result2 = load_and_validate(
        "test-skip-guards",
        project_dir=tmp_path,
        ingredient_overrides={"post_run_diagnostics": "false"},
    )
    assert "inputs.post_run_diagnostics" not in result2["content"]
    assert 'skip_when_false: "false"' not in result2["content"]
    assert "  diag:\n" not in result2["content"]


def test_prune_on_result_only_step_repairs_upstream_routes() -> None:
    """_prune_skipped_steps repairs upstream routes when pruned step has on_result only."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps
    from autoskillit.recipe.schema import StepResultCondition, StepResultRoute

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "flag": RecipeIngredient(description="Enable step", default="false", hidden=True)
        },
        steps={
            "upstream": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="skippable"
            ),
            "skippable": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.flag",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.ok }}", route="done"),
                        StepResultCondition(when=None, route="fallback"),
                    ]
                ),
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "fallback": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo fallback"}, on_success="done"
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, resolutions = _prune_skipped_steps(recipe, ingredient_overrides={"flag": "false"})
    assert "skippable" not in pruned.steps
    assert resolutions["skippable"] is False
    # upstream.on_success redirected to the when=None default condition route ("fallback")
    assert pruned.steps["upstream"].on_success == "fallback"
    # No surviving step references "skippable" in any routing field
    for step in pruned.steps.values():
        assert step.on_success != "skippable"
        assert step.on_failure != "skippable"
        assert step.on_context_limit != "skippable"
        assert step.on_exhausted != "skippable"
        if step.on_result:
            for cond in step.on_result.conditions:
                assert cond.route != "skippable"


def test_prune_repairs_upstream_on_result_pointing_to_pruned_step() -> None:
    """_prune_skipped_steps repairs on_result.conditions routes on upstream steps."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps
    from autoskillit.recipe.schema import StepResultCondition, StepResultRoute

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "flag": RecipeIngredient(description="Enable step", default="false", hidden=True)
        },
        steps={
            "router": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:route /tmp/x.md", "cwd": "/tmp"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.ok }}", route="skippable"),
                        StepResultCondition(when=None, route="done"),
                    ]
                ),
            ),
            "skippable": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.flag",
                on_success="done",
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, _ = _prune_skipped_steps(recipe, ingredient_overrides={"flag": "false"})
    assert "skippable" not in pruned.steps
    router = pruned.steps["router"]
    assert router.on_result is not None
    # The condition that pointed to "skippable" now points to "done" (redirect)
    assert all(cond.route != "skippable" for cond in router.on_result.conditions)
    routes = [c.route for c in router.on_result.conditions]
    assert "done" in routes


def test_prune_repairs_legacy_on_result_routes_pointing_to_pruned_step() -> None:
    """_prune_skipped_steps repairs legacy on_result.routes dict values on upstream steps."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps
    from autoskillit.recipe.schema import StepResultRoute

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "flag": RecipeIngredient(description="Enable step", default="false", hidden=True)
        },
        steps={
            "router": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:route /tmp/x.md", "cwd": "/tmp"},
                on_result=StepResultRoute(
                    field="result.status",
                    routes={"ok": "skippable", "fail": "done"},
                ),
            ),
            "skippable": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.flag",
                on_success="done",
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    pruned, _ = _prune_skipped_steps(recipe, ingredient_overrides={"flag": "false"})
    assert "skippable" not in pruned.steps
    router = pruned.steps["router"]
    assert router.on_result is not None
    assert all(v != "skippable" for v in router.on_result.routes.values())
    assert router.on_result.routes["ok"] == "done"


def test_prune_on_result_no_default_condition_leaves_redirect_none() -> None:
    """When pruned step has on_result.conditions but no when=None, redirect stays None."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps
    from autoskillit.recipe.schema import StepResultCondition, StepResultRoute

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "flag": RecipeIngredient(description="Enable step", default="false", hidden=True)
        },
        steps={
            "upstream": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="skippable"
            ),
            "skippable": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.flag",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(when="${{ result.ok }}", route="done"),
                        StepResultCondition(when="${{ result.fail }}", route="escalate"),
                    ]
                ),
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    # No when=None condition → redirect is None → upstream.on_success not repaired
    pruned, _ = _prune_skipped_steps(recipe, ingredient_overrides={"flag": "false"})
    assert "skippable" not in pruned.steps
    assert pruned.steps["upstream"].on_success == "skippable"


def test_prune_legacy_on_result_routes_leaves_redirect_none() -> None:
    """When pruned step uses legacy on_result.routes, redirect is None (no semantic default)."""
    from autoskillit.recipe._recipe_composition import _prune_skipped_steps
    from autoskillit.recipe.schema import StepResultRoute

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={
            "flag": RecipeIngredient(description="Enable step", default="false", hidden=True)
        },
        steps={
            "upstream": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="skippable"
            ),
            "skippable": RecipeStep(
                tool="run_skill",
                optional=True,
                skip_when_false="inputs.flag",
                on_result=StepResultRoute(
                    field="result.status",
                    routes={"ok": "done", "fail": "escalate"},
                ),
                with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    # Legacy routes format → redirect is None → upstream.on_success not repaired
    pruned, _ = _prune_skipped_steps(recipe, ingredient_overrides={"flag": "false"})
    assert "skippable" not in pruned.steps
    assert pruned.steps["upstream"].on_success == "skippable"


def test_prune_content_strips_pruned_step_block_entirely(tmp_path: Path) -> None:
    """load_and_validate strips the entire step block from content when falsy."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    yaml_text = """
name: test-strip-block
description: Test block stripping
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  flag:
    description: Enable step
    default: "false"
    hidden: true
steps:
  main_step:
    tool: run_cmd
    with:
      cmd: echo hi
    on_success: optional_step
  optional_step:
    tool: run_skill
    optional: true
    skip_when_false: inputs.flag
    with:
      skill_command: /autoskillit:diagnose /tmp/x.md
      cwd: /tmp
    on_success: done
  done:
    action: stop
    message: done
"""
    (recipe_dir / "test-strip-block.yaml").write_text(yaml_text)

    result = load_and_validate(
        "test-strip-block",
        project_dir=tmp_path,
        ingredient_overrides={"flag": "false"},
    )
    content = result["content"]
    # The step block header is stripped
    assert "  optional_step:\n" not in content
    assert 'skip_when_false: "false"' not in content
    assert "skip_when_false: inputs.flag" not in content


def test_prune_content_strips_literal_skip_when_false_step_block() -> None:
    """_resolve_skip_guards_in_content strips step block for literal skip_when_false: false."""
    from autoskillit.recipe._recipe_composition import _resolve_skip_guards_in_content

    raw = """steps:
  main_step:
    tool: run_cmd
    with:
      cmd: echo hi
    on_success: optional_step
  optional_step:
    tool: run_skill
    optional: true
    skip_when_false: "false"
    with:
      skill_command: /autoskillit:diagnose /tmp/x.md
      cwd: /tmp
    on_success: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "optional_step": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="false",
            on_success="done",
            with_args={"skill_command": "/autoskillit:diagnose /tmp/x.md", "cwd": "/tmp"},
        )
    }
    resolutions = {"optional_step": False}

    result = _resolve_skip_guards_in_content(raw, resolutions, original_steps)
    assert "  optional_step:\n" not in result
    assert 'skip_when_false: "false"' not in result


def test_post_prune_dangling_route_returns_errors() -> None:
    """_validate_no_dangling_routes returns errors for dangling route references."""
    from autoskillit.recipe._recipe_composition import _validate_no_dangling_routes

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "upstream": RecipeStep(
                tool="run_cmd", with_args={"cmd": "echo hi"}, on_success="missing_step"
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )

    errors = _validate_no_dangling_routes(recipe)
    assert len(errors) > 0
    assert any("missing_step" in e for e in errors)
    assert any("upstream" in e for e in errors)


def test_load_and_validate_clears_content_on_dangling_routes(tmp_path: Path) -> None:
    """load_and_validate blocks content when pruning produces dangling route references."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    # skippable has on_result with NO when=None default condition; redirect=None after pruning.
    # upstream.on_success remains pointing to "skippable" → dangling route.
    yaml_text = """
name: test-dangling-route
description: Test dangling route safety net
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  flag:
    description: Enable step
    default: "false"
    hidden: true
steps:
  upstream:
    tool: run_cmd
    with:
      cmd: echo hi
    on_success: skippable
  skippable:
    tool: run_skill
    optional: true
    skip_when_false: inputs.flag
    with:
      skill_command: /autoskillit:diagnose /tmp/x.md
      cwd: /tmp
    on_result:
    - when: "${{ result.ok }}"
      route: done
    - when: "${{ result.fail }}"
      route: escalate
  done:
    action: stop
    message: done
"""
    (recipe_dir / "test-dangling-route.yaml").write_text(yaml_text)

    result = load_and_validate(
        "test-dangling-route",
        project_dir=tmp_path,
        ingredient_overrides={"flag": "false"},
    )
    assert result["valid"] is False
    assert result["content"] == ""


def test_bundled_recipes_prune_produces_no_dangling_routes() -> None:
    """Regression: pruning skip_when_false steps with computable redirects
    produces no dangling routes in each bundled recipe.
    """
    from autoskillit.recipe._recipe_composition import (
        _prune_skipped_steps,
        _validate_no_dangling_routes,
    )
    from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

    def _has_computable_redirect(step: object) -> bool:
        """Return True if the step has a safe redirect that can be computed."""
        if getattr(step, "on_success", None) is not None:
            return True
        on_result = getattr(step, "on_result", None)
        if on_result is not None and on_result.conditions:
            return any(c.when is None for c in on_result.conditions)
        return False

    recipe_dir = builtin_recipes_dir()
    yaml_files = sorted(recipe_dir.glob("*.yaml"))
    assert yaml_files, "No bundled recipe YAML files found"

    for yaml_file in yaml_files:
        recipe = load_recipe(yaml_file)
        for step_name, step in recipe.steps.items():
            if step.skip_when_false is None:
                continue
            ref = step.skip_when_false
            if not ref.startswith("inputs."):
                continue
            ingredient_name = ref[len("inputs.") :]
            pruned, resolutions = _prune_skipped_steps(
                recipe, ingredient_overrides={ingredient_name: "false"}
            )
            # Only assert no dangling routes when all pruned steps have computable
            # redirects. When any pruned step lacks a redirect, dangling routes are
            # expected and detected by _validate_no_dangling_routes as designed.
            all_pruned_have_redirect = all(
                _has_computable_redirect(recipe.steps[name])
                for name, kept in resolutions.items()
                if not kept and name in recipe.steps
            )
            if not all_pruned_have_redirect:
                continue
            errors = _validate_no_dangling_routes(pruned)
            assert not errors, (
                f"Bundled recipe {yaml_file.name!r}: pruning step {step_name!r} "
                f"produced dangling routes: {errors}"
            )


def test_resolve_skip_guards_strips_optional_true_on_truthy() -> None:
    """_resolve_skip_guards_in_content strips optional: true from truthy-resolved step."""
    from autoskillit.recipe._recipe_composition import _resolve_skip_guards_in_content

    raw = """steps:
  main_step:
    tool: run_cmd
    with:
      cmd: echo hi
    on_success: guarded
  guarded:
    tool: run_skill
    optional: true
    skip_when_false: inputs.flag
    with:
      skill_command: /autoskillit:do_thing /tmp/x.md
    on_success: done
    on_failure: done
    on_context_limit: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "guarded": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="inputs.flag",
            on_success="done",
            on_failure="done",
            on_context_limit="done",
            with_args={"skill_command": "/autoskillit:do_thing /tmp/x.md"},
        )
    }
    resolutions = {"guarded": True}

    result = _resolve_skip_guards_in_content(raw, resolutions, original_steps)
    assert "optional: true" not in result
    assert "optional: True" not in result
    assert "tool: run_skill" in result
    assert "on_success: done" in result


def test_resolve_skip_guards_preserves_optional_on_unresolved_steps() -> None:
    """_resolve_skip_guards_in_content preserves optional: true on steps not being resolved."""
    from autoskillit.recipe._recipe_composition import _resolve_skip_guards_in_content

    raw = """steps:
  guarded:
    tool: run_skill
    optional: true
    skip_when_false: inputs.flag
    with:
      skill_command: /autoskillit:do_thing /tmp/x.md
    on_success: other
  other:
    tool: run_skill
    optional: true
    skip_when_false: inputs.other_flag
    with:
      skill_command: /autoskillit:other /tmp/y.md
    on_success: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "guarded": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="inputs.flag",
            on_success="other",
            with_args={"skill_command": "/autoskillit:do_thing /tmp/x.md"},
        ),
        "other": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="inputs.other_flag",
            on_success="done",
            with_args={"skill_command": "/autoskillit:other /tmp/y.md"},
        ),
    }
    resolutions = {"guarded": True}

    result = _resolve_skip_guards_in_content(raw, resolutions, original_steps)
    guarded_end = result.index("  other:\n")
    guarded_block = result[result.index("  guarded:\n") : guarded_end]
    assert "optional: true" not in guarded_block
    other_end = result.index("  done:\n")
    other_block = result[result.index("  other:\n") : other_end]
    assert "optional: true" in other_block


def test_assert_content_integrity_raises_on_optional_residual() -> None:
    """_assert_content_integrity raises ValueError if optional: true survives truthy resolution."""
    from autoskillit.recipe._recipe_composition import _assert_content_integrity
    from autoskillit.recipe.schema import RecipeStep

    raw = """steps:
  guarded:
    tool: run_skill
    optional: true
    skip_when_false: inputs.flag
    on_success: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "guarded": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="inputs.flag",
            on_success="done",
        )
    }
    resolutions = {"guarded": True}
    with pytest.raises(ValueError, match="optional: true"):
        _assert_content_integrity(raw, resolutions, original_steps)


def test_assert_content_integrity_passes_on_clean_content() -> None:
    """_assert_content_integrity does not raise when optional: true is absent after resolution."""
    from autoskillit.recipe._recipe_composition import _assert_content_integrity
    from autoskillit.recipe.schema import RecipeStep

    raw = """steps:
  guarded:
    tool: run_skill
    on_success: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "guarded": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="inputs.flag",
            on_success="done",
        )
    }
    resolutions = {"guarded": True}
    _assert_content_integrity(raw, resolutions, original_steps)  # must not raise


def test_assert_content_integrity_allows_literal_skip_when_false() -> None:
    """_assert_content_integrity does not raise on literal skip_when_false (non-inputs.*)."""
    from autoskillit.recipe._recipe_composition import _assert_content_integrity
    from autoskillit.recipe.schema import RecipeStep

    raw = """steps:
  guarded:
    tool: run_skill
    skip_when_false: "true"
    on_success: done
  done:
    action: stop
    message: done
"""
    original_steps = {
        "guarded": RecipeStep(
            tool="run_skill",
            optional=True,
            skip_when_false="true",
            on_success="done",
        )
    }
    resolutions = {"guarded": True}
    _assert_content_integrity(raw, resolutions, original_steps)  # must not raise


# ---------------------------------------------------------------------------
# Hidden ingredient ${{ inputs.* }} interpolation tests
# ---------------------------------------------------------------------------

_RECIPE_HIDDEN_SKILL_COMMAND = """\
name: test-hidden-interp
description: Test hidden ingredient interpolation
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  kitchen_id:
    description: Kitchen ID
    default: ""
    hidden: true
steps:
  run_diag:
    tool: run_skill
    with:
      skill_command: /autoskillit:run-diagnostic ${{ inputs.kitchen_id }}
      cwd: /tmp
    on_success: done
    on_failure: done
  done:
    action: stop
    message: done
"""


def test_hidden_ingredient_in_skill_command_resolved_in_content(tmp_path: Path) -> None:
    """load_and_validate resolves ${{ inputs.X }} in skill_command for hidden ingredients."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "test-hidden-interp.yaml").write_text(_RECIPE_HIDDEN_SKILL_COMMAND)

    result = load_and_validate(
        "test-hidden-interp",
        project_dir=tmp_path,
        ingredient_overrides={"kitchen_id": "test-abc-123"},
    )
    assert "test-abc-123" in result["content"]
    assert "${{ inputs.kitchen_id }}" not in result["content"]


_RECIPE_HIDDEN_WITH_BLOCK = """\
name: test-hidden-with
description: Test hidden ingredient in with block
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  diagnostics_log_dir:
    description: Log directory
    default: ""
    hidden: true
steps:
  consolidate:
    tool: run_skill
    with:
      skill_command: /autoskillit:consolidate-health-reports
      log_dir: ${{ inputs.diagnostics_log_dir }}
      cwd: /tmp
    on_success: done
    on_failure: done
  done:
    action: stop
    message: done
"""


def test_hidden_ingredient_in_with_block_resolved_in_content(tmp_path: Path) -> None:
    """load_and_validate resolves ${{ inputs.X }} in with: block for hidden ingredients."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "test-hidden-with.yaml").write_text(_RECIPE_HIDDEN_WITH_BLOCK)

    result = load_and_validate(
        "test-hidden-with",
        project_dir=tmp_path,
        ingredient_overrides={"diagnostics_log_dir": "/var/log/autoskillit"},
    )
    assert "/var/log/autoskillit" in result["content"]
    assert "${{ inputs.diagnostics_log_dir }}" not in result["content"]


_RECIPE_HIDDEN_DISPATCH_ID = """\
name: test-dispatch-id
description: Test dispatch_id hidden ingredient resolution
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  dispatch_id:
    description: Dispatch ID
    default: ""
    hidden: true
    authority: config
steps:
  run_diag:
    tool: run_skill
    with:
      skill_command: /autoskillit:run-diagnostic ${{ inputs.dispatch_id }}
      cwd: /tmp
    on_success: done
    on_failure: done
  done:
    action: stop
    message: done
"""


def test_dispatch_id_hidden_ingredient_resolved_in_content(tmp_path: Path) -> None:
    """load_and_validate resolves dispatch_id hidden ingredient via ingredient_overrides."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "test-dispatch-id.yaml").write_text(_RECIPE_HIDDEN_DISPATCH_ID)

    result = load_and_validate(
        "test-dispatch-id",
        project_dir=tmp_path,
        ingredient_overrides={"dispatch_id": "d-999"},
    )
    assert "d-999" in result["content"]
    assert "${{ inputs.dispatch_id }}" not in result["content"]


def test_hidden_ingredient_uses_default_when_no_override(tmp_path: Path) -> None:
    """load_and_validate uses ingredient default when no override is provided."""
    from autoskillit.recipe import load_and_validate

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "test-hidden-interp.yaml").write_text(_RECIPE_HIDDEN_SKILL_COMMAND)

    result = load_and_validate("test-hidden-interp", project_dir=tmp_path)
    assert "${{ inputs.kitchen_id }}" not in result["content"]
    assert "skill_command: /autoskillit:run-diagnostic" in result["content"]


def test_visible_ingredient_not_resolved_by_hidden_interpolation(tmp_path: Path) -> None:
    """Visible ingredient ${{ inputs.X }} references are NOT resolved server-side."""
    from autoskillit.recipe import load_and_validate

    yaml_text = """\
name: test-visible-interp
description: Test visible ingredient stays as template
recipe_version: "0.0.1"
kitchen_rules:
  - no native tools
ingredients:
  task:
    description: The task
    required: true
steps:
  do_work:
    tool: run_skill
    with:
      skill_command: /autoskillit:implement ${{ inputs.task }}
      cwd: /tmp
    on_success: done
    on_failure: done
  done:
    action: stop
    message: done
"""
    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "test-visible-interp.yaml").write_text(yaml_text)

    result = load_and_validate(
        "test-visible-interp",
        project_dir=tmp_path,
        ingredient_overrides={"task": "some task"},
    )
    assert "${{ inputs.task }}" in result["content"]
