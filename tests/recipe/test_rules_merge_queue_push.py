"""Tests for push-after-queue-requires-queued-branch-route semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "push-after-queue-requires-queued-branch-route"


def _make_recipe_missing_queued_branch_route() -> Recipe:
    """Minimal recipe: wait_for_queue → eject → push with no queued_branch route."""
    steps = {
        "wait_for_queue": RecipeStep(
            name="wait_for_queue",
            tool="wait_for_merge_queue",
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        when="${{ result.pr_state }} == 'ejected'",
                        route="queue_ejected_fix",
                    ),
                ]
            ),
        ),
        "queue_ejected_fix": RecipeStep(
            name="queue_ejected_fix",
            tool="run_python",
            on_success="re_push_queue_fix",
        ),
        "re_push_queue_fix": RecipeStep(
            name="re_push_queue_fix",
            tool="push_to_remote",
            on_success="done",
            on_failure="terminal_failure",
        ),
        "terminal_failure": RecipeStep(
            name="terminal_failure",
            action="stop",
        ),
        "done": RecipeStep(
            name="done",
            action="stop",
        ),
    }
    return Recipe(
        name="test-missing-route",
        description="test recipe missing queued_branch route",
        steps=steps,
    )


def _make_recipe_with_queued_branch_route() -> Recipe:
    """Minimal recipe: push has on_failure → classify_push_failure with queued_branch route."""
    steps = {
        "wait_for_queue": RecipeStep(
            name="wait_for_queue",
            tool="wait_for_merge_queue",
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        when="${{ result.pr_state }} == 'ejected'",
                        route="queue_ejected_fix",
                    ),
                ]
            ),
        ),
        "queue_ejected_fix": RecipeStep(
            name="queue_ejected_fix",
            tool="run_python",
            on_success="re_push_queue_fix",
        ),
        "re_push_queue_fix": RecipeStep(
            name="re_push_queue_fix",
            tool="push_to_remote",
            on_success="done",
            on_failure="classify_push_failure",
        ),
        "classify_push_failure": RecipeStep(
            name="classify_push_failure",
            action="route",
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        when="${{ context.push_error_type }} == queued_branch",
                        route="wait_dequeue_retry",
                    ),
                    StepResultCondition(
                        when=None,
                        route="terminal_failure",
                    ),
                ]
            ),
        ),
        "wait_dequeue_retry": RecipeStep(
            name="wait_dequeue_retry",
            tool="wait_for_merge_queue",
            on_success="re_push_queue_fix",
        ),
        "terminal_failure": RecipeStep(
            name="terminal_failure",
            action="stop",
        ),
        "done": RecipeStep(
            name="done",
            action="stop",
        ),
    }
    return Recipe(
        name="test-with-route",
        description="test recipe with queued_branch route",
        steps=steps,
    )


def test_rule_fires_when_queued_branch_route_missing():
    """push_to_remote reachable from ejection route without queued_branch triggers finding."""
    recipe = _make_recipe_missing_queued_branch_route()
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE_NAME]
    assert len(matching) == 1
    assert "re_push_queue_fix" in matching[0].step_name


def test_rule_passes_when_queued_branch_route_present():
    """push_to_remote with queued_branch route in failure chain produces no finding."""
    recipe = _make_recipe_with_queued_branch_route()
    findings = run_semantic_rules(recipe)
    matching = [f for f in findings if f.rule == _RULE_NAME]
    assert matching == []
