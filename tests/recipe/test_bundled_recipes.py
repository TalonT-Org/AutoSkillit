"""Tests for structural assertions on individual bundled YAML recipe files."""

from __future__ import annotations

import pytest
import yaml

from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import analyze_dataflow, run_semantic_rules

# ---------------------------------------------------------------------------
# TestImplementationPipelineStructure
# ---------------------------------------------------------------------------


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

    def test_ip_c1_fix_step_routes_on_success_to_test(self, recipe) -> None:
        """T_IP_C1: fix step must route on_success back to test (not next_or_done).

        assess-and-merge used to merge internally, so fix routed to next_or_done
        after the internal merge. resolve-failures does not merge, so the orchestrator
        must route fix → test to re-validate before entering merge_worktree.
        """
        step = recipe.steps["fix"]
        assert step.on_success == "test", (
            "fix step must route back to test — resolve-failures only fixes failures, "
            "it does not merge. The orchestrator must re-validate before merge_worktree."
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
        """A4: The base_sha capture command must reference context.work_dir."""
        sha_step = next(
            (
                step
                for name, step in recipe.steps.items()
                if step.capture and "base_sha" in step.capture
            ),
            None,
        )
        assert sha_step is not None, "No step captures base_sha"
        with_args_str = str(sha_step.with_args)
        assert "context.work_dir" in with_args_str, (
            "base_sha capture must run inside context.work_dir (the clone directory)"
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
        from autoskillit.core.types import Severity
        from autoskillit.recipe.validator import run_semantic_rules

        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1, (
            "push-before-audit must fire: audit_impl has skip_when_false so push is "
            "reachable via the audit=false bypass path"
        )
        assert all(v.severity == Severity.WARNING for v in violations)

    def test_ip_open_pr_step_routes_to_review_pr(self, recipe) -> None:
        """open_pr_step.on_success must be review_pr — review loop inserted before ci_watch."""
        open_pr_step = recipe.steps["open_pr_step"]
        assert open_pr_step.on_success == "review_pr", (
            "open_pr_step must route to review_pr — review loop runs before ci_watch now"
        )

    def test_ip_open_pr_step_has_skip_when_false(self, recipe) -> None:
        """open_pr_step must declare skip_when_false: inputs.open_pr."""
        open_pr_step = recipe.steps["open_pr_step"]
        assert open_pr_step.skip_when_false == "inputs.open_pr"

    def test_ip_audit_impl_has_skip_when_false(self, recipe) -> None:
        """audit_impl must declare skip_when_false: inputs.audit."""
        audit_step = recipe.steps["audit_impl"]
        assert audit_step.skip_when_false == "inputs.audit"

    def test_ip_create_branch_has_skip_when_false(self, recipe) -> None:
        """create_branch must declare skip_when_false: inputs.open_pr."""
        create_branch = recipe.steps["create_branch"]
        assert create_branch.skip_when_false == "inputs.open_pr"

    def test_create_branch_does_not_use_run_name_verbatim(self, recipe) -> None:
        """compute_branch must not use inputs.run_name as the full branch name."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "git checkout -b ${{ inputs.run_name }} &&" not in cmd

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_branch must use create_unique_branch tool (which always checks ls-remote)."""
        assert recipe.steps["create_branch"].tool == "create_unique_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """compute_branch cmd must reference context.issue_number for branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "context.issue_number" in cmd

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """compute_branch must use inputs.run_name as a prefix in branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "inputs.run_name" in cmd

    def test_ip_main_push_step_not_reachable_after_open_pr(self, recipe) -> None:
        """The main `push` step must not be reachable after open_pr_step —
        that would be a double-push. The new `re_push` step IS reachable and is correct."""
        from autoskillit.recipe.validator import _build_step_graph

        graph = _build_step_graph(recipe)
        visited: set[str] = set()
        queue = [recipe.steps["open_pr_step"].on_success]
        while queue:
            current = queue.pop(0)
            if current in visited or current not in recipe.steps:
                continue
            visited.add(current)
            queue.extend(graph.get(current, []))
        assert "push" not in visited, (
            "'push' step is reachable after open_pr_step — double-push risk. "
            "(re_push is allowed; push is not)"
        )

    def test_ip_open_pr_false_path_reaches_push_then_cleanup(self, recipe) -> None:
        """When open_pr_step is bypassed (open_pr=false), execution must go:
        audit_impl (GO) → push → [open_pr_step bypassed] → cleanup_success → done.
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

    def test_ip_open_pr_step_references_all_plan_paths(self, recipe) -> None:
        """open_pr_step must pass all accumulated plan paths, not just the last."""
        cmd = recipe.steps["open_pr_step"].with_args.get("skill_command", "")
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

    def test_ip_ci_watch_exists_and_is_gated(self, recipe) -> None:
        """T_CI1: ci_watch step exists, uses wait_for_ci, has skip_when_false: inputs.open_pr,
        and specifies timeout_seconds: 300."""
        assert "ci_watch" in recipe.steps
        step = recipe.steps["ci_watch"]
        assert step.tool == "wait_for_ci"
        assert step.skip_when_false == "inputs.open_pr"
        assert step.with_args.get("timeout_seconds") == 300

    def test_ip_ci_watch_routing(self, recipe) -> None:
        """T_CI2: ci_watch on_success -> release_issue_success; on_failure -> diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "release_issue_success"
        assert step.on_failure == "diagnose_ci"

    def test_ip_ci_watch_uses_merge_target(self, recipe) -> None:
        """T_CI3: ci_watch uses branch param with context.merge_target, no inline shell."""
        step = recipe.steps["ci_watch"]
        assert "context.merge_target" in step.with_args["branch"]
        assert "cmd" not in step.with_args
        assert "ci_conclusion" in step.capture
        assert "ci_failed_jobs" in step.capture

    def test_ip_resolve_ci_structure(self, recipe) -> None:
        """T_CI4: resolve_ci step exists, uses resolve-failures, has retries: 2
        and on_exhausted: release_issue_failure."""
        assert "resolve_ci" in recipe.steps
        step = recipe.steps["resolve_ci"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "resolve-failures" in skill_cmd
        assert step.retries == 2
        assert step.on_exhausted == "release_issue_failure"

    def test_ip_resolve_ci_uses_work_dir(self, recipe) -> None:
        """T_CI5: resolve_ci uses context.work_dir as the worktree path."""
        cmd = recipe.steps["resolve_ci"].with_args.get("skill_command", "")
        assert "context.work_dir" in cmd

    def test_ip_re_push_loops_back_to_ci_watch(self, recipe) -> None:
        """T_CI6: re_push step exists, is push_to_remote, routes on_success back to ci_watch."""
        assert "re_push" in recipe.steps
        step = recipe.steps["re_push"]
        assert step.tool == "push_to_remote"
        assert step.on_success == "ci_watch"
        assert step.on_failure == "release_issue_failure"

    def test_ip_re_push_has_explicit_remote_url(self, recipe) -> None:
        """T_CI7: re_push uses explicit remote_url (satisfies push-missing-explicit-remote-url)."""
        with_args = recipe.steps["re_push"].with_args
        assert "remote_url" in with_args
        assert "context.remote_url" in with_args["remote_url"]

    def test_ip_open_pr_step_routes_to_review_pr_ci(self, recipe) -> None:
        """T_CI8: open_pr_step.on_success is now review_pr (review loop before ci_watch)."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "review_pr", (
            "open_pr_step must route to review_pr — review loop runs before ci_watch now"
        )


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
        """push_merge_target must route to group, not plan, in the groups recipe."""
        step = recipe.steps.get("push_merge_target")
        assert step is not None
        assert step.on_success == "group"

    def test_ig_audit_impl_uses_base_sha_as_ref(self, recipe) -> None:
        """audit_impl must use context.base_sha as implementation_ref."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.base_sha" in skill_cmd
        assert "context.branch_name" not in skill_cmd

    def test_ig_fix_step_routes_on_success_to_test(self, recipe) -> None:
        """fix step must route on_success to test (resolve-failures does not merge)."""
        assert recipe.steps["fix"].on_success == "test"

    def test_ig_push_after_audit_warning_fires(self, recipe) -> None:
        """push-before-audit semantic rule fires as WARNING (audit has skip_when_false)."""
        from autoskillit.core.types import Severity
        from autoskillit.recipe.validator import run_semantic_rules

        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1
        assert all(v.severity == Severity.WARNING for v in violations)

    def test_ig_ci_watch_exists_and_is_gated(self, recipe) -> None:
        """T_CI1: ci_watch step exists, uses wait_for_ci, has skip_when_false: inputs.open_pr,
        and specifies timeout_seconds: 300."""
        assert "ci_watch" in recipe.steps
        step = recipe.steps["ci_watch"]
        assert step.tool == "wait_for_ci"
        assert step.skip_when_false == "inputs.open_pr"
        assert step.with_args.get("timeout_seconds") == 300

    def test_ig_ci_watch_routing(self, recipe) -> None:
        """T_CI2: ci_watch on_success -> release_issue_success; on_failure -> diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "release_issue_success"
        assert step.on_failure == "diagnose_ci"

    def test_ig_ci_watch_uses_merge_target(self, recipe) -> None:
        """T_CI3: ci_watch uses branch param with context.merge_target, no inline shell."""
        step = recipe.steps["ci_watch"]
        assert "context.merge_target" in step.with_args["branch"]
        assert "cmd" not in step.with_args
        assert "ci_conclusion" in step.capture
        assert "ci_failed_jobs" in step.capture

    def test_ig_resolve_ci_structure(self, recipe) -> None:
        """T_CI4: resolve_ci step exists, uses resolve-failures, has retries: 2
        and on_exhausted: release_issue_failure."""
        assert "resolve_ci" in recipe.steps
        step = recipe.steps["resolve_ci"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "resolve-failures" in skill_cmd
        assert step.retries == 2
        assert step.on_exhausted == "release_issue_failure"

    def test_ig_resolve_ci_uses_work_dir(self, recipe) -> None:
        """T_CI5: resolve_ci uses context.work_dir as the worktree path."""
        cmd = recipe.steps["resolve_ci"].with_args.get("skill_command", "")
        assert "context.work_dir" in cmd

    def test_ig_re_push_loops_back_to_ci_watch(self, recipe) -> None:
        """T_CI6: re_push step exists, is push_to_remote, routes on_success back to ci_watch."""
        assert "re_push" in recipe.steps
        step = recipe.steps["re_push"]
        assert step.tool == "push_to_remote"
        assert step.on_success == "ci_watch"
        assert step.on_failure == "release_issue_failure"

    def test_ig_re_push_has_explicit_remote_url(self, recipe) -> None:
        """T_CI7: re_push uses explicit remote_url."""
        with_args = recipe.steps["re_push"].with_args
        assert "remote_url" in with_args
        assert "context.remote_url" in with_args["remote_url"]


# ---------------------------------------------------------------------------
# TestBugfixLoopStructure
# ---------------------------------------------------------------------------


class TestBugfixLoopStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "bugfix-loop.yaml")

    def test_bl1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
        recipe,
    ) -> None:
        """T_BL1: audit_impl captures remediation_path and routes via on_result using verdict.

        Uses predicate format (v0.3.0): verdict is read directly from result.verdict in predicate
        conditions — not captured as context.verdict (which would create a dead output).
        """
        step = recipe.steps["audit_impl"]
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        conds = step.on_result.conditions
        assert len(conds) > 0, "audit_impl on_result must have predicate conditions"
        assert any("result.verdict" in (c.when or "") for c in conds), (
            "audit_impl on_result must have a condition checking result.verdict"
        )

    def test_bl2_remediate_step_exists_with_on_success_plan(self, recipe) -> None:
        """T_BL2: a step named remediate exists with on_success == 'plan'."""
        assert "remediate" in recipe.steps
        assert recipe.steps["remediate"].on_success == "plan"

    def test_bl_b1_implement_captures_branch_name(self, recipe) -> None:
        """T_BL_B1: implement step must capture branch_name from result."""
        step = recipe.steps["implement"]
        assert "branch_name" in step.capture, (
            "implement step must capture branch_name so audit_impl can pass a "
            "stable git ref to audit-impl after merge_worktree deletes the worktree"
        )

    def test_bl_b2_audit_impl_uses_branch_name_as_ref(self, recipe) -> None:
        """T_BL_B2: audit_impl with: must reference context.branch_name as implementation_ref."""
        step = recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.branch_name" in skill_cmd, (
            "audit_impl must pass context.branch_name as implementation_ref — not "
            "context.implementation_ref or context.worktree_path (stale after merge)"
        )

    def test_bl_b3_retry_worktree_captures_branch_name(self, recipe) -> None:
        """T_BL_B3: retry_worktree step must also capture branch_name."""
        step = recipe.steps["retry_worktree"]
        assert "branch_name" in step.capture, (
            "retry_worktree also updates the active worktree reference; "
            "it must capture branch_name for downstream audit_impl use"
        )

    def test_bugfix_loop_investigate_captures_investigation_path(self, recipe) -> None:
        """1e: investigate step must capture investigation_path; plan step must pass it."""
        investigate_step = recipe.steps["investigate"]
        assert (
            investigate_step.capture is not None
            and "investigation_path" in investigate_step.capture
        ), "bugfix-loop investigate step must capture investigation_path"
        plan_step = recipe.steps["plan"]
        skill_cmd = plan_step.with_args.get("skill_command", "")
        assert "${{ context.investigation_path }}" in skill_cmd, (
            "bugfix-loop plan step skill_command must pass ${{ context.investigation_path }}"
        )


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
        assert step.on_success == "review"
        assert step.on_failure == "release_issue_failure"

    def test_if3_verify_step_uses_implementation_ref(self, recipe) -> None:
        """T_IF3: verify step worktree_path must reference context.implementation_ref."""
        worktree_arg = recipe.steps["verify"].with_args.get("worktree_path", "")
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

    def test_create_branch_does_not_use_run_name_verbatim(self, recipe) -> None:
        """compute_branch must not use inputs.run_name as the full branch name."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "git checkout -b ${{ inputs.run_name }} &&" not in cmd

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_branch must use create_unique_branch tool (which always checks ls-remote)."""
        assert recipe.steps["create_branch"].tool == "create_unique_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """compute_branch cmd must reference context.issue_number for branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "context.issue_number" in cmd

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """compute_branch must use inputs.run_name as a prefix in branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "inputs.run_name" in cmd

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

    def test_if_ci_watch_exists_and_is_gated(self, recipe) -> None:
        """T_CI1: ci_watch step exists, uses wait_for_ci, has skip_when_false: inputs.open_pr,
        and specifies timeout_seconds: 300."""
        assert "ci_watch" in recipe.steps
        step = recipe.steps["ci_watch"]
        assert step.tool == "wait_for_ci"
        assert step.skip_when_false == "inputs.open_pr"
        assert step.with_args.get("timeout_seconds") == 300

    def test_if_ci_watch_routing(self, recipe) -> None:
        """T_CI2: ci_watch on_success -> release_issue_success; on_failure -> diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "release_issue_success"
        assert step.on_failure == "diagnose_ci"

    def test_if_ci_watch_uses_merge_target(self, recipe) -> None:
        """T_CI3: ci_watch uses branch param with context.merge_target, no inline shell."""
        step = recipe.steps["ci_watch"]
        assert "context.merge_target" in step.with_args["branch"]
        assert "cmd" not in step.with_args
        assert "ci_conclusion" in step.capture
        assert "ci_failed_jobs" in step.capture

    def test_if_resolve_ci_structure(self, recipe) -> None:
        """T_CI4: resolve_ci step exists, uses resolve-failures, has retries: 2
        and on_exhausted: release_issue_failure."""
        assert "resolve_ci" in recipe.steps
        step = recipe.steps["resolve_ci"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "resolve-failures" in skill_cmd
        assert step.retries == 2
        assert step.on_exhausted == "release_issue_failure"

    def test_if_resolve_ci_uses_work_dir(self, recipe) -> None:
        """T_CI5: resolve_ci uses context.work_dir as the worktree path."""
        cmd = recipe.steps["resolve_ci"].with_args.get("skill_command", "")
        assert "context.work_dir" in cmd

    def test_if_re_push_loops_back_to_ci_watch(self, recipe) -> None:
        """T_CI6: re_push step exists, is push_to_remote, routes on_success back to ci_watch."""
        assert "re_push" in recipe.steps
        step = recipe.steps["re_push"]
        assert step.tool == "push_to_remote"
        assert step.on_success == "ci_watch"
        assert step.on_failure == "release_issue_failure"

    def test_if_re_push_has_explicit_remote_url(self, recipe) -> None:
        """T_CI7: re_push uses explicit remote_url."""
        with_args = recipe.steps["re_push"].with_args
        assert "remote_url" in with_args
        assert "context.remote_url" in with_args["remote_url"]

    def test_if_open_pr_step_routes_to_review_pr(self, recipe) -> None:
        """T_CI8: open_pr_step.on_success is review_pr (review loop before ci_watch)."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "review_pr", (
            "open_pr_step must route to review_pr — review loop runs before ci_watch now"
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


# ---------------------------------------------------------------------------
# TestAuditAndFixStructure
# ---------------------------------------------------------------------------


class TestAuditAndFixStructure:
    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "audit-and-fix.yaml")

    def test_aaf1_implement_uses_no_merge_skill(self, recipe) -> None:
        """T_AAF1: implement step must use implement-worktree-no-merge."""
        step = recipe.steps["implement"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "implement-worktree-no-merge" in skill_cmd, (
            "implement step must use implement-worktree-no-merge; "
            "implement-worktree merges immediately so test_check runs on a deleted worktree"
        )

    def test_aaf2_has_merge_step(self, recipe) -> None:
        """T_AAF2: recipe must have a merge step (merge_worktree) after test passes."""
        assert "merge" in recipe.steps, (
            "audit-and-fix must have a merge step; "
            "without it the worktree is never merged after passing tests"
        )

    def test_aaf3_test_success_routes_to_merge(self, recipe) -> None:
        """T_AAF3: test step must route to merge (not push) on success."""
        assert recipe.steps["test"].on_success == "merge", (
            "test must route to merge on success — push comes after merge, "
            "not directly after test_check"
        )

    def test_aaf4_has_fix_step_for_test_failures(self, recipe) -> None:
        """T_AAF4: test on_failure must route to a fix/assess step, not cleanup_failure."""
        step = recipe.steps["test"]
        assert step.on_failure not in ("cleanup_failure", "escalate_stop"), (
            "test on_failure must route to a fix/assess step; "
            "going directly to cleanup_failure discards fixable failures"
        )

    def test_aaf5_fix_step_routes_back_to_test(self, recipe) -> None:
        """T_AAF5: fix step must exist and route on_success back to test."""
        assert "fix" in recipe.steps, "fix step must exist for resolve-failures loop"
        assert recipe.steps["fix"].on_success == "test", (
            "fix step must route back to test on success to re-validate the worktree"
        )

    def test_aaf6_merge_step_routes_to_push(self, recipe) -> None:
        """T_AAF6: merge step must route to push on success."""
        merge = recipe.steps["merge"]
        # With on_result, success is the default (unconditional) route
        default_routes = [c for c in merge.on_result.conditions if c.when is None]
        assert len(default_routes) == 1
        assert default_routes[0].route == "push", (
            "merge step must route to push — the push step propagates the merged branch "
            "from the clone back to the upstream remote"
        )

    def test_aaf7_merge_routes_dirty_tree_to_fix(self, recipe) -> None:
        """T_AAF7: merge step must route dirty_tree to fix (not release_issue_failure)."""
        merge = recipe.steps["merge"]
        assert merge.on_result is not None, (
            "merge step must use on_result with predicate conditions "
            "to handle recoverable merge failures"
        )
        conditions = merge.on_result.conditions
        dirty_tree_routes = [c for c in conditions if c.when and "dirty_tree" in c.when]
        assert len(dirty_tree_routes) == 1
        assert dirty_tree_routes[0].route == "fix"

    def test_create_branch_does_not_use_run_name_verbatim(self, recipe) -> None:
        """compute_branch must not use inputs.run_name as the full branch name."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "git checkout -b ${{ inputs.run_name }} &&" not in cmd

    def test_create_branch_checks_remote_for_collisions(self, recipe) -> None:
        """create_branch must use create_unique_branch tool (which always checks ls-remote)."""
        assert recipe.steps["create_branch"].tool == "create_unique_branch"

    def test_create_branch_references_issue_number(self, recipe) -> None:
        """compute_branch cmd must reference context.issue_number for branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "context.issue_number" in cmd

    def test_create_branch_uses_run_name_as_prefix(self, recipe) -> None:
        """compute_branch must use inputs.run_name as a prefix in branch naming."""
        cmd = recipe.steps["compute_branch"].with_args["cmd"]
        assert "inputs.run_name" in cmd

    def test_audit_and_fix_investigate_captures_investigation_path(self, recipe) -> None:
        """1f: investigate step must capture investigation_path; plan step must pass it."""
        step = recipe.steps["investigate"]
        assert step.capture is not None and "investigation_path" in step.capture, (
            "audit-and-fix investigate step must capture investigation_path"
        )
        plan_step = recipe.steps["plan"]
        skill_cmd = plan_step.with_args.get("skill_command", "")
        assert "${{ context.investigation_path }}" in skill_cmd, (
            "audit-and-fix plan step skill_command must pass ${{ context.investigation_path }}"
        )

    def test_aaf_ci_watch_exists_and_is_gated(self, recipe) -> None:
        """T_CI1: ci_watch step exists, uses wait_for_ci, has skip_when_false: inputs.open_pr,
        and specifies timeout_seconds: 300."""
        assert "ci_watch" in recipe.steps
        step = recipe.steps["ci_watch"]
        assert step.tool == "wait_for_ci"
        assert step.skip_when_false == "inputs.open_pr"
        assert step.with_args.get("timeout_seconds") == 300

    def test_aaf_ci_watch_routing(self, recipe) -> None:
        """T_CI2: ci_watch on_success -> release_issue_success; on_failure -> diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "release_issue_success"
        assert step.on_failure == "diagnose_ci"

    def test_aaf_ci_watch_uses_merge_target(self, recipe) -> None:
        """T_CI3: ci_watch uses branch param with context.merge_target, no inline shell."""
        step = recipe.steps["ci_watch"]
        assert "context.merge_target" in step.with_args["branch"]
        assert "cmd" not in step.with_args
        assert "ci_conclusion" in step.capture
        assert "ci_failed_jobs" in step.capture

    def test_aaf_resolve_ci_structure(self, recipe) -> None:
        """T_CI4: resolve_ci step exists, uses resolve-failures, has retries: 2
        and on_exhausted: release_issue_failure."""
        assert "resolve_ci" in recipe.steps
        step = recipe.steps["resolve_ci"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "resolve-failures" in skill_cmd
        assert step.retries == 2
        assert step.on_exhausted == "release_issue_failure"

    def test_aaf_resolve_ci_uses_work_dir(self, recipe) -> None:
        """T_CI5: resolve_ci uses context.work_dir as the worktree path."""
        cmd = recipe.steps["resolve_ci"].with_args.get("skill_command", "")
        assert "context.work_dir" in cmd

    def test_aaf_re_push_loops_back_to_ci_watch(self, recipe) -> None:
        """T_CI6: re_push step exists, is push_to_remote, routes on_success back to ci_watch."""
        assert "re_push" in recipe.steps
        step = recipe.steps["re_push"]
        assert step.tool == "push_to_remote"
        assert step.on_success == "ci_watch"
        assert step.on_failure == "release_issue_failure"

    def test_aaf_re_push_has_explicit_remote_url(self, recipe) -> None:
        """T_CI7: re_push uses explicit remote_url."""
        with_args = recipe.steps["re_push"].with_args
        assert "remote_url" in with_args
        assert "context.remote_url" in with_args["remote_url"]

    def test_aaf_open_pr_step_routes_to_review_pr(self, recipe) -> None:
        """T_CI8: open_pr_step.on_success is review_pr (review loop before ci_watch)."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "review_pr", (
            "open_pr_step must route to review_pr — review loop runs before ci_watch now"
        )


# ---------------------------------------------------------------------------
# TestSmokeTestStructure
# ---------------------------------------------------------------------------


class TestSmokeTestStructure:
    """Structural assertions for the smoke-test.yaml recipe steps."""

    @pytest.fixture()
    def smoke_yaml(self) -> dict:
        recipe_path = builtin_recipes_dir() / "smoke-test.yaml"
        return yaml.safe_load(recipe_path.read_text())

    # T_ST1
    def test_create_branch_is_run_cmd(self, smoke_yaml: dict) -> None:
        """create_branch step has tool == "run_cmd" (not action == "route")."""
        assert smoke_yaml["steps"]["create_branch"]["tool"] == "run_cmd"

    # T_ST2
    def test_create_branch_captures_feature_branch(self, smoke_yaml: dict) -> None:
        """create_branch step has capture containing key feature_branch."""
        assert "feature_branch" in smoke_yaml["steps"]["create_branch"]["capture"]

    # T_ST3
    def test_check_summary_is_run_python(self, smoke_yaml: dict) -> None:
        """check_summary step has python discriminator (not action == "route")."""
        assert (
            smoke_yaml["steps"]["check_summary"]["python"]
            == "autoskillit.smoke_utils.check_bug_report_non_empty"
        )

    # T_ST4
    def test_check_summary_on_result_routes(self, smoke_yaml: dict) -> None:
        """check_summary step has on_result with predicate conditions for non_empty.

        v0.3.0 predicate format: on_result is a list of {when, route} dicts.
        """
        on_result = smoke_yaml["steps"]["check_summary"]["on_result"]
        assert isinstance(on_result, list), "on_result must be a predicate conditions list"
        routes = {c.get("route") for c in on_result}
        whens = [c.get("when", "") or "" for c in on_result]
        assert any("non_empty" in w for w in whens), (
            "check_summary on_result must check result.non_empty"
        )
        assert "create_summary" in routes, "check_summary must route to create_summary"
        assert "done" in routes, "check_summary must have a fallthrough route to done"

    # T_ST5
    def test_merge_references_context_feature_branch(self, smoke_yaml: dict) -> None:
        """merge step with_args references context.feature_branch."""
        base_branch = smoke_yaml["steps"]["merge"]["with"]["base_branch"]
        assert "context.feature_branch" in base_branch

    # A6
    def test_smoke_feature_branch_unconditionally_set(self, smoke_yaml: dict) -> None:
        """A6: feature_branch must be set by a non-optional step before merge."""
        steps = smoke_yaml["steps"]
        step_names = list(steps.keys())
        merge_idx = next(
            (
                i
                for i, name in enumerate(step_names)
                if steps[name].get("tool") == "merge_worktree"
            ),
            None,
        )
        assert merge_idx is not None, "merge step not found"
        unconditional = next(
            (
                name
                for name in step_names[:merge_idx]
                if "feature_branch" in steps[name].get("capture", {})
                and not steps[name].get("optional", False)
            ),
            None,
        )
        assert unconditional is not None, (
            "feature_branch is only captured by optional create_branch. "
            "When collect_on_branch=false, merge step receives undefined context.feature_branch."
        )


# ---------------------------------------------------------------------------
# Contract tests — plan_parts output (D4–D5)
# ---------------------------------------------------------------------------


def test_make_plan_contract_declares_plan_parts_output() -> None:
    """D4: make-plan contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    make_plan = manifest.get("skills", {}).get("make-plan", {})
    output_names = [o["name"] for o in make_plan.get("outputs", [])]
    assert "plan_parts" in output_names, (
        "make-plan contract must declare plan_parts as an output "
        "so capture_list coverage validation can enforce it"
    )


def test_rectify_contract_declares_plan_parts_output() -> None:
    """D5: rectify contract must declare plan_parts as an output."""
    manifest = load_bundled_manifest()
    rectify = manifest.get("skills", {}).get("rectify", {})
    output_names = [o["name"] for o in rectify.get("outputs", [])]
    assert "plan_parts" in output_names


# ---------------------------------------------------------------------------
# Bundled diagram tests (DG-21, DG-29)
# ---------------------------------------------------------------------------


def test_bundled_recipes_have_diagrams() -> None:
    """DG-21: every bundled recipe has a pre-generated diagram."""
    from autoskillit.core.paths import pkg_root

    diagrams_dir = pkg_root() / "recipes" / "diagrams"
    recipes_dir = pkg_root() / "recipes"
    recipe_names = {p.stem for p in recipes_dir.glob("*.yaml")}
    diagram_names = {p.stem for p in diagrams_dir.glob("*.md")}
    assert recipe_names == diagram_names, (
        f"Missing diagrams for: {recipe_names - diagram_names}. "
        f"Run 'autoskillit recipes render' to regenerate."
    )


def test_bundled_diagrams_are_not_stale() -> None:
    """DG-29: every bundled recipe diagram is up to date with its YAML."""
    from autoskillit.core.paths import pkg_root
    from autoskillit.recipe.diagrams import check_diagram_staleness

    recipes_dir = pkg_root() / "recipes"
    stale: list[str] = []
    for recipe_path in sorted(recipes_dir.glob("*.yaml")):
        if check_diagram_staleness(recipe_path.stem, recipes_dir, recipe_path):
            stale.append(recipe_path.stem)
    assert not stale, (
        f"Stale diagrams for: {stale}. Run 'autoskillit recipes render' to regenerate."
    )


# ---------------------------------------------------------------------------
# Two-tier failure model tests
# ---------------------------------------------------------------------------


def test_all_predicate_steps_have_on_failure() -> None:
    """Every tool/python step with on_result.conditions must declare on_failure."""
    for recipe_name in ["implementation", "remediation", "bugfix-loop", "smoke-test"]:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        for step_name, step in recipe.steps.items():
            is_tool = step.tool is not None or step.python is not None
            if is_tool and step.on_result and step.on_result.conditions:
                assert step.on_failure is not None, (
                    f"{recipe_name}.{step_name}: predicate step must declare on_failure"
                )


def test_audit_impl_on_failure_routes_to_escalation() -> None:
    """audit_impl.on_failure must route to an escalation step in each recipe."""
    impl = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    rem = load_recipe(builtin_recipes_dir() / "remediation.yaml")
    bl = load_recipe(builtin_recipes_dir() / "bugfix-loop.yaml")
    assert impl.steps["audit_impl"].on_failure == "escalate_stop"
    assert rem.steps["audit_impl"].on_failure == "escalate_stop"
    assert bl.steps["audit_impl"].on_failure == "escalate"


def test_smoke_check_summary_has_error_escalation() -> None:
    """check_summary must have a result.error condition routing to a non-done step."""
    recipe = load_recipe(builtin_recipes_dir() / "smoke-test.yaml")
    step = recipe.steps["check_summary"]
    error_routes = [
        c.route
        for c in step.on_result.conditions
        if c.when is not None and "result.error" in c.when
    ]
    assert error_routes, "check_summary must have a result.error condition"
    assert all(r != "done" for r in error_routes), (
        f"check_summary result.error must not route to done; got {error_routes}"
    )


# ---------------------------------------------------------------------------
# SKILL.md emit instruction tests (1b, 1c, 1d)
# ---------------------------------------------------------------------------


def test_audit_impl_skill_md_emits_verdict_and_remediation_path() -> None:
    """1b: audit-impl SKILL.md must contain verdict= and remediation_path= emit lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills" / "audit-impl" / "SKILL.md").read_text()
    assert "verdict=" in content, "audit-impl SKILL.md missing 'verdict=' emit line"
    assert "remediation_path=" in content, (
        "audit-impl SKILL.md missing 'remediation_path=' emit line"
    )


def test_review_approach_skill_md_emits_review_path() -> None:
    """1c: review-approach SKILL.md must contain review_path= emit line."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills" / "review-approach" / "SKILL.md").read_text()
    assert "review_path=" in content, "review-approach SKILL.md missing 'review_path=' emit line"


def test_make_groups_skill_md_emits_group_files() -> None:
    """1d: make-groups SKILL.md must contain group_files=, groups_path=, manifest_path= lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills" / "make-groups" / "SKILL.md").read_text()
    assert "group_files=" in content, "make-groups SKILL.md missing 'group_files=' emit line"
    assert "groups_path=" in content, "make-groups SKILL.md missing 'groups_path=' emit line"
    assert "manifest_path=" in content, "make-groups SKILL.md missing 'manifest_path=' emit line"


# ---------------------------------------------------------------------------
# Bundled recipe uncaptured-handoff-consumer rule (1i)
# ---------------------------------------------------------------------------


def test_bundled_recipes_pass_uncaptured_handoff_consumer() -> None:
    """1i: all bundled recipes must produce zero uncaptured-handoff-consumer findings."""
    for yaml_file in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_file)
        findings = run_semantic_rules(recipe)
        handoff_findings = [f for f in findings if f.rule == "uncaptured-handoff-consumer"]
        assert not handoff_findings, f"{yaml_file.name}: {handoff_findings}"


# ---------------------------------------------------------------------------
# PR Review Loop integration tests (T_RP*)
# ---------------------------------------------------------------------------


class TestReviewPrRecipeIntegration:
    @pytest.fixture(
        scope="class",
        params=[
            "implementation.yaml",
            "implementation-groups.yaml",
            "audit-and-fix.yaml",
            "remediation.yaml",
        ],
    )
    def recipe(self, request: pytest.FixtureRequest) -> object:
        return load_recipe(builtin_recipes_dir() / request.param)

    def test_open_pr_step_routes_to_review_pr(self, recipe: object) -> None:
        """T_RP1: open_pr_step.on_success must be review_pr in all four recipes."""
        assert recipe.steps["open_pr_step"].on_success == "review_pr"  # type: ignore[attr-defined]

    def test_review_pr_step_exists_and_is_run_skill(self, recipe: object) -> None:
        """T_RP2: review_pr step exists and uses run_skill tool."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.tool == "run_skill"

    def test_review_pr_skipped_when_open_pr_false(self, recipe: object) -> None:
        """T_RP3: review_pr is gated by inputs.open_pr (skip_when_false)."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.skip_when_false == "inputs.open_pr"

    def test_review_pr_routes_to_ci_watch_on_success(self, recipe: object) -> None:
        """T_RP4: review_pr has on_result with catch-all route to ci_watch."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        default_conditions = [
            c for c in step.on_result.conditions if c.when is None or c.when == "true"
        ]
        assert any(c.route == "ci_watch" for c in default_conditions)

    def test_review_pr_captures_verdict(self, recipe: object) -> None:
        """T_RP4b: review_pr captures the verdict output from the skill result."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert "verdict" in step.capture
        assert step.capture["verdict"] == "${{ result.verdict }}"

    def test_review_pr_changes_requested_routes_to_resolve_review(self, recipe: object) -> None:
        """T_RP4c: on_result routes changes_requested verdict to resolve_review."""
        step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        assert step.on_result is not None
        changes_conditions = [
            c for c in step.on_result.conditions if c.when and "changes_requested" in c.when
        ]
        assert any(c.route == "resolve_review" for c in changes_conditions)

    def test_review_pr_routes_to_resolve_review_on_failure(self, recipe: object) -> None:
        """T_RP5: review_pr.on_failure routes to resolve_review."""
        assert recipe.steps["review_pr"].on_failure == "resolve_review"  # type: ignore[attr-defined]

    def test_resolve_review_has_retries(self, recipe: object) -> None:
        """T_RP6: resolve_review has retries=2 matching resolve_ci pattern."""
        assert recipe.steps["resolve_review"].retries == 2  # type: ignore[attr-defined]

    def test_resolve_review_routes_to_re_push_review(self, recipe: object) -> None:
        """T_RP7: resolve_review.on_success routes to re_push_review."""
        assert recipe.steps["resolve_review"].on_success == "re_push_review"  # type: ignore[attr-defined]

    def test_re_push_review_routes_to_ci_watch(self, recipe: object) -> None:
        """T_RP8: re_push_review routes to ci_watch (one-shot review gate)."""
        assert recipe.steps["re_push_review"].on_success == "ci_watch"  # type: ignore[attr-defined]

    def test_ci_watch_present(self, recipe: object) -> None:
        """T_RP9: ci_watch step present in all four recipes."""
        assert "ci_watch" in recipe.steps  # type: ignore[attr-defined]

    def test_review_pr_needs_human_has_explicit_route(self, recipe: object) -> None:
        """needs_human must have a dedicated on_result route in every recipe."""
        review_pr_step = recipe.steps["review_pr"]  # type: ignore[attr-defined]
        explicit_conditions = [
            c.when
            for c in review_pr_step.on_result.conditions
            if c.when and "needs_human" in c.when and c.when.strip() != "true"
        ]
        assert len(explicit_conditions) >= 1, (
            "review_pr on_result must have an explicit condition for 'needs_human'. "
            "It must not silently fall through the catch-all."
        )

    def test_resolve_review_step_uses_correct_skill(self, recipe: object) -> None:
        """resolve_review step must invoke /autoskillit:resolve-review in all recipes."""
        resolve_step = recipe.steps["resolve_review"]  # type: ignore[attr-defined]
        skill_cmd = resolve_step.with_args.get("skill_command", "")
        assert "resolve-review" in skill_cmd and "resolve-failures" not in skill_cmd, (
            "resolve_review step must call /autoskillit:resolve-review, "
            f"not resolve-failures. Got: {skill_cmd}"
        )


def test_bundled_recipes_pass_unrouted_verdict_value_rule() -> None:
    """All bundled recipes must pass the unrouted-verdict-value semantic rule."""
    for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        verdict_errors = [f for f in findings if f.rule == "unrouted-verdict-value"]
        assert len(verdict_errors) == 0, (
            f"Recipe '{yaml_path.stem}' has unrouted verdict values: "
            + ", ".join(f.message for f in verdict_errors)
        )


def test_implementation_groups_has_ci_watch() -> None:
    """T_RP10: implementation-groups now has ci_watch (parity with other recipes)."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation-groups.yaml")
    assert "ci_watch" in recipe.steps
    assert "resolve_ci" in recipe.steps
    assert "re_push" in recipe.steps


# ---------------------------------------------------------------------------
# Confirm-cleanup gate tests
# ---------------------------------------------------------------------------


def _build_reverse_on_success(recipe) -> dict[str, list[str]]:
    """Build a reverse mapping: step_name → list of steps whose on_success points to it."""
    reverse: dict[str, list[str]] = {name: [] for name in recipe.steps}
    for name, step in recipe.steps.items():
        if step.on_success and step.on_success in recipe.steps:
            reverse[step.on_success].append(name)
    return reverse


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "audit-and-fix",
        "remediation",
        "implementation-groups",
        "pr-merge-pipeline",
    ],
)
def test_bundled_recipe_cleanup_uses_confirm(recipe_name: str) -> None:
    """Every bundled recipe that clones must use action:confirm before deleting clone.

    Verifies that at least one confirm step's on_success points directly to a remove_clone step.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    confirm_steps = [name for name, step in recipe.steps.items() if step.action == "confirm"]
    assert confirm_steps, f"{recipe_name} has no confirm step — cleanup is unguarded"

    # At least one confirm step must route directly to a remove_clone step on success
    for conf_name in confirm_steps:
        conf_step = recipe.steps[conf_name]
        if conf_step.on_success and conf_step.on_success in recipe.steps:
            target = recipe.steps[conf_step.on_success]
            if target.tool == "remove_clone":
                return  # Found a properly connected confirm → remove_clone pair

    raise AssertionError(
        f"{recipe_name}: confirm step(s) exist but none has on_success pointing to remove_clone"
    )


def test_no_bundled_recipe_auto_deletes_on_success() -> None:
    """No bundled recipe should call remove_clone(keep=false) directly from success path.

    Uses transitive predecessor checking: walks the on_success graph backwards from each
    remove_clone(keep=false) step to verify that every reachable ancestor (via on_success
    edges, not crossing confirm steps) is itself a confirm step.
    """
    for recipe_name in [
        "implementation",
        "audit-and-fix",
        "remediation",
        "implementation-groups",
        "pr-merge-pipeline",
    ]:
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        reverse_on_success = _build_reverse_on_success(recipe)

        for name, step in recipe.steps.items():
            if step.tool == "remove_clone":
                keep = (step.with_args or {}).get("keep", "false")
                if keep == "false":
                    # Walk backwards via on_success edges, stopping at confirm steps.
                    # Any non-confirm step found is unguarded — a violation.
                    violations: list[str] = []
                    visited: set[str] = set()
                    queue = [name]

                    while queue:
                        current = queue.pop(0)
                        for pred in reverse_on_success.get(current, []):
                            if pred in visited:
                                continue
                            visited.add(pred)
                            pred_step = recipe.steps[pred]
                            if pred_step.action == "confirm":
                                # Guarded — stop tracing further back through this branch
                                pass
                            else:
                                violations.append(pred)
                                queue.append(
                                    pred
                                )  # Continue tracing to find all unguarded ancestors

                    assert not violations, (
                        f"{recipe_name}: {name} (keep=false) is reachable via on_success "
                        f"from unguarded (non-confirm) steps: {violations}"
                    )
