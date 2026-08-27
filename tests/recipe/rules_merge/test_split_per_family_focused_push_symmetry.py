"""Per-family focused tests for the push-symmetry sibling (R9)."""

from __future__ import annotations

import pytest

from tests.recipe.rules_merge._helpers import (
    assert_rule_fires,
    build_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_r9_two_merges_without_push_between_them_fires() -> None:
    """R9 fires when ``merge_a``'s success fallthrough reaches ``merge_b`` without push.

    The success path is: ``merge_a -> merge_b`` with no ``push_to_remote``
    step in between. R9 must flag this.
    """
    recipe = build_recipe(
        {
            "merge_a": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "x", "base_branch": "main"},
                "on_result": [{"route": "merge_b", "when": None}],
                "on_success": "merge_b",
                "on_failure": "done",
            },
            "merge_b": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "y", "base_branch": "main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-site-push-symmetry")
