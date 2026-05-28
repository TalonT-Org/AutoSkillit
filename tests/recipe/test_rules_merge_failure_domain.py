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
                        "skill_command": (
                            "/autoskillit:resolve-merge-conflicts /tmp/wt /tmp/plan main"
                        )
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
                        "skill_command": (
                            "/autoskillit:resolve-merge-conflicts /tmp/wt /tmp/plan main"
                        )
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


@pytest.mark.medium
class TestBundledRecipesPassFailureDomainCheck:
    """Integration tests: bundled recipes pass the new rule."""

    @pytest.mark.parametrize(
        "recipe_name",
        ["implementation", "remediation", "implementation-groups"],
    )
    def test_bundled_recipes_pass_failure_domain_skill_check(self, recipe_name):
        """All three merge_worktree recipes route rebase to resolve-merge-conflicts."""
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert errors == [], f"{recipe_name}: {[e.message for e in errors]}"

    @pytest.mark.parametrize(
        "recipe_name,merge_step_name",
        [
            ("implementation", "merge"),
            ("remediation", "merge"),
            ("implementation-groups", "merge"),
        ],
    )
    def test_rebase_routes_to_merge_conflict_skill(self, recipe_name, merge_step_name):
        """rebase condition routes (possibly via guard) to resolve-merge-conflicts."""
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        merge_step = recipe.steps[merge_step_name]
        if merge_step.on_result is None or not merge_step.on_result.conditions:
            pytest.skip("merge step has no on_result conditions")
        found_rebase = False
        for cond in merge_step.on_result.conditions:
            if cond.when and "rebase" in cond.when and "post_rebase" not in cond.when:
                found_rebase = True
                target_step = recipe.steps[cond.route]
                skill_cmd = (target_step.with_args or {}).get("skill_command", "")
                if not skill_cmd and target_step.tool == "run_python" and target_step.on_result:
                    for inner in target_step.on_result.conditions:
                        if inner.when and "max_exceeded" in inner.when:
                            continue
                        inner_step = recipe.steps.get(inner.route)
                        if inner_step:
                            skill_cmd = (inner_step.with_args or {}).get("skill_command", "")
                        break
                assert "resolve-merge-conflicts" in skill_cmd, (
                    f"{recipe_name}: rebase routes to '{cond.route}' which invokes "
                    f"'{skill_cmd}', expected resolve-merge-conflicts"
                )
        assert found_rebase, f"{recipe_name}: no rebase condition found in merge step"
