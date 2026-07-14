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
            ("remediation", "pre_remediation_merge"),
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
                remediation_escalation_sites = {
                    ("remediation", "pre_remediation_merge"),
                    ("remediation", "merge"),
                }
                if (recipe_name, merge_step_name) in remediation_escalation_sites:
                    assert cond.route == "release_issue_failure"
                    continue
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


# ---------------------------------------------------------------------------
# Step 1b: REF_COHERENCE domain validation behavior tests
# ---------------------------------------------------------------------------


def _make_ref_coherence_recipe():
    return _make_workflow(
        {
            "merge": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {
                        "when": (
                            "result.failed_step == 'ref_coherence' "
                            "and result.remote_is_ancestor == true"
                        ),
                        "route": "check_ref_push_loop",
                    },
                    {
                        "when": "result.failed_step == 'ref_coherence'",
                        "route": "release_issue_failure",
                    },
                    {"when": "result.error", "route": "release_issue_failure"},
                    {"route": "done"},
                ],
            },
            "check_ref_push_loop": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.ref_push_count }}",
                    "max_iterations": "3",
                },
                "on_success": "ref_push",
                "on_failure": "release_issue_failure",
            },
            "ref_push": {
                "tool": "push_to_remote",
                "with": {
                    "clone_path": "${{ context.work_dir }}",
                    "remote_url": "${{ context.remote_url }}",
                    "branch": "main",
                },
                "on_success": "retry_merge",
                "on_failure": "release_issue_failure",
            },
            "retry_merge": {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_success": "done",
                "on_failure": "release_issue_failure",
            },
            "release_issue_failure": {"action": "stop", "message": "Escalate."},
            "done": {"action": "stop", "message": "Done."},
        }
    )


@pytest.mark.medium
class TestRefCoherenceDomainValidation:
    """REF_COHERENCE must be validated through recovery-class classification, not
    exact skill matching. An ancestry-aware arm reaching push_to_remote is correct;
    one reaching direct remediation is a mismatch."""

    def test_ref_coherence_maps_to_push_recovery_domain(self):
        """MergeFailedStep.REF_COHERENCE must be registered under the push_recovery domain."""
        from autoskillit.core.types import MergeFailedStep
        from autoskillit.recipe.rules import rules_merge

        assert (
            rules_merge._MERGE_FAILURE_DOMAINS.get(MergeFailedStep.REF_COHERENCE)
            == "push_recovery"
        ), "REF_COHERENCE must map to 'push_recovery' in _MERGE_FAILURE_DOMAINS"

    def test_push_recovery_required_class_mapping_exists(self):
        """_REQUIRED_RECOVERY_CLASS must declare push_recovery -> push_recovery."""
        from autoskillit.recipe.rules import rules_merge

        assert rules_merge._REQUIRED_RECOVERY_CLASS.get("push_recovery") == "push_recovery", (
            "_REQUIRED_RECOVERY_CLASS must require push_recovery for the push_recovery domain"
        )

    def test_ancestry_arm_reaching_push_to_remote_is_clean(self):
        """A ref_coherence ancestry arm that reaches push_to_remote via a guard
        must NOT trigger merge-failure-skill-domain-mismatch."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {
                            "when": (
                                "result.failed_step == 'ref_coherence' "
                                "and result.remote_is_ancestor == true"
                            ),
                            "route": "check_ref_push_loop",
                        },
                        {
                            "when": "result.failed_step == 'ref_coherence'",
                            "route": "release_issue_failure",
                        },
                        {"when": "result.error", "route": "release_issue_failure"},
                        {"route": "done"},
                    ],
                },
                "check_ref_push_loop": {
                    "tool": "run_python",
                    "with": {
                        "callable": "autoskillit.smoke_utils.check_loop_iteration",
                        "current_iteration": "${{ context.ref_push_count }}",
                        "max_iterations": "3",
                    },
                    "on_result": [
                        {
                            "when": "${{ result.max_exceeded }} == true",
                            "route": "release_issue_failure",
                        }
                    ],
                    "on_success": "ref_push",
                    "on_failure": "release_issue_failure",
                },
                "ref_push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "remote_url": "${{ context.remote_url }}",
                        "branch": "main",
                    },
                    "on_success": "retry_merge",
                    "on_failure": "release_issue_failure",
                },
                "retry_merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_success": "done",
                    "on_failure": "release_issue_failure",
                },
                "release_issue_failure": {"action": "stop", "message": "Escalate."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert errors == [], (
            f"ref_coherence ancestry arm reaching push_to_remote must be clean, "
            f"got: {[e.message for e in errors]}"
        )

    def test_ancestry_arm_reaching_direct_remediation_fires_mismatch(self):
        """A ref_coherence ancestry arm reaching direct_remediate (make-plan) must
        trigger exactly one mismatch finding naming expected push_recovery."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {
                            "when": (
                                "result.failed_step == 'ref_coherence' "
                                "and result.remote_is_ancestor == true"
                            ),
                            "route": "check_direct_loop",
                        },
                        {
                            "when": "result.failed_step == 'ref_coherence'",
                            "route": "release_issue_failure",
                        },
                        {"when": "result.error", "route": "release_issue_failure"},
                        {"route": "done"},
                    ],
                },
                "check_direct_loop": {
                    "tool": "run_python",
                    "with": {
                        "callable": "autoskillit.smoke_utils.check_loop_iteration",
                        "current_iteration": "${{ context.direct_count }}",
                        "max_iterations": "3",
                    },
                    "on_result": [
                        {
                            "when": "${{ result.max_exceeded }} == true",
                            "route": "release_issue_failure",
                        }
                    ],
                    "on_success": "make_plan",
                    "on_failure": "release_issue_failure",
                },
                "make_plan": {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": "/autoskillit:make-plan /tmp/plan",
                    },
                    "on_success": "done",
                    "on_failure": "release_issue_failure",
                },
                "release_issue_failure": {"action": "stop", "message": "Escalate."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        ref_coherence_errors = [e for e in errors if "ref_coherence" in e.message]
        assert len(ref_coherence_errors) == 1, (
            f"Expected exactly 1 ref_coherence mismatch, got: "
            f"{[e.message for e in ref_coherence_errors]}"
        )
        msg = ref_coherence_errors[0].message
        assert "push_recovery" in msg and "direct_remediate" in msg, (
            f"Mismatch message must report expected push_recovery and actual "
            f"direct_remediate, got: {msg}"
        )

    @pytest.mark.parametrize(
        ("argument", "unrelated_value"),
        [
            ("clone_path", "${{ context.other_work_dir }}"),
            ("remote_url", "${{ context.other_remote_url }}"),
            ("branch", "other-branch"),
        ],
    )
    def test_ancestry_arm_pushing_unrelated_target_fires_mismatch(
        self, argument: str, unrelated_value: str
    ) -> None:
        """Push recovery must target the failing merge's clone, remote, and branch."""
        recipe = _make_ref_coherence_recipe()
        recipe.steps["ref_push"].with_args[argument] = unrelated_value

        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert any(
            "classifies as None" in finding.message
            and "expected 'push_recovery'" in finding.message
            for finding in errors
        )

    def test_fallback_arm_is_not_misclassified_as_ancestry_arm(self):
        """The fallback ref_coherence arm (without remote_is_ancestor) must not
        be classified against the recovery-class requirement.

        Recipe with ancestry arm reaching push_recovery AND fallback arm routing
        to direct remediation: only the ancestry arm is validated; the fallback
        arm is an escalation terminal and not a recovery mismatch."""
        recipe = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {
                            "when": (
                                "result.failed_step == 'ref_coherence' "
                                "and result.remote_is_ancestor == true"
                            ),
                            "route": "check_ref_push_loop",
                        },
                        # Fallback arm — direct remediation as escalation.
                        # Per plan: fallback is intentional escalation terminal.
                        {
                            "when": "result.failed_step == 'ref_coherence'",
                            "route": "release_issue_failure",
                        },
                        {"when": "result.error", "route": "release_issue_failure"},
                        {"route": "done"},
                    ],
                },
                "check_ref_push_loop": {
                    "tool": "run_python",
                    "with": {
                        "callable": "autoskillit.smoke_utils.check_loop_iteration",
                        "current_iteration": "${{ context.ref_push_count }}",
                        "max_iterations": "3",
                    },
                    "on_success": "ref_push",
                    "on_failure": "release_issue_failure",
                },
                "ref_push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "remote_url": "${{ context.remote_url }}",
                        "branch": "main",
                    },
                    "on_success": "retry_merge",
                    "on_failure": "release_issue_failure",
                },
                "retry_merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_success": "done",
                    "on_failure": "release_issue_failure",
                },
                "release_issue_failure": {"action": "stop", "message": "Escalate."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert errors == [], (
            f"Fallback escalation arm must not be flagged when ancestry arm is correct, "
            f"got: {[e.message for e in errors]}"
        )

    def test_overlapping_fallback_before_ancestry_arm_fires(self):
        """A broad fallback cannot shadow the ancestry-qualified recovery arm."""
        recipe = _make_ref_coherence_recipe()
        merge_step = recipe.steps["merge"]
        assert merge_step.on_result is not None
        conditions = merge_step.on_result.conditions
        conditions[0], conditions[1] = conditions[1], conditions[0]

        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert any("must precede" in finding.message for finding in errors)

    def test_fallback_entering_recovery_instead_of_terminating_fires(self):
        """Divergence fallback must escalate instead of reusing push recovery."""
        recipe = _make_ref_coherence_recipe()
        merge_step = recipe.steps["merge"]
        assert merge_step.on_result is not None
        fallback = next(
            condition
            for condition in merge_step.on_result.conditions
            if condition.when
            and "ref_coherence" in condition.when
            and "remote_is_ancestor" not in condition.when
        )
        fallback.route = "check_ref_push_loop"

        findings = run_semantic_rules(recipe)
        errors = [f for f in findings if f.rule == "merge-failure-skill-domain-mismatch"]
        assert any("must terminate" in finding.message for finding in errors)
