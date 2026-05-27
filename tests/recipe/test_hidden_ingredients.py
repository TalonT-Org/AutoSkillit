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
            for v in step.on_result.routes.values():
                assert v != "skippable"


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
