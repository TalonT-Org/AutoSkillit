"""Tests for structural assertions on pipeline recipe variants
(implementation, implementation-groups, remediation).

TestPipelineVariantInvariants covers the 16 tests that were copy-paste identical
across the three per-variant classes.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import analyze_dataflow, run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _assert_ci_conflict_fix_on_context_limit(recipe) -> None:
    """Shared assertion: ci_conflict_fix must abort via release_issue_failure on context limit."""
    step = recipe.steps["ci_conflict_fix"]
    assert step.on_context_limit == "release_issue_failure", (
        "ci_conflict_fix is advisory; an incomplete conflict fix cannot be safely "
        "pushed — abort via release_issue_failure"
    )


def _assert_ci_steps(recipe) -> None:
    """Assert shared CI step structure across all pipeline recipe variants.

    Covers T_CI1–T_CI6: ci_watch, resolve_ci, and re_push steps.
    Called from each recipe test class to eliminate triplication.
    """
    # ci_watch step (T_CI1, T_CI2, T_CI3)
    assert "ci_watch" in recipe.steps
    ci = recipe.steps["ci_watch"]
    assert ci.tool == "wait_for_ci"
    assert ci.skip_when_false == "inputs.open_pr"
    assert ci.with_args.get("timeout_seconds") == 600
    assert ci.on_result is not None
    result_routes = {c.route for c in ci.on_result.conditions}
    assert "check_repo_merge_state" in result_routes
    assert "handle_no_ci_runs" in result_routes
    assert ci.on_failure == "detect_ci_conflict"
    assert "release_issue_success" in recipe.steps
    assert "context.merge_target" in ci.with_args["branch"]
    assert "cmd" not in ci.with_args
    assert "ci_conclusion" in ci.capture
    assert "ci_failed_jobs" in ci.capture

    # resolve_ci step (T_CI4, T_CI5)
    assert "resolve_ci" in recipe.steps
    resolve_ci = recipe.steps["resolve_ci"]
    assert resolve_ci.tool == "run_skill"
    skill_cmd = resolve_ci.with_args.get("skill_command", "")
    assert "resolve-failures" in skill_cmd
    assert resolve_ci.retries == 2
    assert resolve_ci.on_exhausted == "release_issue_failure"
    assert "context.work_dir" in skill_cmd

    # re_push step (T_CI6)
    assert "re_push" in recipe.steps
    re_push = recipe.steps["re_push"]
    assert re_push.tool == "push_to_remote"
    assert re_push.on_success == "check_repo_ci_event"
    assert re_push.on_failure == "release_issue_failure"

    # check_ci_loop guard (loop bounding for ci_watch / handle_no_ci_runs cycle)
    assert "check_ci_loop" in recipe.steps, "ci_watch cycle must have check_ci_loop guard"
    guard = recipe.steps["check_ci_loop"]
    assert guard.tool == "run_python"
    assert "check_loop_iteration" in guard.with_args.get("callable", "")
    assert guard.on_result is not None
    guard_routes = {c.route for c in guard.on_result.conditions}
    assert "ci_watch" in guard_routes, "check_ci_loop must route back to ci_watch"
    assert "check_active_trigger_loop" in guard_routes, (
        "check_ci_loop must route to check_active_trigger_loop on budget exhaustion"
    )
    assert "check_active_trigger_loop" in recipe.steps, (
        "check_active_trigger_loop guard step missing"
    )
    trigger_guard = recipe.steps["check_active_trigger_loop"]
    trigger_guard_routes = {c.route for c in trigger_guard.on_result.conditions}
    assert "trigger_ci_actively" in trigger_guard_routes, (
        "check_active_trigger_loop must route to trigger_ci_actively"
    )
    assert "ci_loop_count" in guard.capture
    handle = recipe.steps["handle_no_ci_runs"]
    assert handle.on_success == "check_ci_loop", (
        "handle_no_ci_runs must route to check_ci_loop, not ci_watch"
    )


# Maps recipe name → expected on_context_limit value for the review step.
# review step routes to verify in the implementation variants (can skip to next step)
# but to dry_walkthrough in remediation (review is optional before dry run).
_REVIEW_STEP_OCL = {
    "implementation": "verify",
    "implementation-groups": "verify",
    "remediation": "dry_walkthrough",
}


class TestPipelineVariantInvariants:
    """Assertions that hold for all three pipeline variants.

    Each test runs once per variant (implementation / implementation-groups / remediation).
    Before this refactor, each assertion was copy-paste triplicated across
    TestImplementationPipelineStructure, TestImplementationGroupsStructure, and
    TestInvestigateFirstStructure.
    """

    @pytest.fixture(
        scope="class",
        params=["implementation", "implementation-groups", "remediation"],
        ids=lambda x: x,
    )
    def recipe(self, request: pytest.FixtureRequest):
        return load_recipe(builtin_recipes_dir() / f"{request.param}.yaml")

    def test_ci_step_structure(self, recipe) -> None:
        """T_CI1–T_CI6: shared ci_watch, resolve_ci, and re_push structure."""
        _assert_ci_steps(recipe)

    def test_re_push_has_explicit_remote_url(self, recipe) -> None:
        """T_CI7: re_push uses explicit remote_url."""
        with_args = recipe.steps["re_push"].with_args
        assert "remote_url" in with_args
        assert "context.remote_url" in with_args["remote_url"]

    def test_compose_pr_routes_to_extract_pr_number(self, recipe) -> None:
        """T_CI8: compose_pr.on_success routes to extract_pr_number before review_pr."""
        step = recipe.steps["compose_pr"]
        assert step.on_success == "extract_pr_number", "compose_pr must route to extract_pr_number"

    def test_detect_ci_conflict_exists(self, recipe) -> None:
        assert "detect_ci_conflict" in recipe.steps
        step = recipe.steps["detect_ci_conflict"]
        assert step.tool == "run_cmd"

    def test_detect_ci_conflict_uses_merge_base(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        cmd = (step.with_args or {}).get("cmd", "")
        assert "merge-base" in cmd or "is-ancestor" in cmd

    def test_detect_ci_conflict_routing(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.on_success == "ci_conflict_fix"
        assert step.on_failure == "diagnose_ci"

    def test_ci_conflict_fix_exists(self, recipe) -> None:
        assert "ci_conflict_fix" in recipe.steps
        step = recipe.steps["ci_conflict_fix"]
        assert step.tool == "run_skill"
        skill_cmd = (step.with_args or {}).get("skill_command", "")
        assert "resolve-merge-conflicts" in skill_cmd

    def test_ci_conflict_fix_routing(self, recipe) -> None:
        step = recipe.steps["ci_conflict_fix"]
        assert step.on_failure == "release_issue_failure"
        assert step.on_exhausted == "release_issue_failure"
        conditions = step.on_result.conditions if step.on_result else []
        routes = {c.when: c.route for c in conditions}
        assert any(
            "escalation_required" in (w or "") and r == "release_issue_failure"
            for w, r in routes.items()
        )
        default_routes = [r for w, r in routes.items() if w is None]
        assert default_routes == ["re_push"]

    def test_detect_ci_conflict_skip_when_false(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_ci_conflict_fix_skip_when_false(self, recipe) -> None:
        step = recipe.steps["ci_conflict_fix"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_review_step_has_skip_when_false(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.skip_when_false == "inputs.review_approach"

    def test_review_step_has_retries(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.retries >= 1

    def test_review_step_has_on_context_limit(self, recipe) -> None:
        """review step on_context_limit differs per variant (verify vs dry_walkthrough)."""
        step = recipe.steps["review"]
        expected = _REVIEW_STEP_OCL[recipe.name]
        assert step.on_context_limit == expected

    def test_audit_impl_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["audit_impl"]
        assert step.on_context_limit == "escalate_stop", (
            "audit_impl is a merge gate; a context-exhausted audit cannot provide "
            "a valid verdict — aborting via escalate_stop is correct"
        )

    def test_compose_pr_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["compose_pr"]
        assert step.on_context_limit == "release_issue_failure", (
            "compose_pr is advisory (skip_when_false); on context limit the pipeline "
            "cannot determine PR state — release the issue via release_issue_failure"
        )

    def test_ci_conflict_fix_has_on_context_limit(self, recipe) -> None:
        _assert_ci_conflict_fix_on_context_limit(recipe)


# ---------------------------------------------------------------------------
# TestImplementationPipelineStructure
# ---------------------------------------------------------------------------


# Negative-parity mirror of TestImplementationGroupsStructure: asserts that
# implementation.yaml uniquely owns features absent from implementation-groups.yaml,
# and vice versa.  Changes to one class should be reviewed alongside the other.
class TestImplementationPipelineStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation.yaml")

    def test_ip2_review_step_captures_review_path(self, recipe) -> None:
        """T_IP2: review step has capture containing key review_path."""
        assert "review_path" in recipe.steps["review"].capture

    def test_ip3_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
        recipe,
    ) -> None:
        """T_IP3: audit_impl captures remediation_path and routes via on_result using verdict.

        Uses predicate format (v0.3.0): verdict is read directly from result.verdict in predicate
        conditions — it is not captured as context.verdict (which would create a dead output).
        """
        step = recipe.steps["audit_impl"]
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        # Predicate format: conditions list (not legacy field+routes dict)
        conds = step.on_result.conditions
        assert len(conds) > 0, "audit_impl on_result must have predicate conditions"
        assert any("result.verdict" in (c.when or "") for c in conds), (
            "audit_impl on_result must have a condition checking result.verdict"
        )

    def test_ip4_verify_step_references_context_review_path(self, recipe) -> None:
        """T_IP4: verify step with_args contains a reference to context.review_path."""
        verify_with = recipe.steps["verify"].with_args
        assert any("context.review_path" in str(v) for v in verify_with.values())

    def test_ip5_audit_impl_has_on_failure(self, recipe) -> None:
        """T_IP5: audit_impl declares on_failure for tool-level failure routing.

        In the two-tier failure model, on_result.conditions fire when run_skill returns
        success=true with a result object. on_failure fires when run_skill returns
        success=false (tool-level failure, no result object). Both must be declared.
        """
        step = recipe.steps["audit_impl"]
        assert step.on_success is None  # on_result is used; on_success remains absent
        assert step.on_failure == "escalate_stop", (
            "audit_impl must declare on_failure: escalate_stop. "
            "Tool-level failures produce no result object — on_result conditions cannot fire."
        )

    def test_ip6_plan_step_note_contains_glob_pattern(self, recipe) -> None:
        """T_IP6: plan step note must contain *_part_*.md glob pattern for multi-part discovery."""
        note = recipe.steps["plan"].note or ""
        assert "*_part_*.md" in note, (
            "plan step note must contain glob pattern for multi-part discovery; "
            "if removed, agents will not discover part files"
        )

    def test_ip7_verify_step_note_sequential_constraint(self, recipe) -> None:
        """T_IP7: verify step note must contain sequential execution constraint."""
        note = recipe.steps["verify"].note or ""
        assert "SEQUENTIAL EXECUTION" in note or "full cycle" in note.lower(), (
            "verify step note must contain sequential constraint; "
            "without it agents may batch-verify all parts before implementing any"
        )

    def test_ip8_next_or_done_routes_more_parts_to_verify(self, recipe) -> None:
        """T_IP8: next_or_done routes more_parts back to verify for sequential processing.

        Uses predicate format (v0.3.0): when-condition checks result.next == more_parts.
        """
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert any(
            c.route == "verify" and c.when is not None and "more_parts" in c.when for c in conds
        ), "next_or_done must have a predicate routing more_parts → verify"

    def test_ip9_next_or_done_routes_all_done_to_audit_impl(self, recipe) -> None:
        """T_IP9: next_or_done must route all_done to audit_impl.

        Uses predicate format (v0.3.0): fallthrough condition (when=None) routes to audit_impl.
        """
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        # The fallthrough condition (when=None) is the default route to audit_impl
        assert any(c.route == "audit_impl" for c in conds), (
            "next_or_done must have a condition routing to audit_impl"
        )

    def test_ip_audit_impl_uses_base_sha_as_ref(self, recipe) -> None:
        """T_IP_B2: audit_impl must use context.base_sha (not context.branch_name).

        branch_name is deleted by git branch -D inside merge_worktree. A commit SHA
        names a git object and survives unconditionally.
        """
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.base_sha" in skill_cmd, (
            "audit_impl must use context.base_sha as implementation_ref — "
            "context.branch_name is deleted by merge_worktree before audit_impl runs"
        )
        assert "context.branch_name" not in skill_cmd, (
            "audit_impl must NOT use context.branch_name (deleted by merge_worktree)"
        )

    def test_ip_c1_fix_step_routes_via_on_result_to_test(self, recipe) -> None:
        """T_IP_C1: fix step must route via verdict-gated on_result back to test.

        After Part B, fix uses on_result: verdict dispatch instead of unconditional
        on_success: test. real_fix and already_green verdicts route to test for
        re-validation before entering merge_worktree.
        """
        step = recipe.steps["fix"]
        assert step.on_success is None, (
            "fix step must use on_result: verdict dispatch, not unconditional on_success"
        )
        assert step.on_result is not None, "fix step must have on_result: verdict dispatch"
        test_routes = [
            c.route for c in step.on_result.conditions if c.when and "real_fix" in c.when
        ]
        assert any(r == "test" for r in test_routes), (
            "fix step on_result must route verdict=real_fix to test for re-validation"
        )

    def test_ip_base_sha_captured_before_implement(self, recipe) -> None:
        """A1: base_sha must be captured by a step that precedes the implement loop."""
        steps = list(recipe.steps.items())
        step_names = [name for name, _ in steps]

        # Find step that captures base_sha
        sha_step = next(
            (name for name, step in steps if step.capture and "base_sha" in step.capture),
            None,
        )
        assert sha_step is not None, "No step captures base_sha"

        implement_idx = next(
            (
                i
                for i, (name, step) in enumerate(steps)
                if step.tool in {"run_skill"}
                and "implement-worktree" in step.with_args.get("skill_command", "")
            ),
            None,
        )
        assert implement_idx is not None, "No implement step found"
        sha_idx = step_names.index(sha_step)
        assert sha_idx < implement_idx, (
            f"base_sha capture step '{sha_step}' (idx {sha_idx}) must come before "
            f"implement step (idx {implement_idx})"
        )

    def test_ip_merge_target_unconditionally_set(self, recipe) -> None:
        """A3: merge_target must be captured by a non-optional step before merge/push."""
        steps = list(recipe.steps.items())
        step_names = [name for name, _ in steps]

        merge_idx = step_names.index("merge") if "merge" in step_names else None
        assert merge_idx is not None, "merge step not found"

        # Find any non-optional step that captures merge_target before merge
        unconditional_capture = next(
            (
                name
                for name, step in steps[:merge_idx]
                if step.capture and "merge_target" in step.capture and not step.optional
            ),
            None,
        )
        assert unconditional_capture is not None, (
            "merge_target must be captured by a non-optional step before the merge step. "
            "Currently it is only captured by the optional create_branch step, leaving it "
            "undefined in direct mode (open_pr=false)."
        )

    def test_ip_base_sha_capture_uses_work_dir(self, recipe) -> None:
        """A4: base_sha must be captured by the bootstrap_clone step."""
        sha_step = next(
            (
                step
                for name, step in recipe.steps.items()
                if step.capture and "base_sha" in step.capture
            ),
            None,
        )
        assert sha_step is not None, "No step captures base_sha"
        assert sha_step.tool == "bootstrap_clone", (
            "base_sha must be captured by bootstrap_clone (which runs git rev-parse inside the "
            "clone directory internally)"
        )

    def test_ip_base_sha_used_by_audit_impl(self, recipe) -> None:
        """A5: base_sha captured must be consumed — specifically by audit_impl."""
        report = analyze_dataflow(recipe)
        dead = {w.field for w in report.warnings if w.code == "DEAD_OUTPUT"}
        assert "base_sha" not in dead, (
            "base_sha is captured but never consumed — audit_impl must reference it"
        )

    def test_ip_push_after_audit_warning_fires(self, recipe) -> None:
        """T_IP_PBA: after Part B, audit_impl has skip_when_false so push-before-audit
        fires as a WARNING. This is correct and expected — the user can opt out of audit
        (audit=false), and the rule signals that push is reachable without audit on that path.
        """
        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1, (
            "push-before-audit must fire: audit_impl has skip_when_false so push is "
            "reachable via the audit=false bypass path"
        )
        assert all(v.severity == Severity.WARNING for v in violations)

    def test_ip_compose_pr_has_skip_when_false(self, recipe) -> None:
        """compose_pr must declare skip_when_false: inputs.open_pr."""
        compose_pr = recipe.steps["compose_pr"]
        assert compose_pr.skip_when_false == "inputs.open_pr"

    def test_ip_audit_impl_has_skip_when_false(self, recipe) -> None:
        """audit_impl must declare skip_when_false: inputs.audit."""
        audit_step = recipe.steps["audit_impl"]
        assert audit_step.skip_when_false == "inputs.audit"

    def test_ip_create_branch_has_skip_when_false(self, recipe) -> None:
        """create_and_publish must declare skip_when_false: inputs.open_pr."""
        create_and_publish = recipe.steps["create_and_publish"]
        assert create_and_publish.skip_when_false == "inputs.open_pr"

    def test_create_branch_uses_callable(self, recipe) -> None:
        """create_and_publish must use create_and_publish_branch MCP tool."""
        step = recipe.steps["create_and_publish"]
        assert step.tool == "create_and_publish_branch"

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_and_publish uses create_and_publish_branch (which checks ls-remote)."""
        assert recipe.steps["create_and_publish"].tool == "create_and_publish_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """create_and_publish with_args must include issue_number for branch naming."""
        assert "issue_number" in recipe.steps["create_and_publish"].with_args

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """create_and_publish with_args must include run_name for branch naming."""
        assert "run_name" in recipe.steps["create_and_publish"].with_args

    def test_ip_main_push_step_not_reachable_after_compose_pr(self, recipe) -> None:
        """The main `push` step must not be reachable after compose_pr —
        that would be a double-push. The new `re_push` step IS reachable and is correct."""
        from autoskillit.recipe.validator import _build_step_graph

        graph = _build_step_graph(recipe)
        visited: set[str] = set()
        queue = [recipe.steps["compose_pr"].on_success]
        while queue:
            current = queue.pop(0)
            if current in visited or current not in recipe.steps:
                continue
            visited.add(current)
            queue.extend(graph.get(current, []))
        assert "push" not in visited, (
            "'push' step is reachable after compose_pr — double-push risk. "
            "(re_push is allowed; push is not)"
        )

    def test_ip_open_pr_false_path_reaches_push_then_cleanup(self, recipe) -> None:
        """When compose_pr is bypassed (open_pr=false), execution must go:
        audit_impl (GO) → push → [compose_pr bypassed] → cleanup_success → done.
        After the fix, audit_impl's GO route points directly to push, so push is
        always reachable from audit_impl's successors."""
        from autoskillit.recipe.validator import _build_step_graph

        graph = _build_step_graph(recipe)
        # After the fix: audit_impl.on_result.GO → push (directly).
        # Verify push is reachable from audit_impl's successors.
        reachable: set[str] = set()
        queue = list(graph.get("audit_impl", []))
        while queue:
            node = queue.pop(0)
            if node in reachable or node not in recipe.steps:
                continue
            reachable.add(node)
            queue.extend(graph.get(node, []))
        assert "push" in reachable

    def test_ip_plan_step_captures_all_plan_paths(self, recipe) -> None:
        """plan step must capture all_plan_paths for multi-group accumulation."""
        assert "all_plan_paths" in recipe.steps["plan"].capture
        assert "result.plan_path" in recipe.steps["plan"].capture["all_plan_paths"]

    def test_ip_prepare_pr_references_all_plan_paths(self, recipe) -> None:
        """prepare_pr must pass all accumulated plan paths, not just the last."""
        cmd = recipe.steps["prepare_pr"].with_args.get("skill_command", "")
        assert "context.all_plan_paths" in cmd
        assert "context.plan_path" not in cmd

    def test_ip_plan_step_note_contains_accumulation_instruction(self, recipe) -> None:
        """plan step note must instruct agent to accumulate plan paths across groups."""
        note = recipe.steps["plan"].note or ""
        assert "ACCUMULATION" in note
        assert "all_plan_paths" in note

    def test_ip_no_group_step(self, recipe) -> None:
        """implementation.yaml must not contain a group step."""
        assert "group" not in recipe.steps

    def test_ip_task_ingredient_required(self, recipe) -> None:
        """task ingredient must be required in the direct recipe."""
        task_ing = recipe.ingredients.get("task")
        assert task_ing is not None
        assert task_ing.required is True or task_ing.default is None

    def test_ip_no_make_groups_ingredient(self, recipe) -> None:
        """make_groups ingredient must not be present."""
        assert "make_groups" not in recipe.ingredients

    def test_ip_no_source_doc_ingredient(self, recipe) -> None:
        """source_doc ingredient must not be present."""
        assert "source_doc" not in recipe.ingredients

    def test_ip_next_or_done_no_more_groups_route(self, recipe) -> None:
        """next_or_done must not route more_groups — no groups in the direct recipe."""
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert not any("more_groups" in (c.when or "") for c in conds)


# ---------------------------------------------------------------------------
# TestImplementationGroupsStructure
# ---------------------------------------------------------------------------


class TestImplementationGroupsStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")

    def test_ig1_group_step_captures_group_files(self, recipe) -> None:
        """T_IG1: group step captures group_files, not groups_path."""
        assert "group_files" in recipe.steps["group"].capture
        assert "groups_path" not in recipe.steps["group"].capture

    def test_ig2_group_step_is_not_optional(self, recipe) -> None:
        """T_IG2: group step must always run — no skip_when_false, not conditional."""
        step = recipe.steps["group"]
        assert step.skip_when_false is None
        assert not step.optional

    def test_ig3_source_doc_required(self, recipe) -> None:
        """T_IG3: source_doc must be a required ingredient in the groups recipe."""
        src = recipe.ingredients.get("source_doc")
        assert src is not None
        assert src.required is True

    def test_ig4_no_make_groups_ingredient(self, recipe) -> None:
        """T_IG4: make_groups must not be present — groups are always used in this recipe."""
        assert "make_groups" not in recipe.ingredients

    def test_ig5_next_or_done_routes_more_groups_to_plan(self, recipe) -> None:
        """T_IG5: next_or_done must route more_groups back to plan for group iteration."""
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert any(
            c.route == "plan" and c.when is not None and "more_groups" in c.when for c in conds
        ), "next_or_done must have a predicate routing more_groups → plan"

    def test_ig6_next_or_done_routes_more_parts_to_verify(self, recipe) -> None:
        """T_IG6: next_or_done must route more_parts to verify for sequential part processing."""
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert any(
            c.route == "verify" and c.when is not None and "more_parts" in c.when for c in conds
        ), "next_or_done must have a predicate routing more_parts → verify"

    def test_ig7_next_or_done_fallthrough_to_audit_impl(self, recipe) -> None:
        """T_IG7: next_or_done fallthrough (all done) must route to audit_impl."""
        step = recipe.steps["next_or_done"]
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert any(c.route == "audit_impl" for c in conds)

    def test_ig8_plan_note_contains_accumulation_instruction(self, recipe) -> None:
        """T_IG8: plan step note must instruct agent to accumulate plan paths across groups."""
        note = recipe.steps["plan"].note or ""
        assert "ACCUMULATION" in note
        assert "all_plan_paths" in note

    def test_ig_push_merge_target_routes_to_group(self, recipe) -> None:
        """create_and_publish must route to group, not plan, in the groups recipe."""
        step = recipe.steps.get("create_and_publish")
        assert step is not None
        assert step.on_success == "group"

    def test_ig_audit_impl_uses_base_sha_as_ref(self, recipe) -> None:
        """audit_impl must use context.base_sha as implementation_ref."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.base_sha" in skill_cmd
        assert "context.branch_name" not in skill_cmd

    def test_ig_fix_step_routes_via_on_result_to_test(self, recipe) -> None:
        """fix step must route via verdict-gated on_result to test."""
        step = recipe.steps["fix"]
        assert step.on_success is None, "fix step must use on_result: verdict dispatch"
        assert step.on_result is not None, "fix step must have on_result: verdict dispatch"
        test_routes = [
            c.route for c in step.on_result.conditions if c.when and "real_fix" in c.when
        ]
        assert any(r == "test" for r in test_routes), (
            "fix step on_result must route verdict=real_fix to test"
        )

    def test_ig_push_after_audit_warning_fires(self, recipe) -> None:
        """push-before-audit semantic rule fires as WARNING (audit has skip_when_false)."""
        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1
        assert all(v.severity == Severity.WARNING for v in violations)

    def test_ig_auto_merge_ingredient_exists(self, recipe) -> None:
        """REQ-C7-01: auto_merge ingredient must exist in implementation-groups.yaml."""
        assert "auto_merge" in recipe.ingredients, (
            "auto_merge ingredient is missing — required for merge queue lifecycle"
        )
        assert recipe.ingredients["auto_merge"].default == "true"

    def test_ig_extract_pr_number_step_exists(self, recipe) -> None:
        """REQ-C7-01: extract_pr_number step must exist to supply pr_number to queue steps."""
        assert "extract_pr_number" in recipe.steps
        step = recipe.steps["extract_pr_number"]
        assert step.tool == "run_cmd"
        assert "pr_number" in step.capture
        assert step.on_success == "annotate_pr_diff"

    def test_ig_ci_watch_routes_to_check_repo_merge_state(self, recipe) -> None:
        """REQ-C7-01: ci_watch on_result success must route to check_repo_merge_state."""
        step = recipe.steps["ci_watch"]
        assert step.on_result is not None, "ci_watch must use on_result predicate routing"
        result_routes = {c.route for c in step.on_result.conditions}
        assert "check_repo_merge_state" in result_routes, (
            "ci_watch must route to check_repo_merge_state so the PR can enter the merge queue. "
            "Routing directly to release_issue_success skips the queue lifecycle entirely."
        )

    def test_ig_check_repo_merge_state_step_exists(self, recipe) -> None:
        """REQ-C7-01: check_repo_merge_state step must exist."""
        assert "check_repo_merge_state" in recipe.steps
        step = recipe.steps["check_repo_merge_state"]
        assert step.tool == "check_repo_merge_state"
        assert step.block == "pre_queue_gate"
        assert "queue_available" in step.capture

    def test_ig_wait_for_queue_step_exists(self, recipe) -> None:
        """REQ-C7-01: wait_for_queue step must exist with correct tool and routing."""
        assert "wait_for_queue" in recipe.steps
        step = recipe.steps["wait_for_queue"]
        assert step.tool == "wait_for_merge_queue"
        assert step.with_args.get("timeout_seconds") == 900
        conds = step.on_result.conditions if step.on_result else []
        merged_cond = next((c for c in conds if c.when and "merged" in c.when), None)
        assert merged_cond is not None and merged_cond.route == "release_issue_success"

    def test_ig_release_issue_success_has_target_branch(self, recipe) -> None:
        """REQ-C7-01: release_issue_success must pass target_branch to trigger staged label."""
        step = recipe.steps["release_issue_success"]
        with_args = step.with_args or {}
        assert "target_branch" in with_args, (
            "release_issue_success must pass target_branch: ${{ inputs.base_branch }} — "
            "without it release_issue cannot apply the staged label on non-default branches"
        )
        assert "inputs.base_branch" in with_args["target_branch"]

    def test_create_branch_uses_callable(self, recipe) -> None:
        """create_and_publish must use create_and_publish_branch MCP tool."""
        step = recipe.steps["create_and_publish"]
        assert step.tool == "create_and_publish_branch"

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_and_publish uses create_and_publish_branch (which checks ls-remote)."""
        assert recipe.steps["create_and_publish"].tool == "create_and_publish_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """create_and_publish with_args must include issue_number for branch naming."""
        assert "issue_number" in recipe.steps["create_and_publish"].with_args

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """create_and_publish with_args must include run_name for branch naming."""
        assert "run_name" in recipe.steps["create_and_publish"].with_args


# ---------------------------------------------------------------------------
# TestInvestigateFirstStructure
# ---------------------------------------------------------------------------


class TestInvestigateFirstStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "remediation.yaml")

    def test_if1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
        recipe,
    ) -> None:
        """T_IF1: audit_impl captures remediation_path and routes via on_result using verdict.

        Uses predicate format (v0.3.0): verdict is read directly from result.verdict in predicate
        conditions — it is not captured as context.verdict (which would create a dead output).
        """
        step = recipe.steps["audit_impl"]
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert len(conds) > 0, "audit_impl on_result must have predicate conditions"
        assert any("result.verdict" in (c.when or "") for c in conds), (
            "audit_impl on_result must have a condition checking result.verdict"
        )

    def test_if2_remediate_step_routes_to_make_plan(self, recipe) -> None:
        """T_IF2: remediate step exists and routes to make_plan (not rectify)."""
        assert "remediate" in recipe.steps
        assert recipe.steps["remediate"].on_success == "make_plan"

    def test_if5_make_plan_step_has_correct_structure(self, recipe) -> None:
        """T_IF5: make_plan step calls make-plan with remediation_path and captures outputs."""
        assert "make_plan" in recipe.steps
        step = recipe.steps["make_plan"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "/autoskillit:make-plan" in skill_cmd
        assert "context.remediation_path" in skill_cmd
        assert "plan_path" in step.capture
        assert "plan_parts" in step.capture_list
        assert step.on_success == "dry_walkthrough"
        assert step.on_failure == "release_issue_failure"

    def test_if3_test_step_uses_implementation_ref(self, recipe) -> None:
        """T_IF3: test step worktree_path must reference context.implementation_ref."""
        worktree_arg = recipe.steps["test"].with_args.get("worktree_path", "")
        assert "context.implementation_ref" in worktree_arg
        assert "context.work_dir" not in worktree_arg

    def test_if4_merge_step_uses_implementation_ref(self, recipe) -> None:
        """T_IF4: merge step worktree_path must reference context.implementation_ref."""
        worktree_arg = recipe.steps["merge"].with_args.get("worktree_path", "")
        assert "context.implementation_ref" in worktree_arg
        assert "context.work_dir" not in worktree_arg

    def test_if_b1_implement_captures_branch_name(self, recipe) -> None:
        """T_IF_B1: implement step must capture branch_name from result."""
        step = recipe.steps["implement"]
        assert "branch_name" in step.capture, (
            "implement step must capture branch_name so audit_impl can pass a "
            "stable git ref to audit-impl after merge_worktree deletes the worktree"
        )

    def test_if_b2_audit_impl_uses_branch_name_as_ref(self, recipe) -> None:
        """T_IF_B2: audit_impl with: must reference context.branch_name as implementation_ref."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.branch_name" in skill_cmd, (
            "audit_impl must pass context.branch_name as implementation_ref — not "
            "context.implementation_ref or context.worktree_path (stale after merge)"
        )

    def test_if_b3_retry_worktree_captures_branch_name(self, recipe) -> None:
        """T_IF_B3: retry_worktree step must also capture branch_name."""
        step = recipe.steps["retry_worktree"]
        assert "branch_name" in step.capture, (
            "retry_worktree also updates the active worktree reference; "
            "it must capture branch_name for downstream audit_impl use"
        )

    def test_create_branch_uses_callable(self, recipe) -> None:
        """create_and_publish must use create_and_publish_branch MCP tool."""
        step = recipe.steps["create_and_publish"]
        assert step.tool == "create_and_publish_branch"

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_and_publish uses create_and_publish_branch (which checks ls-remote)."""
        assert recipe.steps["create_and_publish"].tool == "create_and_publish_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """create_and_publish with_args must include issue_number for branch naming."""
        assert "issue_number" in recipe.steps["create_and_publish"].with_args

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """create_and_publish with_args must include run_name for branch naming."""
        assert "run_name" in recipe.steps["create_and_publish"].with_args

    def test_if_push_after_audit_warning_fires(self, recipe) -> None:
        """push-before-audit semantic rule fires as WARNING (audit has skip_when_false)."""
        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1
        assert all(v.severity == Severity.WARNING for v in violations)

    def test_if_c1_implement_uses_no_merge_skill(self, recipe) -> None:
        """T_IF_C1: implement step must use implement-worktree-no-merge.

        implement-worktree merges and deletes the worktree internally; subsequent
        verify (test_check) and assess (resolve-failures) steps would run against
        a non-existent path. implement-worktree-no-merge leaves the worktree intact
        for the orchestrator's gate-test-merge cycle.
        """
        step = recipe.steps["implement"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "implement-worktree-no-merge" in skill_cmd, (
            "implement step must use implement-worktree-no-merge; "
            "implement-worktree merges immediately, making verify and assess unreachable"
        )

    def test_remediation_investigate_captures_investigation_path(self, recipe) -> None:
        """1c: investigate step must have a capture block containing investigation_path."""
        step = recipe.steps["investigate"]
        assert step.capture is not None and "investigation_path" in step.capture, (
            "investigate step must capture investigation_path so rectify receives "
            "the explicit path rather than scanning the filesystem"
        )
        assert step.capture["investigation_path"] == "${{ result.investigation_path }}"

    def test_remediation_rectify_uses_context_investigation_path(self, recipe) -> None:
        """1d: rectify step must pass ${{ context.investigation_path }} in skill_command."""
        step = recipe.steps["rectify"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "${{ context.investigation_path }}" in skill_cmd, (
            "rectify step skill_command must include ${{ context.investigation_path }} "
            "to pass the explicit path from the capture block"
        )

    def test_if_resolve_review_uses_resolve_review_skill(self, recipe) -> None:
        """T_IF_RR1: resolve_review step must invoke resolve-review, not resolve-failures.

        resolve-failures is test-driven and finds no work when tests are green.
        resolve-review reads PR review comments and applies the reviewer's requested changes.
        """
        assert "resolve_review" in recipe.steps
        step = recipe.steps["resolve_review"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "resolve-review" in skill_cmd, (
            "resolve_review step must call /autoskillit:resolve-review; "
            "resolve-failures is test-driven and ignores PR review comments"
        )
        assert "resolve-failures" not in skill_cmd, (
            "resolve_review step must not call resolve-failures; "
            "that skill does not read review comments"
        )

    def test_if_resolve_review_passes_merge_target(self, recipe) -> None:
        """T_IF_RR2: resolve_review skill_command must pass context.merge_target."""
        step = recipe.steps["resolve_review"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.merge_target" in skill_cmd, (
            "resolve-review requires feature_branch as first arg; "
            "context.merge_target holds the feature branch name"
        )

    def test_remediation_no_add_dir_dead_param(self, recipe) -> None:
        """Remediation recipe must not have add_dir as a with: key (removed dead param)."""
        findings = run_semantic_rules(recipe)
        add_dir_dead = [
            f for f in findings if f.rule == "dead-with-param" and "add_dir" in f.message
        ]
        assert not add_dir_dead, (
            f"Remediation recipe still has dead add_dir param: "
            f"{[(f.step_name, f.message) for f in add_dir_dead]}"
        )

    def test_remediation_assess_step_has_on_context_limit(self, recipe) -> None:
        """REQ-RCP-002: assess step in remediation.yaml must declare on_context_limit: test.

        assess runs resolve-failures inside an existing worktree. Partial fixes are committed
        to disk, so routing to test checks whether partial work was sufficient — same rationale
        as the fix step in implementation.yaml.
        """
        assess = recipe.steps["assess"]
        assert assess.on_context_limit == "test", (
            f"remediation.yaml assess step must declare on_context_limit: test, "
            f"got: {assess.on_context_limit!r}"
        )
