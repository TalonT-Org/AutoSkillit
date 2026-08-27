"""Per-family focused tests for the wait sibling (R7)."""

from __future__ import annotations

import pytest

from tests.recipe.rules_merge._helpers import (
    assert_rule_does_not_fire,
    assert_rule_fires,
    build_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_r7_release_issue_reachable_from_merge_wait_timeout_fires() -> None:
    """R7 fires when ``release_issue`` is reachable from a merge-wait timeout exit."""
    recipe = build_recipe(
        {
            "wait_for_merge_queue": {
                "tool": "wait_for_merge_queue",
                "on_result": [
                    {
                        "route": "release_issue_failure",
                        "when": "result.timeout == true",
                    },
                    {
                        "route": "done",
                        "when": None,
                    },
                ],
                "on_success": "done",
                "on_failure": "release_issue_failure",
            },
            "release_issue_failure": {
                "tool": "release_issue",
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="release-issue-on-unconfirmed-merge")


def test_r7_timeout_via_register_clone_unconfirmed_does_not_fire() -> None:
    """R7 does NOT fire when the timeout exit routes to ``register_clone_unconfirmed``."""
    recipe = build_recipe(
        {
            "wait_for_merge_queue": {
                "tool": "wait_for_merge_queue",
                "on_result": [
                    {
                        "route": "register_clone_unconfirmed",
                        "when": "result.timeout == true",
                    },
                    {
                        "route": "done",
                        "when": None,
                    },
                ],
                "on_success": "done",
                "on_failure": "register_clone_unconfirmed",
            },
            "register_clone_unconfirmed": {"action": "noop"},
            "done": {"action": "stop"},
        },
    )
    assert_rule_does_not_fire(recipe, rule_name="release-issue-on-unconfirmed-merge")
