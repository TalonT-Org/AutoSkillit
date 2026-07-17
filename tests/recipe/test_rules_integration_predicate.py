from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe._analysis_bfs import (
    _bfs_capped,
    _build_success_step_graph,
    all_paths_cross,
)
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _get_routing_targets(step) -> set[str]:
    targets: set[str] = set()
    if step.on_success:
        targets.add(step.on_success)
    if step.on_failure:
        targets.add(step.on_failure)
    if step.on_result:
        for cond in step.on_result.conditions:
            if cond.route:
                targets.add(cond.route)
    return targets


class TestRecipeIntegrationPredicateRouting:
    """Integration tests: bundled recipes with predicate on_result validate correctly."""

    @pytest.fixture(scope="class", autouse=True)
    def _load_recipes(self, request) -> None:
        request.cls.if_recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
        request.cls.ip_recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
        request.cls.ig_recipe = load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")

    def test_investigate_first_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in remediation.yaml has predicate on_result."""
        step = self.if_recipe.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 9

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "check_merge_fix_loop"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "release_issue_failure"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "release_issue_failure"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "release_issue_failure"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.failed_step == 'dirty_main_repo'"
        assert cond4.route == "check_dirty_main_retry"

        cond5 = step.on_result.conditions[5]
        assert (
            cond5.when
            == "result.failed_step == 'ref_coherence' and result.remote_is_ancestor == true"
        )
        assert cond5.route == "check_ref_push_loop"

        cond6 = step.on_result.conditions[6]
        assert cond6.when == "result.failed_step == 'ref_coherence'"
        assert cond6.route == "release_issue_failure"

        cond7 = step.on_result.conditions[7]
        assert cond7.when == "result.error"
        assert cond7.route == "release_issue_failure"

        cond8 = step.on_result.conditions[8]
        assert cond8.when is None
        assert cond8.route == "inter_part_push"

    def test_investigate_first_merge_step_captures_worktree_path(self) -> None:
        """The merge step captures worktree_path from result.worktree_path."""
        step = self.if_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"].from_

    def test_implementation_pipeline_merge_step_has_predicate_on_result(self) -> None:
        """The merge step in implementation.yaml has predicate on_result."""
        step = self.ip_recipe.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 9

        cond0 = step.on_result.conditions[0]
        assert cond0.when == "result.failed_step == 'dirty_tree'"
        assert cond0.route == "check_merge_fix_loop"

        cond1 = step.on_result.conditions[1]
        assert cond1.when == "result.failed_step == 'test_gate'"
        assert cond1.route == "check_merge_fix_loop"

        cond2 = step.on_result.conditions[2]
        assert cond2.when == "result.failed_step == 'post_rebase_test_gate'"
        assert cond2.route == "check_merge_fix_loop"

        cond3 = step.on_result.conditions[3]
        assert cond3.when == "result.failed_step == 'rebase'"
        assert cond3.route == "check_merge_rebase_loop"

        cond4 = step.on_result.conditions[4]
        assert cond4.when == "result.failed_step == 'dirty_main_repo'"
        assert cond4.route == "check_dirty_main_retry"

        cond5 = step.on_result.conditions[5]
        assert (
            cond5.when
            == "result.failed_step == 'ref_coherence' and result.remote_is_ancestor == true"
        )
        assert cond5.route == "check_ref_push_loop"

        cond6 = step.on_result.conditions[6]
        assert cond6.when == "result.failed_step == 'ref_coherence'"
        assert cond6.route == "release_issue_failure"

        cond7 = step.on_result.conditions[7]
        assert cond7.when == "result.error"
        assert cond7.route == "release_issue_failure"

        cond8 = step.on_result.conditions[8]
        assert cond8.when is None
        assert cond8.route == "inter_part_push"

    def test_implementation_pipeline_merge_step_captures_worktree_path(self) -> None:
        """The merge step in implementation.yaml captures worktree_path."""
        step = self.ip_recipe.steps["merge"]
        assert "worktree_path" in step.capture
        assert "result.worktree_path" in step.capture["worktree_path"].from_

    def test_both_recipes_validate_cleanly(self) -> None:
        """Both recipes have no structural errors after predicate routing changes."""

        from autoskillit.recipe.validator import validate_recipe_structure

        if_errors = validate_recipe_structure(self.if_recipe)
        assert if_errors == [], f"remediation.yaml has validation errors: {if_errors}"

        ip_errors = validate_recipe_structure(self.ip_recipe)
        assert ip_errors == [], f"implementation.yaml has validation errors: {ip_errors}"

    def test_all_recipes_no_error_semantic_findings(self) -> None:
        """All bundled implementation-family recipes have no ERROR-severity findings."""
        for recipe, name in [
            (self.if_recipe, "remediation"),
            (self.ip_recipe, "implementation"),
            (self.ig_recipe, "implementation-groups"),
        ]:
            findings = run_semantic_rules(recipe)
            errors = [f for f in findings if f.severity == Severity.ERROR]
            assert errors == [], f"{name} has ERROR-severity semantic findings: " + str(
                [(f.rule, f.step_name, f.message) for f in errors]
            )


class TestPreRemediationMergePredicateRouting:
    @pytest.fixture(scope="class", autouse=True)
    def _load_recipe(self, request) -> None:
        request.cls.recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")

    def test_ref_coherence_routes_by_ancestry_before_fallback(self) -> None:
        step = self.recipe.steps["pre_remediation_merge"]
        assert step.on_result is not None

        ancestry_indexes = [
            index
            for index, condition in enumerate(step.on_result.conditions)
            if condition.when
            and "ref_coherence" in condition.when
            and "remote_is_ancestor" in condition.when
        ]
        fallback_indexes = [
            index
            for index, condition in enumerate(step.on_result.conditions)
            if condition.when
            and "ref_coherence" in condition.when
            and "remote_is_ancestor" not in condition.when
        ]

        assert len(ancestry_indexes) == 1
        assert len(fallback_indexes) == 1
        ancestry_index = ancestry_indexes[0]
        fallback_index = fallback_indexes[0]
        assert ancestry_index < fallback_index
        assert (
            step.on_result.conditions[ancestry_index].route
            == "check_ref_push_loop_pre_remediation"
        )
        assert step.on_result.conditions[fallback_index].route == "release_issue_failure"

    def test_ref_push_returns_to_pre_remediation_guard(self) -> None:
        assert (
            self.recipe.steps["ref_push_pre_remediation"].on_success
            == "commit_guard_pre_remediation"
        )


class TestLoopBudgetSeparation:
    """Budget separation: merge-fix and audit-remediation use independent counters."""

    RECIPE_NAMES = ["remediation", "implementation", "implementation-groups"]

    @pytest.fixture(scope="class", autouse=True)
    def _load_recipes(self, request) -> None:
        request.cls.recipes = {
            name: load_recipe(builtin_recipes_dir() / f"{name}.yaml")
            for name in TestLoopBudgetSeparation.RECIPE_NAMES
        }

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_test_step_bypasses_merge_fix_guard(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        assert recipe.steps["test"].on_success != "check_merge_fix_loop"

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_audit_remediation_loop_exists_and_wired(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        step = recipe.steps["check_audit_remediation_loop"]
        assert step.tool == "run_python"
        assert step.with_args["callable"] == "autoskillit.smoke_utils.check_loop_iteration"
        assert "audit_remediation_count" in step.capture
        exceeded = [c for c in step.on_result.conditions if c.when and "max_exceeded" in c.when]
        assert any(c.route == "release_issue_failure" for c in exceeded)

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_audit_impl_no_go_routes_to_audit_loop(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        audit_step = recipe.steps["audit_impl"]
        fallthrough = [
            c.route
            for c in audit_step.on_result.conditions
            if c.when is None or ("GO" not in c.when and "error" not in c.when)
        ]
        assert fallthrough == ["check_audit_remediation_loop"]

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_all_merge_failure_arms_guarded(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        merge_step = recipe.steps["merge"]
        merge_fix_guard_steps = {
            "check_merge_fix_loop",
            "check_merge_rebase_loop",
            "check_dirty_main_retry",
        }
        guard_steps = merge_fix_guard_steps | {"check_ref_push_loop"}
        terminal_steps = {"release_issue_failure"}
        for cond in merge_step.on_result.conditions:
            if not cond.when or "failed_step" not in cond.when:
                continue
            if "ref_coherence" in cond.when and "remote_is_ancestor" not in cond.when:
                assert cond.route == "release_issue_failure"
            elif recipe_name == "remediation" and cond.when in (
                "result.failed_step == 'test_gate'",
                "result.failed_step == 'post_rebase_test_gate'",
                "result.failed_step == 'rebase'",
            ):
                # After PART B step 5, remediation.yaml's pre_remediation_merge
                # routes these to terminal escalation so live-worktree merge
                # failures no longer orphan the next worktree creator.
                assert cond.route in terminal_steps, (
                    f"{cond.when} routes to {cond.route}, expected a terminal escalation"
                )
            else:
                assert cond.route in guard_steps, (
                    f"{cond.when} routes to {cond.route}, expected a guard step"
                )
        for name in merge_fix_guard_steps:
            if name in recipe.steps:
                step = recipe.steps[name]
                assert step.with_args.get("current_iteration") == "${{ context.merge_fix_count }}"

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_loop_budget_ingredients_exist(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        assert "merge_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["merge_fix_max_retries"].default == "3"
        assert "audit_remediation_max_retries" in recipe.ingredients
        assert recipe.ingredients["audit_remediation_max_retries"].default == "3"
        assert "test_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["test_fix_max_retries"].default == "3"
        assert recipe.ingredients["test_fix_max_retries"].hidden is True
        assert "merge_test_fix_max_retries" in recipe.ingredients
        assert recipe.ingredients["merge_test_fix_max_retries"].default == "3"
        assert recipe.ingredients["merge_test_fix_max_retries"].hidden is True

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_audit_remediation_description_documents_arithmetic(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        desc = recipe.ingredients["audit_remediation_max_retries"].description
        assert "value − 1" in desc or "value - 1" in desc, (
            f"audit_remediation_max_retries description must document arithmetic: {desc!r}"
        )

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_guard_steps_use_ingredients(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        for name in (
            "check_merge_fix_loop",
            "check_merge_rebase_loop",
            "check_dirty_main_retry",
        ):
            step = recipe.steps[name]
            assert step.with_args["max_iterations"] == "${{ inputs.merge_fix_max_retries }}"
        audit_guard = recipe.steps["check_audit_remediation_loop"]
        assert (
            audit_guard.with_args["max_iterations"]
            == "${{ inputs.audit_remediation_max_retries }}"
        )
        test_fix_guard = recipe.steps["check_test_fix_loop"]
        assert test_fix_guard.with_args["max_iterations"] == "${{ inputs.test_fix_max_retries }}"
        merge_test_fix_guard = recipe.steps["check_merge_test_fix_loop"]
        assert (
            merge_test_fix_guard.with_args["max_iterations"]
            == "${{ inputs.merge_test_fix_max_retries }}"
        )

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_merge_test_fix_loop_uses_separate_counter(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        step = recipe.steps["check_merge_test_fix_loop"]
        assert step.with_args["current_iteration"] == "${{ context.merge_test_fix_loop_count }}"
        assert "merge_test_fix_loop_count" in step.capture

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_test_fix_loop_not_reachable_from_merge_gate_path(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        steps = recipe.steps
        merge_guard = steps["check_merge_test_fix_loop"]
        merge_guard_targets = _get_routing_targets(merge_guard)
        assert "test" not in merge_guard_targets, (
            f"check_merge_test_fix_loop routes to shared 'test': {merge_guard_targets}"
        )
        assert "merge_gate_test" in steps, "merge_gate_test step missing"
        mgt = steps["merge_gate_test"]
        assert mgt.on_failure != "check_test_fix_loop", (
            "merge_gate_test.on_failure must not be check_test_fix_loop"
        )

    @pytest.mark.parametrize("recipe_name", RECIPE_NAMES)
    def test_merge_gate_assess_routes_through_merge_fix_guard(self, recipe_name: str) -> None:
        recipe = self.recipes[recipe_name]
        if "merge_gate_assess" not in recipe.steps:
            pytest.skip(f"{recipe_name}: no merge_gate_assess step")
        targets = _get_routing_targets(recipe.steps["merge_gate_assess"])
        assert "merge_gate_test" in targets, (
            f"merge_gate_assess does not route to merge_gate_test; targets={targets}"
        )
        assert "check_test_fix_loop" not in targets, (
            f"merge_gate_assess must not route to check_test_fix_loop; targets={targets}"
        )
        mgt_failure = recipe.steps["merge_gate_test"].on_failure
        assert mgt_failure == "check_merge_test_fix_loop", (
            f"merge_gate_test.on_failure should be check_merge_test_fix_loop, got {mgt_failure}"
        )


def _has_counter_reset_on_path(recipe, start: str, end: str, counter: str) -> bool:
    """Walk from start toward end and check if any intermediate step captures ``counter``."""
    graph = _build_success_step_graph(recipe)
    visited = _bfs_capped(graph, {start}, {end})
    for sn in visited:
        if sn == start or sn == end:
            continue
        step = recipe.steps.get(sn)
        if step and counter in step.capture:
            return True
    return False


def test_test_fix_loop_count_resets_on_remediation_cycle_implementation() -> None:
    """implementation.yaml must reset test_fix_loop_count between audit cycles."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    assert _has_counter_reset_on_path(
        recipe, "check_audit_remediation_loop", "remediate", "test_fix_loop_count"
    ), "implementation.yaml must reset test_fix_loop_count between audit-remediation cycles"


def test_test_fix_loop_count_resets_in_remediation_recipe() -> None:
    """remediation.yaml must reset test_fix_loop_count between audit cycles."""
    recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
    assert _has_counter_reset_on_path(
        recipe, "check_audit_remediation_loop", "pre_remediation_merge", "test_fix_loop_count"
    ), "remediation.yaml must reset test_fix_loop_count between audit-remediation cycles"


def test_test_fix_loop_count_resets_in_implementation_groups_recipe() -> None:
    """implementation-groups.yaml must reset test_fix_loop_count between audit cycles."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")
    assert _has_counter_reset_on_path(
        recipe, "check_audit_remediation_loop", "remediate", "test_fix_loop_count"
    ), "implementation-groups.yaml must reset test_fix_loop_count between audit-remediation cycles"


# ---------------------------------------------------------------------------
# Ref-push counter reset coverage — issue #4274 regression guards.
#
# The pre-remediation ref-push guard (``check_ref_push_loop_pre_remediation``)
# must have its counter reset on the NO-GO re-entry path. Cross-part
# coordination: this test reads the counter variable directly from the loaded
# recipe so it survives Part B's counter separation (the counter name may
# change from ``ref_push_count`` to ``pre_remediation_ref_push_count``).
# ---------------------------------------------------------------------------


def _extract_check_loop_iteration_counter(step) -> str | None:
    """Return the counter variable name from a check_loop_iteration step, or None."""
    if step.tool != "run_python":
        return None
    if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
        return None
    current_iter_expr = step.with_args.get("current_iteration", "")
    import regex as re

    m = re.search(r"\$\{\{\s*context\.(\w+)\s*\}\}", current_iter_expr)
    return m.group(1) if m else None


@pytest.mark.parametrize("recipe_name", ["remediation", "implementation", "implementation-groups"])
def test_ref_push_counter_reset_on_no_go_path(recipe_name: str) -> None:
    """The ref-push pre-remediation guard's counter is reset on the NO-GO path.

    For ``remediation.yaml``: the pre-remediation guard
    (``check_ref_push_loop_pre_remediation``) sits downstream of the audit-
    remediation cycle's NO-GO route, so its counter MUST be reset by
    ``reset_ref_push_counter`` on every path from the audit loop's non-exit
    target to the guard. ``all_paths_cross`` enforces this universal-path
    contract (Part B may split the counter variable; the test reads the name
    dynamically so it survives that change).

    For ``implementation.yaml`` and ``implementation-groups.yaml``: the pre-
    remediation guard step is NOT in those recipes, so the test only asserts
    the structural reset invariant for remediation.yaml.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")

    pre_remediation_guard = "check_ref_push_loop_pre_remediation"
    if pre_remediation_guard not in recipe.steps:
        pytest.skip(f"{recipe_name} has no {pre_remediation_guard} step")

    guard_step = recipe.steps[pre_remediation_guard]
    counter_var = _extract_check_loop_iteration_counter(guard_step)
    assert counter_var is not None, (
        f"{pre_remediation_guard} does not parse as a check_loop_iteration guard"
    )

    # reset_ref_push_counter must capture the same counter variable
    reset_step = recipe.steps.get("reset_ref_push_counter")
    assert reset_step is not None, f"{recipe_name} must have a reset_ref_push_counter step"
    assert counter_var in reset_step.capture, (
        f"reset_ref_push_counter must capture {counter_var!r}, got {list(reset_step.capture)}"
    )

    # The audit-remediation loop's non-max_exceeded route feeds the pre-
    # remediation cycle. reset_ref_push_counter must dominate the pre-
    # remediation guard from that starting point.
    audit_loop = recipe.steps["check_audit_remediation_loop"]
    non_exit_target: str | None = None
    if audit_loop.on_result:
        for cond in audit_loop.on_result.conditions:
            if cond.when and "max_exceeded" in cond.when:
                continue
            non_exit_target = cond.route
            break
    assert non_exit_target is not None, (
        "check_audit_remediation_loop must declare a non-max_exceeded route"
    )

    graph = recipe.steps and _build_success_step_graph(recipe)
    dominated = all_paths_cross(
        graph, non_exit_target, "reset_ref_push_counter", pre_remediation_guard
    )
    assert dominated, (
        f"{recipe_name}: reset_ref_push_counter must dominate "
        f"{pre_remediation_guard} from {non_exit_target}"
    )

    # Structural immunity confirmation: the GO-path entry to check_ref_push_loop
    # (audit_impl's GO branch -> commit_guard -> main_repo_guard -> merge) must
    # not re-enter the pre-remediation cycle. Barrier at audit_impl so the
    # check stays scoped to this audit decision's own GO/NO-GO branches rather
    # than a subsequent plan part's independent audit cycle (confirmed the
    # only fork point on every path into check_audit_remediation_loop).
    go_path_entry = "commit_guard"
    reachable_without_new_audit = _bfs_capped(graph, {go_path_entry}, {"audit_impl"})
    assert pre_remediation_guard not in reachable_without_new_audit, (
        f"{recipe_name}: GO-path entry '{go_path_entry}' must not re-enter the "
        f"pre-remediation cycle ({pre_remediation_guard} reachable via "
        f"{reachable_without_new_audit})"
    )
