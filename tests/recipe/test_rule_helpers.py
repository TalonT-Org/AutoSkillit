"""Unit tests for recipe rule helper functions."""

from __future__ import annotations

import pytest

from autoskillit.recipe._analysis import _build_step_graph
from autoskillit.recipe._rule_helpers import is_success_stop, push_reachable
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _minimal_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-push-reachable",
        description="Synthetic recipe for push_reachable tests.",
        version="0.2.0",
        kitchen_rules="none",
        steps=steps,
    )


def test_push_reachable_finds_push_to_remote() -> None:
    steps = {
        "skill_step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
            on_success="push_step",
            on_failure="done",
        ),
        "push_step": RecipeStep(
            tool="push_to_remote",
            with_args={"clone_path": "/tmp", "remote_url": "r", "branch": "b"},
            on_success="done",
            on_failure="done",
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _minimal_recipe(steps)
    graph = _build_step_graph(recipe)
    reachable, push_step = push_reachable(graph, "skill_step", recipe)
    assert reachable is True
    assert push_step == "push_step"


def test_push_reachable_traverses_through_merge_worktree() -> None:
    steps = {
        "skill_step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
            on_success="merge_step",
            on_failure="done",
        ),
        "merge_step": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp"},
            on_success="check_step",
            on_failure="done",
        ),
        "check_step": RecipeStep(
            tool="check_has_commits",
            with_args={"clone_path": "/tmp"},
            on_success="push_step",
            on_failure="done",
        ),
        "push_step": RecipeStep(
            tool="push_to_remote",
            with_args={"clone_path": "/tmp", "remote_url": "r", "branch": "b"},
            on_success="done",
            on_failure="done",
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _minimal_recipe(steps)
    graph = _build_step_graph(recipe)
    reachable, push_step = push_reachable(graph, "skill_step", recipe)
    assert reachable is True
    assert push_step == "push_step"


def test_push_reachable_returns_false_for_run_cmd_push() -> None:
    steps = {
        "skill_step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
            on_success="cmd_push",
            on_failure="done",
        ),
        "cmd_push": RecipeStep(
            tool="run_cmd",
            with_args={"cmd": "push_branch.sh"},
            on_success="done",
            on_failure="done",
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _minimal_recipe(steps)
    graph = _build_step_graph(recipe)
    reachable, push_step = push_reachable(graph, "skill_step", recipe)
    assert reachable is False
    assert push_step is None


class TestIsSuccessStop:
    def test_success_true_in_message(self) -> None:
        step = RecipeStep(
            action="stop",
            message=(
                "Output the following sentinel JSON:\n\n"
                'Example sentinel: {"success": true, "reason": "done"}'
            ),
        )
        assert is_success_stop(step) is True

    def test_success_false_in_message(self) -> None:
        step = RecipeStep(
            action="stop",
            message=(
                "Output the following sentinel JSON:\n\n"
                'Example sentinel: {"success": false, "reason": "failed"}'
            ),
        )
        assert is_success_stop(step) is False

    def test_success_equals_true_string(self) -> None:
        step = RecipeStep(
            action="stop",
            message=(
                "Output the following sentinel JSON:\n\n"
                'Example sentinel: {"success": "true", "reason": "done"}'
            ),
        )
        assert is_success_stop(step) is True

    def test_non_stop_step(self) -> None:
        step = RecipeStep(
            tool="run_skill",
            on_success="done",
            with_args={"skill_command": "/x"},
        )
        assert is_success_stop(step) is False

    def test_no_success_field(self) -> None:
        step = RecipeStep(
            action="stop",
            message='Example sentinel: {"reason": "timeout"}',
        )
        assert is_success_stop(step) is False

    def test_none_message(self) -> None:
        step = RecipeStep(action="stop", message=None)
        assert is_success_stop(step) is False


def test_push_reachable_returns_false_for_run_python_push() -> None:
    steps = {
        "skill_step": RecipeStep(
            tool="run_skill",
            with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
            on_success="py_push",
            on_failure="done",
        ),
        "py_push": RecipeStep(
            tool="run_python",
            with_args={"callable": "force_push_and_wait_mergeability"},
            on_success="done",
            on_failure="done",
        ),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _minimal_recipe(steps)
    graph = _build_step_graph(recipe)
    reachable, push_step = push_reachable(graph, "skill_step", recipe)
    assert reachable is False
    assert push_step is None
