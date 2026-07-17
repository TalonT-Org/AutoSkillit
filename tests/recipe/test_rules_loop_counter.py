"""Tests for loop-counter-cross-path-sharing, loop-guard-before-verify, and
loop-counter-not-reset-on-outer-cycle semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _guard_step(counter_var: str, *, non_exit: str, exit_route: str) -> RecipeStep:
    return RecipeStep(
        tool="run_python",
        with_args={
            "callable": "autoskillit.smoke_utils.check_loop_iteration",
            "current_iteration": f"${{{{ context.{counter_var} }}}}",
            "max_iterations": "3",
        },
        capture={counter_var: "${{ result.next_iteration }}"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(when="${{ result.max_exceeded }} == true", route=exit_route),
                StepResultCondition(route=non_exit),
            ]
        ),
        on_failure=exit_route,
        optional_context_refs=[counter_var],
    )


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-loop-counter",
        description="Test recipe for loop counter rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


class TestLoopCounterCrossPathSharing:
    def test_shared_counter_across_disjoint_entry_paths_fires(self) -> None:
        """Two disjoint paths enter the cycle at DIFFERENT members."""
        steps = {
            "initial_test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "merge_diagnose": RecipeStep(
                tool="run_python",
                with_args={"callable": "some.diagnose"},
                on_success="fix",
                on_failure="done",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="guard",
                on_failure="done",
            ),
            "guard": _guard_step("counter", non_exit="test", exit_route="done"),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert len(sharing) == 1
        assert sharing[0].severity == Severity.ERROR
        assert sharing[0].step_name == "guard"

    def test_shared_counter_single_external_predecessor_does_not_fire(self) -> None:
        steps = {
            "merge": RecipeStep(
                tool="run_skill",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="result.failed_step == 'dirty_tree'", route="guard_a"
                        ),
                        StepResultCondition(
                            when="result.failed_step == 'rebase'", route="guard_b"
                        ),
                        StepResultCondition(route="done"),
                    ]
                ),
                on_failure="done",
            ),
            "guard_a": _guard_step("merge_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("merge_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="merge",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert sharing == []

    def test_single_entry_path_counter_does_not_fire(self) -> None:
        steps = {
            "start": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="guard",
            ),
            "guard": _guard_step("counter", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert sharing == []

    @pytest.mark.parametrize(
        "recipe_name",
        ("remediation", "implementation", "implementation-groups", "merge-prs"),
    )
    def test_bundled_recipes_no_counter_sharing(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        findings = run_semantic_rules(recipe)
        sharing = [f for f in findings if f.rule == "loop-counter-cross-path-sharing"]
        assert sharing == [], f"{recipe_name}: {[(f.step_name, f.message) for f in sharing]}"


class TestLoopGuardBeforeVerify:
    def test_guard_before_verify_fires(self) -> None:
        steps = {
            "fix": RecipeStep(
                tool="run_skill",
                on_success="guard",
                on_failure="done",
            ),
            "guard": _guard_step("counter", non_exit="test", exit_route="done"),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="fix",
            ),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
        assert len(gbv) == 1
        assert gbv[0].severity == Severity.WARNING
        assert gbv[0].step_name == "guard"

    def test_verify_before_guard_does_not_fire(self) -> None:
        steps = {
            "fix": RecipeStep(
                tool="run_skill",
                on_success="test",
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                on_success="done",
                on_failure="guard",
            ),
            "guard": _guard_step("counter", non_exit="fix", exit_route="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
        assert gbv == []

    @pytest.mark.parametrize(
        "recipe_name",
        ("remediation", "implementation", "implementation-groups", "merge-prs"),
    )
    def test_bundled_recipes_no_guard_before_verify(self, recipe_name: str) -> None:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        findings = run_semantic_rules(recipe)
        gbv = [f for f in findings if f.rule == "loop-guard-before-verify"]
        assert gbv == [], f"{recipe_name}: {[(f.step_name, f.message) for f in gbv]}"


def _audit_outer_guard(counter_var: str, *, non_exit: str, exit_route: str) -> RecipeStep:
    """A check_loop_iteration guard whose counter contains 'audit_remediation'."""
    return _guard_step(counter_var, non_exit=non_exit, exit_route=exit_route)


class TestLoopCounterNotResetOnOuterCycle:
    def test_missing_reset_fires(self) -> None:
        """Inner guard reachable from audit_remediation outer without reset step fires."""
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="work", exit_route="done"
            ),
            "work": RecipeStep(tool="run_skill", on_success="inner_guard", on_failure="done"),
            "inner_guard": _guard_step("fix_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="test", on_failure="done"),
            "test": RecipeStep(tool="test_check", on_success="outer_guard", on_failure="fix"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert len(rule_findings) == 1
        assert rule_findings[0].severity == Severity.ERROR
        assert rule_findings[0].step_name == "inner_guard"

    def test_reset_present_does_not_fire(self) -> None:
        """A reset step on the path suppresses the finding."""
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="reset_fix", exit_route="done"
            ),
            "reset_fix": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.init_counter",
                    "counter_value": "",
                },
                capture={"fix_count": "${{ result.value }}"},
                on_success="work",
                on_failure="work",
            ),
            "work": RecipeStep(tool="run_skill", on_success="inner_guard", on_failure="done"),
            "inner_guard": _guard_step("fix_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="test", on_failure="done"),
            "test": RecipeStep(tool="test_check", on_success="outer_guard", on_failure="fix"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert rule_findings == []

    def test_post_audit_terminal_guard_excluded(self) -> None:
        """A guard downstream with no path back to outer is excluded by bilateral check."""
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="work", exit_route="done"
            ),
            "work": RecipeStep(tool="run_skill", on_success="push", on_failure="done"),
            "push": RecipeStep(tool="push_to_remote", on_success="ci_guard", on_failure="done"),
            "ci_guard": _guard_step("ci_count", non_exit="ci_watch", exit_route="done"),
            "ci_watch": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert rule_findings == []

    def test_wrapper_loop_exempt_counter_excluded(self) -> None:
        """group_iteration_count counter is exempt even when in the bilateral cycle."""
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="work", exit_route="done"
            ),
            "work": RecipeStep(tool="run_skill", on_success="inner_guard", on_failure="done"),
            "inner_guard": _guard_step(
                "group_iteration_count", non_exit="process", exit_route="done"
            ),
            "process": RecipeStep(tool="run_skill", on_success="test", on_failure="done"),
            "test": RecipeStep(tool="test_check", on_success="outer_guard", on_failure="process"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert rule_findings == []

    @pytest.mark.parametrize(
        "recipe_name",
        ("remediation", "implementation", "implementation-groups"),
    )
    def test_bundled_recipes_no_missing_reset(self, recipe_name: str) -> None:
        """After the dominator fix to ``loop-counter-not-reset-on-outer-cycle``,
        this test asserts the rule fires on the bundled recipes for the
        counters that lack a reset step on the audit-remediation NO-GO path.

        Before the fix this test asserted zero findings (the original
        existential-path BFS mistakenly accepted parallel guards' ``capture``
        as resets). The fix correctly excludes ``check_loop_iteration`` guards
        from the reset candidate list because their capture is an INCREMENT,
        not a reset — so parallel guards sharing a counter no longer
        masquerade as resets.

        The merged findings here represent latent defects in the bundled
        recipes (``merge_fix_count`` is shared by ``check_merge_fix_loop``,
        ``check_merge_rebase_loop``, ``check_dirty_main_retry`` with no
        ``init_counter`` reset on the audit-remediation NO-GO path). Part B
        will add the missing reset steps; once it lands this test will need
        updating to assert zero findings again.
        """
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]

        # Expected findings per recipe: counters shared by parallel guards with
        # no reset step on the audit-remediation NO-GO path.
        expected_inner_steps = {
            "remediation": {"check_merge_fix_loop", "check_dirty_main_retry"},
            "implementation": {
                "check_merge_fix_loop",
                "check_merge_rebase_loop",
                "check_dirty_main_retry",
            },
            "implementation-groups": {
                "check_merge_fix_loop",
                "check_merge_rebase_loop",
                "check_dirty_main_retry",
            },
        }
        expected = expected_inner_steps.get(recipe_name, set())

        actual = {f.step_name for f in rule_findings}
        assert expected <= actual, (
            f"{recipe_name}: expected at least {sorted(expected)}, "
            f"got {sorted(actual)}. Findings: "
            f"{[(f.step_name, f.message) for f in rule_findings]}"
        )

    def test_branching_reentry_reset_on_only_one_branch_fires(self) -> None:
        """Outer guard forks; only one branch crosses a reset — the other reaches
        inner_guard without a reset. The original existential-path BFS misses
        this because it accepts any reset reachable in the bilateral region.

        With the dominator fix, no single reset step dominates every path from
        non_exit_target (work) to inner_guard (branch_a skips the reset), so
        the rule must fire.
        """
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="work", exit_route="done"
            ),
            "work": RecipeStep(
                tool="run_skill",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="branch_a", when="context.flag == 'a'"),
                        StepResultCondition(route="branch_b"),
                    ]
                ),
                on_failure="done",
            ),
            "branch_a": RecipeStep(tool="run_skill", on_success="inner_guard", on_failure="done"),
            "branch_b": RecipeStep(tool="run_skill", on_success="reset_fix", on_failure="done"),
            "reset_fix": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.init_counter",
                    "counter_value": "",
                },
                capture={"fix_count": "${{ result.value }}"},
                on_success="inner_guard",
                on_failure="inner_guard",
            ),
            "inner_guard": _guard_step("fix_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="test", on_failure="done"),
            "test": RecipeStep(tool="test_check", on_success="outer_guard", on_failure="fix"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert len(rule_findings) == 1
        assert rule_findings[0].severity == Severity.ERROR
        assert rule_findings[0].step_name == "inner_guard"

    def test_branching_reentry_reset_on_all_branches_does_not_fire(self) -> None:
        """Outer guard forks; both branches reconverge at a SHARED reset before
        reaching inner_guard. The shared reset dominates inner_guard from the
        outer guard's non-exit target, so the rule must NOT fire.

        Mirrors the linear test_reset_present_does_not_fire topology but
        exercises the fork-join structure explicitly.
        """
        steps = {
            "outer_guard": _audit_outer_guard(
                "audit_remediation_count", non_exit="work", exit_route="done"
            ),
            "work": RecipeStep(
                tool="run_skill",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="branch_a", when="context.flag == 'a'"),
                        StepResultCondition(route="branch_b"),
                    ]
                ),
                on_failure="done",
            ),
            "branch_a": RecipeStep(tool="run_skill", on_success="reset_fix", on_failure="done"),
            "branch_b": RecipeStep(tool="run_skill", on_success="reset_fix", on_failure="done"),
            "reset_fix": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.init_counter",
                    "counter_value": "",
                },
                capture={"fix_count": "${{ result.value }}"},
                on_success="inner_guard",
                on_failure="inner_guard",
            ),
            "inner_guard": _guard_step("fix_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="test", on_failure="done"),
            "test": RecipeStep(tool="test_check", on_success="outer_guard", on_failure="fix"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [f for f in findings if f.rule == "loop-counter-not-reset-on-outer-cycle"]
        assert rule_findings == []


# ---------------------------------------------------------------------------
# shared-counter-cross-site-without-push-symmetry
#
# These fixtures exercise the rule that catches the #4274 defect shape:
# two check_loop_iteration guards share a counter, each is preceded by a
# merge_worktree step, and those merge steps disagree on whether their
# unconditional success route reaches a push_to_remote step.
# ---------------------------------------------------------------------------


def _merge_worktree_step(*, push_route: str | None, guard_route: str) -> RecipeStep:
    """A merge_worktree step with two on_result conditions: one to a guard,
    one (default) to either a push_to_remote step (push_route) or a
    non-push work step (push_route=None → routes to 'remediate')."""
    conditions = [
        StepResultCondition(when="result.failed_step == 'ref_coherence'", route=guard_route),
    ]
    if push_route is not None:
        conditions.append(StepResultCondition(route=push_route))
    else:
        conditions.append(StepResultCondition(route="remediate"))
    return RecipeStep(
        tool="merge_worktree",
        on_result=StepResultRoute(conditions=conditions),
        on_success="done",
    )


def _push_to_remote_step(next_step: str) -> RecipeStep:
    return RecipeStep(tool="push_to_remote", on_success=next_step, on_failure="done")


class TestSharedCounterCrossSiteWithoutPushSymmetry:
    def test_asymmetric_push_protection_fires(self) -> None:
        """merge_a's success routes to push_to_remote; merge_b's success routes
        to remediate (no push). Both guards share ``shared_count``. Rule must fire.
        """
        steps = {
            "merge_a": _merge_worktree_step(push_route="push_a", guard_route="guard_a"),
            "push_a": _push_to_remote_step("done"),
            "merge_b": _merge_worktree_step(push_route=None, guard_route="guard_b"),
            "guard_a": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="done", on_failure="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert len(rule_findings) == 1
        assert rule_findings[0].severity == Severity.ERROR
        # Finding must name both guard sites
        assert "guard_a" in rule_findings[0].message
        assert "guard_b" in rule_findings[0].message

    def test_symmetric_push_protection_does_not_fire(self) -> None:
        """Both merge sites' success routes reach a push_to_remote step. Rule
        must NOT fire — push symmetry is preserved across both guards.
        """
        steps = {
            "merge_a": _merge_worktree_step(push_route="push_a", guard_route="guard_a"),
            "push_a": _push_to_remote_step("done"),
            "merge_b": _merge_worktree_step(push_route="push_b", guard_route="guard_b"),
            "push_b": _push_to_remote_step("done"),
            "guard_a": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="done", on_failure="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert rule_findings == []

    def test_single_guard_site_does_not_fire(self) -> None:
        """Only one check_loop_iteration guard uses the counter — nothing to
        compare. Rule must NOT fire.
        """
        steps = {
            "merge_a": _merge_worktree_step(push_route="push_a", guard_route="guard_a"),
            "push_a": _push_to_remote_step("done"),
            "guard_a": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="done", on_failure="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert rule_findings == []

    def test_multi_hop_push_reachable_does_not_fire(self) -> None:
        """merge_a's push is reachable two hops downstream (through an
        intermediate non-branching step); merge_b's push is immediate. Both
        are genuinely push-protected — the rule must not fire on hop-distance
        alone.
        """
        steps = {
            "merge_a": _merge_worktree_step(push_route="hop1", guard_route="guard_a"),
            "hop1": RecipeStep(tool="run_skill", on_success="push_a", on_failure="done"),
            "push_a": _push_to_remote_step("done"),
            "merge_b": _merge_worktree_step(push_route="push_b", guard_route="guard_b"),
            "push_b": _push_to_remote_step("done"),
            "guard_a": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="done", on_failure="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert rule_findings == []

    def test_push_beyond_another_merge_worktree_not_credited(self) -> None:
        """merge_a's unconditional route reaches another merge_worktree step
        (merge_c) before any push_to_remote step; merge_c's own downstream
        push must not be credited to merge_a. merge_b pushes immediately.
        Rule must fire (asymmetric — merge_a has no push of its own before
        crossing merge_c).
        """
        steps = {
            "merge_a": _merge_worktree_step(push_route="merge_c", guard_route="guard_a"),
            "merge_c": _merge_worktree_step(push_route="push_c", guard_route="guard_c"),
            "push_c": _push_to_remote_step("done"),
            "guard_c": _guard_step("other_count", non_exit="fix", exit_route="done"),
            "merge_b": _merge_worktree_step(push_route="push_b", guard_route="guard_b"),
            "push_b": _push_to_remote_step("done"),
            "guard_a": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "guard_b": _guard_step("shared_count", non_exit="fix", exit_route="done"),
            "fix": RecipeStep(tool="run_skill", on_success="done", on_failure="done"),
            "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
        }
        recipe = _make_recipe(steps)
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert len(rule_findings) == 1
        assert "guard_a" in rule_findings[0].message
        assert "guard_b" in rule_findings[0].message

    def test_bundled_remediation_yaml_does_not_fire(self) -> None:
        """Issue #4274 regression: after Part B, ``remediation.yaml`` must NOT
        fire ``shared-counter-cross-site-without-push-symmetry``.

        The original failure had two guards sharing ``ref_push_count`` with
        asymmetric push protection across their merge_worktree predecessors
        (``merge`` had immediate push, ``pre_remediation_merge`` did not). Part B
        separates the counters and adds the missing push step, so the rule must
        no longer fire on the bundled recipe. This is the green-gate invariant
        for the Part B fix.
        """
        recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
        findings = run_semantic_rules(recipe)
        rule_findings = [
            f for f in findings if f.rule == "shared-counter-cross-site-without-push-symmetry"
        ]
        assert rule_findings == [], (
            f"remediation.yaml must NOT fire shared-counter-cross-site-without-push-symmetry "
            f"after Part B's counter split and missing-push fix; got findings: "
            f"{[(f.rule, f.message) for f in rule_findings]}"
        )

    def test_ref_push_loop_sites_use_independent_counters(self) -> None:
        """The two ref-push guard sites must use distinct counter variables.

        Issue #4274 root cause #2: ``check_ref_push_loop`` and
        ``check_ref_push_loop_pre_remediation`` both read from
        ``context.ref_push_count``, so a recovery at one site silently
        drains the other's budget. The fix separates the pre-remediation
        site to ``context.pre_remediation_ref_push_count`` — a distinct
        counter — restoring the codebase convention of one counter per
        guard site.
        """
        import re

        recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")

        ctx_var_re = re.compile(r"\$\{\{\s*context\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

        def _counter_var(step_name: str) -> str:
            step = recipe.steps[step_name]
            current_iteration = (
                step.with_args.get("current_iteration", "") if step.with_args else ""
            )
            match = ctx_var_re.search(current_iteration)
            assert match is not None, (
                f"{step_name} must declare current_iteration referencing a context counter; "
                f"got: {current_iteration!r}"
            )
            return match.group(1)

        final_counter = _counter_var("check_ref_push_loop")
        pre_remediation_counter = _counter_var("check_ref_push_loop_pre_remediation")

        assert final_counter == "ref_push_count", (
            f"check_ref_push_loop must continue to use ref_push_count; got {final_counter!r}"
        )
        assert pre_remediation_counter == "pre_remediation_ref_push_count", (
            f"check_ref_push_loop_pre_remediation must use the dedicated "
            f"pre_remediation_ref_push_count; got {pre_remediation_counter!r}"
        )
        assert final_counter != pre_remediation_counter, (
            f"the two ref-push guard sites must use independent counters; "
            f"both share {final_counter!r} — this is the shared-counter bug"
        )
