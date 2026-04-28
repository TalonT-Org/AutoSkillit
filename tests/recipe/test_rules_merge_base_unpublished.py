from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestMergeBaseUnpublishedRule:
    """Tests for the merge-base-unpublished semantic rule."""

    def test_merge_base_unpublished_rule_fires_when_push_absent(self) -> None:
        """merge-base-unpublished ERROR fires when merge_worktree.base_branch
        is a context variable without a preceding push_to_remote step."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": ".", "run_name": "test"},
                    "on_success": "create_branch",
                },
                "create_branch": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo test", "cwd": "/tmp"},
                    "capture": {"merge_target": "${{ result.stdout }}"},
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "${{ context.merge_target }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "merge-base-unpublished"]
        assert len(rule_findings) >= 1
        assert any(f.severity == Severity.ERROR for f in rule_findings)

    def test_merge_base_unpublished_rule_passes_when_push_precedes_merge(self) -> None:
        """merge-base-unpublished does NOT fire when push_to_remote appears
        on the path to merge_worktree for the same context variable."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": ".", "run_name": "test"},
                    "on_success": "create_branch",
                },
                "create_branch": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo test", "cwd": "/tmp"},
                    "capture": {"merge_target": "${{ result.stdout }}"},
                    "on_success": "push_target",
                },
                "push_target": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "/tmp",
                        "branch": "${{ context.merge_target }}",
                        "remote_url": "https://example.com/repo.git",
                    },
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "${{ context.merge_target }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)

    def test_merge_base_unpublished_rule_does_not_fire_for_literal_branch(self) -> None:
        """merge-base-unpublished does NOT fire when base_branch is a
        literal string — literals like 'main' are always published."""
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_cmd",
                    "with": {"cmd": "echo ok", "cwd": "/tmp"},
                    "on_success": "merge_step",
                },
                "merge_step": {
                    "tool": "merge_worktree",
                    "with": {
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)

    def test_implementation_pipeline_satisfies_push_before_merge_contract(self) -> None:
        """implementation.yaml must pass the merge-base-unpublished
        rule after the push_merge_target step is added."""
        recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
        findings = run_semantic_rules(recipe)
        assert not any(f.rule == "merge-base-unpublished" for f in findings)
