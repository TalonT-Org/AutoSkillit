from __future__ import annotations

import pytest

from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestMergeRoutingIncompleteRule:
    """Tests for the merge-routing-incomplete semantic rule (RMR*)."""

    def _make_merge_step(self, conditions: list[dict]) -> Recipe:
        """Build a minimal recipe with a merge_worktree step using predicate on_result."""
        return _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": conditions,
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "recover": {"action": "stop", "message": "Recover."},
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )

    def test_rmr1_fires_when_test_gate_missing(self):
        """RMR1: ERROR when test_gate is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "test_gate" in errors[0].message

    def test_rmr2_fires_when_post_rebase_test_gate_missing(self):
        """RMR2: ERROR when post_rebase_test_gate is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "post_rebase_test_gate" in errors[0].message

    def test_rmr3_fires_when_rebase_missing(self):
        """RMR3: ERROR when rebase is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "rebase" in errors[0].message

    def test_rmr4_clears_when_all_four_covered(self):
        """RMR4: No finding when all recoverable values are explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'dirty_tree'", "route": "recover"},
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []

    def test_rmr7_fires_when_dirty_tree_missing(self):
        """RMR7: ERROR when dirty_tree is not explicitly routed."""
        recipe = self._make_merge_step(
            [
                {"when": "result.failed_step == 'test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "recover"},
                {"when": "result.failed_step == 'rebase'", "route": "recover"},
                {"when": "result.error", "route": "escalate"},
                {"route": "done"},
            ]
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert len(errors) == 1
        assert "dirty_tree" in errors[0].message

    def test_rmr5_does_not_fire_for_non_merge_worktree_step(self):
        """RMR5: Rule is scoped to merge_worktree steps only."""
        recipe = _make_workflow(
            {
                "run": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:implement-worktree", "cwd": "/tmp"},
                    "on_result": [
                        {"when": "result.error", "route": "done"},
                        {"route": "done"},
                    ],
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []

    def test_rmr6_does_not_fire_when_no_on_result(self):
        """RMR6: Rule is silent for merge_worktree steps without on_result."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_success": "done",
                    "on_failure": "escalate",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-routing-incomplete"]
        assert errors == []
