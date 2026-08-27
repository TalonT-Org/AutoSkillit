"""Per-family focused tests for the guards sibling (R4, R6)."""

from __future__ import annotations

import pytest

from tests.recipe.rules_merge._helpers import (
    assert_rule_does_not_fire,
    assert_rule_fires,
    build_recipe,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_r4_fix_cycle_without_loop_guard_fires() -> None:
    """R4 fires when merge → fix has no check_loop_iteration guard."""
    recipe = build_recipe(
        {
            "merge": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "x", "base_branch": "main"},
                "on_result": [
                    {
                        "route": "fix_step",
                        "when": "result.failed_step == 'dirty_tree'",
                    },
                ],
                "on_success": "done",
                "on_failure": "done",
            },
            "fix_step": {"action": "noop"},
            "test_step": {"action": "noop"},
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-fix-cycle-without-iteration-guard")


def test_r6_merge_without_commit_guard_fires() -> None:
    """R6 fires when a merge_worktree step has no commit_guard predecessor."""
    recipe = build_recipe(
        {
            "merge": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "x", "base_branch": "main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_fires(recipe, rule_name="merge-without-commit-guard")


def test_r6_merge_with_commit_guard_does_not_fire() -> None:
    """R6 does NOT fire when a ``commit_guard`` step is on the merge path."""
    recipe = build_recipe(
        {
            "commit_guard": {
                "tool": "run_cmd",
                "with": {"cmd": "git commit --allow-empty -m x"},
                "on_success": "merge",
            },
            "merge": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "x", "base_branch": "main"},
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop"},
        },
    )
    assert_rule_does_not_fire(recipe, rule_name="merge-without-commit-guard")
