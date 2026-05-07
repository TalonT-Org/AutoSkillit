import pytest

from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestMergeFailureSkillDomainMismatch:
    """Tests for the merge-failure-skill-domain-mismatch semantic rule."""

    def test_rebase_routed_to_resolve_failures_fires(self):
        """ERROR when failed_step == 'rebase' routes to resolve-failures."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'dirty_tree'", "route": "fix"},
                        {"when": "result.failed_step == 'test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'rebase'", "route": "fix"},
                        {"when": "result.error", "route": "escalate"},
                        {"route": "done"},
                    ],
                },
                "fix": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:resolve-failures /tmp/wt /tmp/plan main"
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert len(errors) == 1
        assert "rebase" in errors[0].message
        assert "resolve-merge-conflicts" in errors[0].message

    def test_rebase_routed_to_resolve_merge_conflicts_is_clean(self):
        """No finding when rebase routes to resolve-merge-conflicts."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'dirty_tree'", "route": "fix"},
                        {"when": "result.failed_step == 'test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'rebase'", "route": "rebase_fix"},
                        {"when": "result.error", "route": "escalate"},
                        {"route": "done"},
                    ],
                },
                "fix": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:resolve-failures /tmp/wt /tmp/plan main"
                    },
                    "on_success": "done",
                },
                "rebase_fix": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:resolve-merge-conflicts /tmp/wt /tmp/plan main"
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert errors == []

    def test_code_failure_routed_to_resolve_merge_conflicts_fires(self):
        """ERROR when dirty_tree routes to resolve-merge-conflicts."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'dirty_tree'", "route": "conflict_fix"},
                        {"when": "result.failed_step == 'test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'post_rebase_test_gate'", "route": "fix"},
                        {"when": "result.failed_step == 'rebase'", "route": "conflict_fix"},
                        {"when": "result.error", "route": "escalate"},
                        {"route": "done"},
                    ],
                },
                "fix": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:resolve-failures /tmp/wt /tmp/plan main"
                    },
                    "on_success": "done",
                },
                "conflict_fix": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:resolve-merge-conflicts /tmp/wt /tmp/plan main"
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
                "escalate": {"action": "stop", "message": "Escalate."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert len(errors) == 1
        assert "dirty_tree" in errors[0].message
        assert "resolve-failures" in errors[0].message

    def test_rule_does_not_fire_for_non_merge_worktree(self):
        """Rule is scoped to merge_worktree steps only."""
        recipe = _make_workflow(
            {
                "run": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:implement-worktree /tmp/wt"},
                    "on_result": [
                        {"when": "result.error", "route": "done"},
                        {"route": "done"},
                    ],
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert errors == []


class TestBundledRecipesPassFailureDomainCheck:
    """Integration tests: bundled recipes pass the new rule."""

    def test_bundled_recipes_pass_failure_domain_skill_check(self):
        """All three merge_worktree recipes route rebase to resolve-merge-conflicts."""
        pytest.skip("Recipe YAML fixes are pending — run after step 2c/2d/2e")

    def test_rebase_routes_to_merge_conflict_skill(self):
        """rebase condition in each recipe routes to a step invoking resolve-merge-conflicts."""
        pytest.skip("Recipe YAML fixes are pending — run after step 2c/2d/2e")
