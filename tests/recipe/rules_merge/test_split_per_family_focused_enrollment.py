"""Per-family focused tests for the enrollment sibling (R5, R8)."""

from __future__ import annotations

import pytest

from tests.recipe.rules_merge._helpers import (
    assert_rule_does_not_fire,
    assert_rule_fires,
    build_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_r5_gh_pr_merge_routes_to_success_register_clone_fires() -> None:
    """R5 fires when a ``gh pr merge`` step routes on_failure to ``register_clone_success``."""
    recipe = build_recipe(
        {
            "merge_step": {
                "tool": "run_cmd",
                "with": {"cmd": "gh pr merge --squash 123"},
                "on_success": "done",
                "on_failure": "register_clone_success",
            },
            "register_clone_success": {"action": "stop"},
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="gh-pr-merge-silent-success-routing")


def test_r5_release_issue_prefix_step_is_exempt() -> None:
    """R5 does NOT fire for ``release_issue_*`` steps (cleanup exemption)."""
    recipe = build_recipe(
        {
            "release_issue_step": {
                "tool": "run_cmd",
                "with": {"cmd": "gh pr merge --squash 456"},
                "on_success": "done",
                "on_failure": "register_clone_success",
            },
            "register_clone_success": {"action": "stop"},
            "done": {"action": "stop"},
        },
    )
    assert_rule_does_not_fire(recipe, rule_name="gh-pr-merge-silent-success-routing")


def test_r8_toggle_auto_merge_reachable_from_no_auto_arm_fires() -> None:
    """R8 fires when ``toggle_auto_merge`` is reachable from ``auto_merge_available == false``."""
    recipe = build_recipe(
        {
            "decide": {
                "tool": "run_cmd",
                "with": {"cmd": "echo check"},
                "on_result": [
                    {
                        "route": "no_auto_arm",
                        "when": "result.auto_merge_available == 'false'",
                    },
                    {
                        "route": "auto_arm",
                        "when": None,
                    },
                ],
            },
            "no_auto_arm": {"tool": "toggle_auto_merge"},
            "auto_arm": {"action": "noop"},
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-enrollment-auto-consistency")
