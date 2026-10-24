"""Dataflow and routing contracts for the merge-prs PR cursor."""

from __future__ import annotations

import pytest

from autoskillit.core import BoundValueOrigin
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.analysis._analysis_detectors import _context_refs_in_value
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture(scope="module")
def recipe():
    return load_recipe(builtin_recipes_dir() / "merge-prs.yaml")


def test_cursor_steps_have_defined_inputs_and_single_writer(recipe) -> None:
    invocation = bind_recipe(recipe).for_step("merge_pr")
    assert invocation is not None
    for name, dependency in (
        ("pr_number", "current_pr_number"),
        ("complexity", "current_pr_complexity"),
    ):
        bound = invocation.skill_input(name)
        assert bound is not None
        assert bound.origin is BoundValueOrigin.CONTEXT
        assert bound.context_dependencies == (dependency,)

    cursor_fields = {"current_pr_number", "current_pr_complexity"}
    cursor_steps = {
        "get_first_pr_number": "autoskillit.recipe._cmd_rpc.first_ordered_pr",
        "advance_merge_pr": "autoskillit.recipe._cmd_rpc.next_ordered_pr",
    }

    must_defined = make_validation_context(recipe).must_defined_context
    assert {"current_pr_number", "pr_order_file"} <= must_defined.get(
        "advance_merge_pr", frozenset()
    )

    writers = {
        name
        for name, step in recipe.steps.items()
        if cursor_fields.intersection(set(step.capture) | set(step.capture_list))
    }
    assert writers == set(cursor_steps)
    assert not cursor_fields.intersection(
        set(recipe.steps["merge_pr"].capture) | set(recipe.steps["merge_pr"].capture_list)
    )
    merge_inputs = recipe.steps["merge_pr"].with_args["skill_inputs"]
    assert merge_inputs["pr_number"] == "${{ context.current_pr_number }}"
    assert merge_inputs["complexity"] == "${{ context.current_pr_complexity }}"

    for step_name, callable_name in cursor_steps.items():
        step = recipe.steps[step_name]
        assert step.tool == "run_python"
        assert step.with_args.get("callable") == callable_name
        assert cursor_fields <= set(step.capture)
        assert step.capture["current_pr_number"].from_ == "${{ result.pr_number }}"
        assert step.capture["current_pr_complexity"].from_ == "${{ result.complexity }}"


def test_pr_order_file_has_declared_recipe_consumers(recipe) -> None:
    consumers = {
        name
        for name, step in recipe.steps.items()
        if "pr_order_file" in _context_refs_in_value(step.declared_with_args or {})
    }
    assert consumers == {
        "get_first_pr_number",
        "advance_merge_pr",
        "open_integration_pr",
    }

    writers = {
        name
        for name, step in recipe.steps.items()
        if "pr_order_file" in step.capture or "pr_order_file" in step.capture_list
    }
    assert writers == {"analyze_prs"}

    integration_inputs = recipe.steps["open_integration_pr"].with_args["skill_inputs"]
    assert integration_inputs["pr_order_file"] == "${{ context.pr_order_file }}"

    shell_readers = {
        name
        for name, step in recipe.steps.items()
        if step.tool == "run_cmd"
        and "pr_order_file" in _context_refs_in_value((step.declared_with_args or {}).get("cmd"))
    }
    assert shell_readers == set()


def test_cursor_routes_preserve_plan_iteration_and_pr_advancement(recipe) -> None:
    next_part = recipe.steps["next_part_or_next_pr"]
    assert next_part.on_result is not None
    assert next_part.on_result.routes == {
        "more_parts": "select_next_plan_part",
        "next_pr": "advance_merge_pr",
    }

    advance = recipe.steps["advance_merge_pr"]
    assert advance.on_result is not None
    assert [(condition.when, condition.route) for condition in advance.on_result.conditions] == [
        (
            "${{ context.queue_mode }} == true and ${{ result.done }} == true",
            "collect_and_check_impl_plans",
        ),
        ("${{ result.done }} == true", "push_batch_branch"),
        ("${{ context.queue_mode }} == true", "get_next_pr_branch"),
        (None, "check_pr_merge_loop"),
    ]
