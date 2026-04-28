"""Tests for step graph construction, ValidationContext, and action-type validation."""

from __future__ import annotations

import pytest
import yaml

from autoskillit.recipe.io import _parse_recipe
from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules, validate_recipe
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# skip_when_false bypass edge tests
# ---------------------------------------------------------------------------


def test_build_step_graph_adds_bypass_edges_for_skip_when_false() -> None:
    """When a step has skip_when_false, predecessors get a direct edge to its on_success."""
    from autoskillit.recipe.validator import _build_step_graph

    # Recipe: entry → optional_step (skip_when_false) → final_step
    # Expected bypass edge: entry → final_step
    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="optional_step"),
            "optional_step": RecipeStep(
                tool="run_skill",
                on_success="final_step",
                skip_when_false="inputs.flag",
            ),
            "final_step": RecipeStep(action="stop", message="done"),
        },
        ingredients={"flag": RecipeIngredient(description="", required=False, default="true")},
        kitchen_rules=["test"],
    )
    graph = _build_step_graph(recipe)
    # Bypass edge: entry can reach final_step directly (skip of optional_step)
    assert "final_step" in graph["entry"]


def test_build_step_graph_bypass_does_not_remove_normal_edge() -> None:
    """The bypass edge is additional, not a replacement for the normal edge."""
    from autoskillit.recipe.validator import _build_step_graph

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "entry": RecipeStep(tool="run_cmd", on_success="optional_step"),
            "optional_step": RecipeStep(
                tool="run_skill",
                on_success="final_step",
                skip_when_false="inputs.flag",
            ),
            "final_step": RecipeStep(action="stop", message="done"),
        },
        ingredients={"flag": RecipeIngredient(description="", required=False, default="true")},
        kitchen_rules=["test"],
    )
    graph = _build_step_graph(recipe)
    # Normal edge still present
    assert "optional_step" in graph["entry"]
    # Bypass edge also present
    assert "final_step" in graph["entry"]


# ---------------------------------------------------------------------------
# _build_step_graph predicate on_result tests
# ---------------------------------------------------------------------------


class TestPredicateBuildStepGraph:
    """_build_step_graph includes condition.route edges."""

    def test_build_step_graph_includes_condition_routes(self) -> None:
        """_build_step_graph produces edges for condition.route targets."""
        from autoskillit.recipe.validator import _build_step_graph

        wf = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "assess": {"action": "stop", "message": "Assess."},
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            }
        )
        graph = _build_step_graph(wf)
        assert "assess" in graph["merge"]
        assert "cleanup" in graph["merge"]
        assert "push" in graph["merge"]


# ---------------------------------------------------------------------------
# ValidationContext tests
# ---------------------------------------------------------------------------


def test_run_semantic_rules_builds_step_graph_exactly_once(monkeypatch):
    """_build_step_graph is called only once regardless of how many rules need it."""
    from autoskillit.recipe import _analysis

    call_count = []
    real_fn = _analysis._build_step_graph

    def counting_fn(recipe):
        call_count.append(1)
        return real_fn(recipe)

    monkeypatch.setattr(_analysis, "_build_step_graph", counting_fn)

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    run_semantic_rules(recipe)

    assert len(call_count) == 1


def test_run_semantic_rules_calls_analyze_dataflow_exactly_once(monkeypatch):
    """analyze_dataflow is called only once regardless of how many rules consume it."""
    from autoskillit.recipe import _analysis

    call_count = []
    real_fn = _analysis.analyze_dataflow

    def counting_fn(recipe, **kwargs):
        call_count.append(1)
        return real_fn(recipe, **kwargs)

    monkeypatch.setattr(_analysis, "analyze_dataflow", counting_fn)

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    run_semantic_rules(recipe)

    assert len(call_count) == 1


def test_validation_context_exposes_recipe_step_graph_and_dataflow():
    """ValidationContext contains recipe, step_graph, and dataflow attributes."""
    from autoskillit.recipe._analysis import ValidationContext, make_validation_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {"stop": {"action": "stop", "message": "done"}},
        }
    )
    ctx = make_validation_context(recipe)

    assert isinstance(ctx, ValidationContext)
    assert ctx.recipe is recipe
    assert isinstance(ctx.step_graph, dict)
    assert hasattr(ctx.dataflow, "warnings")


def test_analyze_dataflow_accepts_prebuilt_step_graph():
    """analyze_dataflow can reuse a pre-built step_graph to avoid duplicate computation."""
    from autoskillit.recipe._analysis import _build_step_graph, analyze_dataflow

    recipe = _parse_recipe(
        {
            "name": "test",
            "description": "test",
            "autoskillit_version": "0.2.0",
            "kitchen_rules": ["Never use native tools"],
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "capture": {"wp": "${{ result.worktree_path }}"},
                    "on_success": "use_it",
                },
                "use_it": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.wp }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            },
        }
    )
    graph = _build_step_graph(recipe)
    report1 = analyze_dataflow(recipe, step_graph=graph)
    report2 = analyze_dataflow(recipe)
    assert report1.warnings == report2.warnings


def test_validate_recipe_catches_rectify_with_missing_investigation_path_capture() -> None:
    """1h: validate_recipe catches missing investigation_path capture before rectify step.

    When rectify is invoked with ${{ context.investigation_path }} but no preceding step
    captures investigation_path, the existing undeclared-context-reference validator must
    return an error — confirming the enforcement layer works for the new context variable.
    """
    recipe = _parse_recipe(
        yaml.safe_load(
            """\
name: test-missing-capture
description: Recipe with investigate→rectify but no capture block
kitchen_rules:
  - test
steps:
  investigate:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate the bug"
    on_success: rectify
    on_failure: done
  rectify:
    tool: run_skill
    with:
      skill_command: "/autoskillit:rectify ${{ context.investigation_path }}"
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done.
"""
        )
    )
    errors = validate_recipe(recipe)
    assert any("investigation_path" in e for e in errors), (
        "validate_recipe must report an error about undeclared context reference "
        "'investigation_path' when the investigate step has no capture block"
    )


# ---------------------------------------------------------------------------
# TestConfirmAction — action: "confirm" validation
# ---------------------------------------------------------------------------


class TestConfirmAction:
    def test_confirm_action_requires_message(self) -> None:
        """action: confirm without message is a validation error."""
        recipe = _make_workflow(
            {
                "step1": {"action": "confirm", "on_success": "done", "on_failure": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        errors = validate_recipe(recipe)
        assert any("message" in e.lower() for e in errors), (
            "Expected a validation error mentioning 'message'"
        )
        assert any("confirm" in e.lower() for e in errors), (
            "Expected a validation error mentioning 'confirm'"
        )

    def test_confirm_action_requires_on_success(self) -> None:
        """action: confirm without on_success is a validation error."""
        recipe = _make_workflow(
            {
                "step1": {"action": "confirm", "message": "Delete?", "on_failure": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        errors = validate_recipe(recipe)
        assert any("on_success" in e.lower() for e in errors)

    def test_confirm_action_requires_on_failure(self) -> None:
        """action: confirm without on_failure is a validation error."""
        recipe = _make_workflow(
            {
                "step1": {"action": "confirm", "message": "Delete?", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        errors = validate_recipe(recipe)
        assert any("on_failure" in e.lower() for e in errors)

    def test_confirm_action_valid_step(self) -> None:
        """action: confirm with all required fields passes validation."""
        recipe = _make_workflow(
            {
                "confirm_step": {
                    "action": "confirm",
                    "message": "Delete the clone?",
                    "on_success": "delete_step",
                    "on_failure": "done",
                },
                "delete_step": {
                    "tool": "remove_clone",
                    "with": {"clone_path": "/tmp/clone", "keep": "false"},
                    "on_success": "done",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        errors = validate_recipe(recipe)
        assert not errors

    def test_confirm_action_routing_targets_validated(self) -> None:
        """on_success and on_failure of a confirm step must name defined steps."""
        recipe = _make_workflow(
            {
                "confirm_step": {
                    "action": "confirm",
                    "message": "Delete?",
                    "on_success": "nonexistent_step",
                    "on_failure": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        errors = validate_recipe(recipe)
        assert any("nonexistent_step" in e for e in errors)


# ---------------------------------------------------------------------------
# RECIPE-8: constant step type — structural validation
# ---------------------------------------------------------------------------


class TestConstantStepValidation:
    def test_constant_step_is_valid_discriminator(self) -> None:
        """A step with only constant set passes structural validation."""
        recipe = _make_workflow({"step1": {"constant": "main", "on_success": "done"}})
        errors = validate_recipe(recipe)
        assert errors == []

    def test_constant_step_rejected_with_tool(self) -> None:
        """constant + tool on same step is rejected (multiple discriminators)."""
        recipe = _make_workflow(
            {"step1": {"constant": "main", "tool": "run_cmd", "on_success": "done"}}
        )
        errors = validate_recipe(recipe)
        assert any("multiple discriminators" in e for e in errors)

    def test_constant_step_capture_allows_literal_values(self) -> None:
        """constant step capture values may be plain strings (no result.* required)."""
        step_data = {
            "constant": "main",
            "capture": {"merge_target": "main"},
            "on_success": "done",
        }
        recipe = _make_workflow({"step1": step_data})
        errors = validate_recipe(recipe)
        assert errors == []

    def test_non_constant_step_capture_still_requires_template(self) -> None:
        """Non-constant step capture still requires ${{ result.* }} expression."""
        step_data = {
            "tool": "run_cmd",
            "with": {"command": "echo hi"},
            "capture": {"out": "literal_no_template"},
            "on_success": "done",
        }
        recipe = _make_workflow({"step1": step_data})
        errors = validate_recipe(recipe)
        assert any("result." in e for e in errors)

    def test_step_without_any_discriminator_still_rejected(self) -> None:
        """A step with no discriminator (no tool/action/python/constant) is invalid."""
        recipe = _make_workflow({"step1": {"on_success": "done"}})
        errors = validate_recipe(recipe)
        assert any("must have" in e for e in errors)
