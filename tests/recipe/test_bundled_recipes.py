"""Tests for structural assertions on individual bundled YAML recipe files."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.core import SKILL_TOOLS
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import analyze_dataflow, run_semantic_rules

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE_RECIPE = PROJECT_ROOT / ".autoskillit" / "recipes" / "smoke-test.yaml"


def _assert_ci_conflict_fix_on_context_limit(recipe) -> None:
    """Shared assertion: ci_conflict_fix must abort via release_issue_failure on context limit."""
    step = recipe.steps["ci_conflict_fix"]
    assert step.on_context_limit == "release_issue_failure", (
        "ci_conflict_fix is advisory; an incomplete conflict fix cannot be safely "
        "pushed — abort via release_issue_failure"
    )


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
        """open_pr_step.on_success routes to extract_pr_number before review_pr."""
        open_pr_step = recipe.steps["open_pr_step"]
        assert open_pr_step.on_success == "extract_pr_number", (
            "open_pr_step must route to extract_pr_number"
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
        """T_CI2: ci_watch on_success -> check_merge_queue; on_failure -> detect_ci_conflict."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "check_merge_queue"
        assert step.on_failure == "detect_ci_conflict"
        assert "release_issue_success" in recipe.steps

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
        """T_CI8: open_pr_step.on_success routes to extract_pr_number before review_pr."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "extract_pr_number", (
            "open_pr_step must route to extract_pr_number"
        )

    def test_ip_ci_watch_routes_failure_to_conflict_gate(self, recipe) -> None:
        """ci_watch.on_failure must route to detect_ci_conflict, not directly to diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_failure == "detect_ci_conflict"

    def test_ip_detect_ci_conflict_exists(self, recipe) -> None:
        assert "detect_ci_conflict" in recipe.steps
        step = recipe.steps["detect_ci_conflict"]
        assert step.tool == "run_cmd"

    def test_ip_detect_ci_conflict_uses_merge_base(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        cmd = (step.with_args or {}).get("cmd", "")
        assert "merge-base" in cmd or "is-ancestor" in cmd

    def test_ip_detect_ci_conflict_routing(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.on_success == "ci_conflict_fix"
        assert step.on_failure == "diagnose_ci"

    def test_ip_ci_conflict_fix_exists(self, recipe) -> None:
        assert "ci_conflict_fix" in recipe.steps
        step = recipe.steps["ci_conflict_fix"]
        assert step.tool == "run_skill"
        skill_cmd = (step.with_args or {}).get("skill_command", "")
        assert "resolve-merge-conflicts" in skill_cmd

    def test_ip_ci_conflict_fix_routing(self, recipe) -> None:
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

    def test_ip_detect_ci_conflict_skip_when_false(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_ip_ci_conflict_fix_skip_when_false(self, recipe) -> None:
        step = recipe.steps["ci_conflict_fix"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_ip_review_step_has_skip_when_false(self, recipe) -> None:
        """REQ-C7-02: review step must declare skip_when_false: inputs.review_approach."""
        step = recipe.steps["review"]
        assert step.skip_when_false == "inputs.review_approach", (
            "review step must declare skip_when_false: inputs.review_approach — "
            "the skip intent is already in the note: field but not schema-enforced"
        )

    def test_implementation_review_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.on_context_limit == "verify", (
            "review is advisory (skip_when_false); on context limit it should skip to "
            "verify, not abort via on_failure"
        )

    def test_implementation_review_step_has_retries(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.retries >= 1

    def test_ip_audit_impl_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["audit_impl"]
        assert step.on_context_limit == "escalate_stop", (
            "audit_impl is a merge gate; a context-exhausted audit cannot provide "
            "a valid verdict — aborting via escalate_stop is correct"
        )

    def test_ip_open_pr_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["open_pr_step"]
        assert step.on_context_limit == "release_issue_failure", (
            "open_pr_step is advisory (skip_when_false); on context limit the pipeline "
            "cannot determine PR state — release the issue via release_issue_failure"
        )

    def test_ip_ci_conflict_fix_has_on_context_limit(self, recipe) -> None:
        _assert_ci_conflict_fix_on_context_limit(recipe)


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
        """T_CI2: ci_watch on_success -> check_merge_queue; on_failure -> detect_ci_conflict."""  # noqa: E501
        step = recipe.steps["ci_watch"]
        assert step.on_success == "check_merge_queue"
        assert step.on_failure == "detect_ci_conflict"
        assert "release_issue_success" in recipe.steps

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

    def test_ig_ci_watch_routes_failure_to_conflict_gate(self, recipe) -> None:
        """ci_watch.on_failure must route to detect_ci_conflict, not directly to diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_failure == "detect_ci_conflict"

    def test_ig_detect_ci_conflict_exists(self, recipe) -> None:
        assert "detect_ci_conflict" in recipe.steps
        step = recipe.steps["detect_ci_conflict"]
        assert step.tool == "run_cmd"

    def test_ig_detect_ci_conflict_uses_merge_base(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        cmd = (step.with_args or {}).get("cmd", "")
        assert "merge-base" in cmd or "is-ancestor" in cmd

    def test_ig_detect_ci_conflict_routing(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.on_success == "ci_conflict_fix"
        assert step.on_failure == "diagnose_ci"

    def test_ig_ci_conflict_fix_exists(self, recipe) -> None:
        assert "ci_conflict_fix" in recipe.steps
        step = recipe.steps["ci_conflict_fix"]
        assert step.tool == "run_skill"
        skill_cmd = (step.with_args or {}).get("skill_command", "")
        assert "resolve-merge-conflicts" in skill_cmd

    def test_ig_ci_conflict_fix_routing(self, recipe) -> None:
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

    def test_ig_detect_ci_conflict_skip_when_false(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_ig_ci_conflict_fix_skip_when_false(self, recipe) -> None:
        step = recipe.steps["ci_conflict_fix"]
        assert step.skip_when_false == "inputs.open_pr"

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

    def test_ig_open_pr_step_routes_to_extract_pr_number(self, recipe) -> None:
        """REQ-C7-01: open_pr_step must route to extract_pr_number (not review_pr directly)."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "extract_pr_number", (
            "open_pr_step must route to extract_pr_number so pr_number is available "
            "for enable_auto_merge and wait_for_queue"
        )

    def test_ig_ci_watch_routes_to_check_merge_queue(self, recipe) -> None:
        """REQ-C7-01: ci_watch.on_success must route to check_merge_queue (not release_issue_success)."""  # noqa: E501
        step = recipe.steps["ci_watch"]
        assert step.on_success == "check_merge_queue", (
            "ci_watch must route to check_merge_queue so the PR can enter the merge queue. "
            "Routing directly to release_issue_success skips the queue lifecycle entirely."
        )

    def test_ig_check_merge_queue_step_exists(self, recipe) -> None:
        """REQ-C7-01: check_merge_queue step must exist."""
        assert "check_merge_queue" in recipe.steps
        step = recipe.steps["check_merge_queue"]
        assert step.tool == "run_cmd"
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

    def test_ig_review_step_has_skip_when_false(self, recipe) -> None:
        """REQ-C7-02: review step must declare skip_when_false: inputs.review_approach."""
        step = recipe.steps["review"]
        assert step.skip_when_false == "inputs.review_approach", (
            "review step must declare skip_when_false: inputs.review_approach to make the "
            "skip intent schema-enforced. Currently it is prose-only in the note: field."
        )

    def test_ig_review_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.on_context_limit == "verify", (
            "review is advisory (skip_when_false); on context limit it should skip to "
            "verify, not abort via on_failure"
        )

    def test_ig_review_step_has_retries(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.retries >= 1

    def test_ig_audit_impl_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["audit_impl"]
        assert step.on_context_limit == "escalate_stop", (
            "audit_impl is a merge gate; a context-exhausted audit cannot provide "
            "a valid verdict — aborting via escalate_stop is correct"
        )

    def test_ig_open_pr_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["open_pr_step"]
        assert step.on_context_limit == "release_issue_failure", (
            "open_pr_step is advisory (skip_when_false); on context limit the pipeline "
            "cannot determine PR state — release the issue via release_issue_failure"
        )

    def test_ig_ci_conflict_fix_has_on_context_limit(self, recipe) -> None:
        _assert_ci_conflict_fix_on_context_limit(recipe)


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
        """T_CI2: ci_watch on_success -> check_merge_queue; on_failure -> detect_ci_conflict."""
        step = recipe.steps["ci_watch"]
        assert step.on_success == "check_merge_queue"
        assert step.on_failure == "detect_ci_conflict"
        assert "release_issue_success" in recipe.steps

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
        """T_CI8: open_pr_step.on_success routes to extract_pr_number before review_pr."""
        step = recipe.steps["open_pr_step"]
        assert step.on_success == "extract_pr_number", (
            "open_pr_step must route to extract_pr_number"
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

    def test_if_ci_watch_routes_failure_to_conflict_gate(self, recipe) -> None:
        """ci_watch.on_failure must route to detect_ci_conflict, not directly to diagnose_ci."""
        step = recipe.steps["ci_watch"]
        assert step.on_failure == "detect_ci_conflict"

    def test_if_detect_ci_conflict_exists(self, recipe) -> None:
        assert "detect_ci_conflict" in recipe.steps
        step = recipe.steps["detect_ci_conflict"]
        assert step.tool == "run_cmd"

    def test_if_detect_ci_conflict_uses_merge_base(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        cmd = (step.with_args or {}).get("cmd", "")
        assert "merge-base" in cmd or "is-ancestor" in cmd

    def test_if_detect_ci_conflict_routing(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.on_success == "ci_conflict_fix"
        assert step.on_failure == "diagnose_ci"

    def test_if_ci_conflict_fix_exists(self, recipe) -> None:
        assert "ci_conflict_fix" in recipe.steps
        step = recipe.steps["ci_conflict_fix"]
        assert step.tool == "run_skill"
        skill_cmd = (step.with_args or {}).get("skill_command", "")
        assert "resolve-merge-conflicts" in skill_cmd

    def test_if_ci_conflict_fix_routing(self, recipe) -> None:
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

    def test_if_detect_ci_conflict_skip_when_false(self, recipe) -> None:
        step = recipe.steps["detect_ci_conflict"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_if_ci_conflict_fix_skip_when_false(self, recipe) -> None:
        step = recipe.steps["ci_conflict_fix"]
        assert step.skip_when_false == "inputs.open_pr"

    def test_if_review_step_has_skip_when_false(self, recipe) -> None:
        """REQ-C7-02: review step in remediation.yaml must declare skip_when_false."""
        step = recipe.steps["review"]
        assert step.skip_when_false == "inputs.review_approach", (
            "review step must declare skip_when_false: inputs.review_approach — "
            "the skip intent is already in the note: field but not schema-enforced"
        )

    def test_if_review_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.on_context_limit == "dry_walkthrough", (
            "review is advisory (skip_when_false); on context limit it should skip to "
            "dry_walkthrough, not abort via on_failure"
        )

    def test_if_review_step_has_retries(self, recipe) -> None:
        step = recipe.steps["review"]
        assert step.retries >= 1, (
            "review step should allow at least one retry before routing to on_context_limit"
        )

    def test_if_audit_impl_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["audit_impl"]
        assert step.on_context_limit == "escalate_stop", (
            "audit_impl is a merge gate; a context-exhausted audit cannot provide "
            "a valid verdict — aborting via escalate_stop is correct"
        )

    def test_if_open_pr_step_has_on_context_limit(self, recipe) -> None:
        step = recipe.steps["open_pr_step"]
        assert step.on_context_limit == "release_issue_failure", (
            "open_pr_step is advisory (skip_when_false); on context limit the pipeline "
            "cannot determine PR state — release the issue via release_issue_failure"
        )

    def test_if_ci_conflict_fix_has_on_context_limit(self, recipe) -> None:
        _assert_ci_conflict_fix_on_context_limit(recipe)


# ---------------------------------------------------------------------------
# TestSmokeTestStructure
# ---------------------------------------------------------------------------


class TestSmokeTestStructure:
    """Structural assertions for the smoke-test.yaml recipe steps."""

    @pytest.fixture()
    def smoke_yaml(self) -> dict:
        return yaml.safe_load(SMOKE_RECIPE.read_text())

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


def test_bundled_recipes_diagrams_dir_exists() -> None:
    """Diagrams directory exists for bundled recipes."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "recipes" / "diagrams").is_dir()


# ---------------------------------------------------------------------------
# Two-tier failure model tests
# ---------------------------------------------------------------------------


def test_all_predicate_steps_have_on_failure() -> None:
    """Every tool/python step with on_result.conditions must declare on_failure."""
    paths = {
        "implementation": builtin_recipes_dir() / "implementation.yaml",
        "remediation": builtin_recipes_dir() / "remediation.yaml",
        "smoke-test": SMOKE_RECIPE,
    }
    for recipe_name, recipe_path in paths.items():
        recipe = load_recipe(recipe_path)
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
    assert impl.steps["audit_impl"].on_failure == "escalate_stop"
    assert rem.steps["audit_impl"].on_failure == "escalate_stop"


@pytest.mark.parametrize(
    "recipe_name,yaml_name",
    [
        ("implementation", "implementation.yaml"),
        ("remediation", "remediation.yaml"),
        ("merge-prs", "merge-prs.yaml"),
        ("implementation-groups", "implementation-groups.yaml"),
    ],
)
def test_audit_ingredient_defaults_to_false(recipe_name: str, yaml_name: str) -> None:
    """audit must default to 'false' (OFF) in all recipes — opt-in, not opt-out."""
    recipe = load_recipe(builtin_recipes_dir() / yaml_name)
    audit_ing = recipe.ingredients.get("audit")
    assert audit_ing is not None, f"{recipe_name}: 'audit' ingredient not found"
    assert audit_ing.default == "false", (
        f"{recipe_name}: audit.default must be 'false' (OFF by default), got {audit_ing.default!r}"
    )


def test_smoke_check_summary_has_error_escalation() -> None:
    """check_summary must have a result.error condition routing to a non-done step."""
    recipe = load_recipe(SMOKE_RECIPE)
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
    """1b: audit-impl SKILL.md must contain verdict and remediation_path emit lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "audit-impl" / "SKILL.md").read_text()
    assert "verdict = " in content, "audit-impl SKILL.md missing 'verdict = ' emit line"
    assert "remediation_path = " in content, (
        "audit-impl SKILL.md missing 'remediation_path = ' emit line"
    )


def test_review_approach_skill_md_emits_review_path() -> None:
    """1c: review-approach SKILL.md must contain review_path emit line."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "review-approach" / "SKILL.md").read_text()
    assert "review_path = " in content, (
        "review-approach SKILL.md missing 'review_path = ' emit line"
    )


def test_make_groups_skill_md_emits_group_files() -> None:
    """1d: make-groups SKILL.md must contain group_files, groups_path, manifest_path lines."""
    from autoskillit.core.paths import pkg_root

    content = (pkg_root() / "skills_extended" / "make-groups" / "SKILL.md").read_text()
    assert "group_files = " in content, "make-groups SKILL.md missing 'group_files = ' emit line"
    assert "groups_path = " in content, "make-groups SKILL.md missing 'groups_path = ' emit line"
    assert "manifest_path = " in content, (
        "make-groups SKILL.md missing 'manifest_path = ' emit line"
    )


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
            "remediation.yaml",
        ],
    )
    def recipe(self, request: pytest.FixtureRequest) -> object:
        return load_recipe(builtin_recipes_dir() / request.param)

    def test_open_pr_step_routes_to_review_pr(self, recipe: object) -> None:
        """T_RP1: open_pr_step.on_success routes per-recipe to the correct next step.

        All queue-aware recipes (implementation, remediation, implementation-groups) insert
        extract_pr_number between open_pr_step and review_pr to capture the PR number for
        merge queue support.
        """
        _expected: dict[str, str] = {
            "implementation": "extract_pr_number",
            "remediation": "extract_pr_number",
            "implementation-groups": "extract_pr_number",
        }
        recipe_name = recipe.name  # type: ignore[attr-defined]
        expected = _expected[recipe_name]
        on_success = recipe.steps["open_pr_step"].on_success  # type: ignore[attr-defined]
        assert on_success == expected, (
            f"{recipe_name}: open_pr_step.on_success must be {expected!r}, got {on_success!r}"
        )

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


def test_telemetry_before_open_pr_rule_not_in_registry() -> None:
    """The telemetry-before-open-pr rule must not be in the rule registry.

    This rule was removed because open-pr now self-retrieves token telemetry
    from disk using cwd_filter (Step 0b). If this test fails, the rule was
    re-added to the registry and would silently fire on bundled production recipes.
    """
    import autoskillit.recipe  # noqa: F401 — triggers rule registration
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule_names = {spec.name for spec in _RULE_REGISTRY}
    assert "telemetry-before-open-pr" not in rule_names, (
        "telemetry-before-open-pr was re-added to the registry; "
        "open-pr self-retrieves token telemetry via cwd_filter — "
        "this rule is no longer needed and must not be registered"
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
# TestBaseBranchDefaults
# ---------------------------------------------------------------------------


class TestBaseBranchDefaults:
    @pytest.mark.parametrize(
        "recipe_name",
        [
            "implementation",
            "remediation",
            "implementation-groups",
            "merge-prs",
        ],
    )
    def test_recipe_base_branch_auto_detects(self, recipe_name: str) -> None:
        """Non-exempt bundled recipes must use auto-detect for base_branch."""
        recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
        assert recipe.ingredients["base_branch"].default == "", (
            f"{recipe_name}.yaml: base_branch must use auto-detect (default: '')"
        )

    def test_smoke_test_base_branch_remains_main(self) -> None:
        """smoke-test.yaml must keep base_branch default 'main' — isolated scratch repo context."""
        recipe = load_recipe(SMOKE_RECIPE)
        assert recipe.ingredients["base_branch"].default == "main", (
            "smoke-test.yaml creates a fresh git repo initialized with 'main' — "
            "its base_branch default must stay 'main'"
        )


# ---------------------------------------------------------------------------
# TestImplementationRecipeMergeQueueRule
# ---------------------------------------------------------------------------


class TestImplementationRecipeMergeQueueRule:
    """implementation.yaml kitchen_rules must reference merge queue detection."""

    @pytest.fixture(scope="class")
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation.yaml")

    def test_kitchen_rules_mention_check_merge_queue(self, recipe) -> None:
        all_rules = " ".join(recipe.kitchen_rules)
        assert "check_merge_queue" in all_rules, (
            "implementation.yaml kitchen_rules must reference check_merge_queue"
        )
        assert "MERGE ROUTING" in all_rules, (
            "implementation.yaml kitchen_rules must contain a MERGE ROUTING rule"
        )

    def test_kitchen_rules_prohibit_direct_gh_pr_merge(self, recipe) -> None:
        # Find the specific rule that mentions "gh pr merge" and check for
        # prohibition language within that rule, not across all rules.
        merge_rules = [r for r in recipe.kitchen_rules if "gh pr merge" in r]
        assert merge_rules, (
            "implementation.yaml kitchen_rules must contain a rule mentioning 'gh pr merge'"
        )
        has_prohibition = any(
            any(phrase in rule.lower() for phrase in ("never", "prohibited", "do not"))
            for rule in merge_rules
        )
        assert has_prohibition, (
            "implementation.yaml kitchen_rules must explicitly prohibit calling "
            "gh pr merge directly outside of recipe steps"
        )


# ---------------------------------------------------------------------------
# WF7: build_recipe_graph emits zero warnings for all bundled recipes
# ---------------------------------------------------------------------------

import structlog.testing  # noqa: E402

from autoskillit.recipe._analysis import build_recipe_graph  # noqa: E402

_BUNDLED_RECIPE_PATHS = sorted(builtin_recipes_dir().glob("*.yaml"))


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_bundled_recipes_emit_no_graph_warnings(recipe_path):
    """WF7: build_recipe_graph emits zero warnings for all bundled recipes."""
    recipe = load_recipe(recipe_path)
    with structlog.testing.capture_logs() as cap_logs:
        build_recipe_graph(recipe)
    warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
    assert warning_events == [], (
        f"build_recipe_graph emitted {len(warning_events)} warnings for "
        f"{recipe_path.name}: {warning_events}"
    )


@pytest.mark.parametrize("recipe_path", _BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
def test_all_advisory_run_skill_steps_have_on_context_limit(recipe_path):
    """
    Every run_skill step with skip_when_false must declare on_context_limit.
    A step that can be skipped by configuration must also be skippable on context limit.
    """
    recipe = load_recipe(recipe_path)
    violations = [
        name
        for name, step in recipe.steps.items()
        if step.tool in SKILL_TOOLS
        and step.skip_when_false is not None
        and step.on_context_limit is None
    ]
    assert violations == [], (
        f"Advisory run_skill steps in {recipe_path.name} missing on_context_limit: "
        f"{violations}. Set on_context_limit to the appropriate skip/recovery step."
    )


# ---------------------------------------------------------------------------
# Cross-recipe review-pr pre-computation tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs"],
)
def test_review_pr_step_passes_annotated_diff_inputs(recipe_name: str) -> None:
    """Every review-pr invocation must pass annotated_diff_path= and hunk_ranges_path=
    in skill_command, or have a reachable annotate_pr_diff predecessor step."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    # Find review-pr steps
    review_steps = [
        (name, step)
        for name, step in recipe.steps.items()
        if step.tool in SKILL_TOOLS
        and "review-pr" in step.with_args.get("skill_command", "")
    ]
    assert review_steps, f"No review-pr step found in {recipe_name}.yaml"
    for step_name, step in review_steps:
        cmd = step.with_args.get("skill_command", "")
        has_inline = "annotated_diff_path=" in cmd and "hunk_ranges_path=" in cmd
        has_predecessor = any(
            s.with_args.get("callable", "") == "autoskillit.smoke_utils.annotate_pr_diff"
            for s in recipe.steps.values()
            if s.tool == "run_python"
        )
        assert has_inline or has_predecessor, (
            f"{recipe_name}.yaml: step '{step_name}' invokes review-pr but neither "
            f"passes annotated_diff_path=/hunk_ranges_path= inline nor has an "
            f"annotate_pr_diff predecessor step"
        )


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation", "remediation", "implementation-groups", "merge-prs"],
)
def test_annotate_pr_diff_captures_both_paths(recipe_name: str) -> None:
    """The annotate_pr_diff step must capture annotated_diff_path and hunk_ranges_path."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    annotate_steps = [
        (name, step)
        for name, step in recipe.steps.items()
        if step.tool == "run_python"
        and step.with_args.get("callable", "") == "autoskillit.smoke_utils.annotate_pr_diff"
    ]
    assert annotate_steps, f"No annotate_pr_diff step found in {recipe_name}.yaml"
    for step_name, step in annotate_steps:
        assert step.capture is not None, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' has no capture block"
        )
        assert "annotated_diff_path" in step.capture, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' must capture "
            f"annotated_diff_path"
        )
        assert "hunk_ranges_path" in step.capture, (
            f"{recipe_name}.yaml: annotate_pr_diff step '{step_name}' must capture "
            f"hunk_ranges_path"
        )


# ---------------------------------------------------------------------------
# TestRunModeIngredient
# ---------------------------------------------------------------------------


class TestRunModeIngredient:
    """REQ-INGREDIENT-001 through REQ-INGREDIENT-005: run_mode ingredient in multi-issue recipes."""  # noqa: E501

    @pytest.fixture(scope="class")
    def impl_recipe(self):
        return load_recipe(builtin_recipes_dir() / "implementation.yaml")

    @pytest.fixture(scope="class")
    def remed_recipe(self):
        return load_recipe(builtin_recipes_dir() / "remediation.yaml")

    def test_implementation_has_run_mode_ingredient(self, impl_recipe) -> None:
        """REQ-INGREDIENT-001: implementation.yaml declares run_mode ingredient."""
        assert "run_mode" in impl_recipe.ingredients, (
            "implementation.yaml must declare run_mode ingredient"
        )

    def test_implementation_run_mode_default_is_sequential(self, impl_recipe) -> None:
        """REQ-INGREDIENT-002: run_mode defaults to 'sequential'."""
        ing = impl_recipe.ingredients["run_mode"]
        assert ing.default == "sequential", (
            "implementation.yaml run_mode must default to 'sequential'"
        )

    def test_implementation_run_mode_description_mentions_parallel(self, impl_recipe) -> None:
        """REQ-INGREDIENT-001: description must document 'parallel' as a valid option."""
        ing = impl_recipe.ingredients["run_mode"]
        assert "parallel" in ing.description.lower(), (
            "run_mode description must mention 'parallel' as an option"
        )

    def test_remediation_has_run_mode_ingredient(self, remed_recipe) -> None:
        """REQ-INGREDIENT-001: remediation.yaml declares run_mode ingredient."""
        assert "run_mode" in remed_recipe.ingredients, (
            "remediation.yaml must declare run_mode ingredient"
        )

    def test_remediation_run_mode_default_is_sequential(self, remed_recipe) -> None:
        """REQ-INGREDIENT-002: run_mode defaults to 'sequential'."""
        ing = remed_recipe.ingredients["run_mode"]
        assert ing.default == "sequential", (
            "remediation.yaml run_mode must default to 'sequential'"
        )

    def test_remediation_run_mode_description_mentions_parallel(self, remed_recipe) -> None:
        """REQ-INGREDIENT-001: description must document 'parallel' as a valid option."""
        ing = remed_recipe.ingredients["run_mode"]
        assert "parallel" in ing.description.lower(), (
            "run_mode description must mention 'parallel' as an option"
        )


def test_no_bare_temp_paths_in_bundled_recipe_notes() -> None:
    """No bundled recipe YAML should reference temp/ without .autoskillit/ prefix.

    Bare temp/ references are incorrect; all project-local temp output must be
    rooted under .autoskillit/temp/ per CLAUDE.md §3.2.
    """
    import re

    recipes_dir = builtin_recipes_dir()
    bare_temp = re.compile(r"(?<!\.autoskillit/)temp/")

    violations: list[str] = []
    for yaml_file in sorted(recipes_dir.glob("*.yaml")):
        text = yaml_file.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if bare_temp.search(line):
                violations.append(f"{yaml_file.name}:{lineno}: {line.strip()}")

    assert not violations, (
        "Bundled recipe YAML files contain bare temp/ path references.\n"
        "Replace with .autoskillit/temp/ per CLAUDE.md §3.2:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# TestDeferCleanupRecipeStructure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_recipe_has_no_defer_cleanup_ingredient(recipe_name: str) -> None:
    """Recipes must not declare 'defer_cleanup' — that design is removed."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "defer_cleanup" not in recipe.ingredients, (
        f"{recipe_name}.yaml must not declare 'defer_cleanup'"
    )


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_recipe_has_no_registry_path_ingredient(recipe_name: str) -> None:
    """Recipes must not declare 'registry_path' — replaced by a well-known default."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "registry_path" not in recipe.ingredients, (
        f"{recipe_name}.yaml must not declare 'registry_path'"
    )


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_recipe_has_no_interactive_cleanup_steps(recipe_name: str) -> None:
    """Recipes must not have confirm_cleanup or delete_clone — these blocked unattended runs."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "confirm_cleanup" not in recipe.steps, (
        f"{recipe_name}.yaml must not have 'confirm_cleanup' step"
    )
    assert "delete_clone" not in recipe.steps, (
        f"{recipe_name}.yaml must not have 'delete_clone' step"
    )


@pytest.mark.parametrize(
    "recipe_name", ["implementation", "remediation", "implementation-groups", "merge-prs"]
)
def test_recipe_has_unconditional_register_steps(recipe_name: str) -> None:
    """register_clone_success routes to done; register_clone_failure routes to escalate_stop."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    assert "register_clone_success" in recipe.steps
    assert "register_clone_failure" in recipe.steps
    s = recipe.steps["register_clone_success"]
    f = recipe.steps["register_clone_failure"]
    assert s.on_success == "done"
    assert f.on_success == "escalate_stop"
    assert f.on_failure == "escalate_stop"
    assert "check_defer_cleanup" not in recipe.steps
    assert "check_defer_on_failure" not in recipe.steps


@pytest.mark.parametrize(
    "recipe_name",
    ["implementation.yaml", "implementation-groups.yaml", "remediation.yaml"],
)
def test_re_push_steps_have_force_true(recipe_name: str) -> None:
    """All re_push/* steps must have force='true'.

    Post-rebase push requires --force-with-lease.
    """
    recipe = load_recipe(builtin_recipes_dir() / recipe_name)
    for step_name in (
        "re_push",
        "re_push_queue_fix",
        "re_push_direct_fix",
        "re_push_immediate_fix",
    ):
        assert step_name in recipe.steps, f"Expected step {step_name!r} in {recipe_name}"
        step = recipe.steps[step_name]
        assert step.tool == "push_to_remote"
        assert step.with_args.get("force") == "true", (
            f"{step_name} in {recipe_name} must include force='true' — "
            "post-rebase push requires --force-with-lease"
        )


class TestResearchRecipeStructure:
    @pytest.fixture
    def recipe(self):
        return load_recipe(builtin_recipes_dir() / "research.yaml")

    def test_research_has_review_pr_ingredient(self, recipe) -> None:
        """research.yaml must declare a review_pr ingredient with default 'false'."""
        assert "review_pr" in recipe.ingredients, (
            "research.yaml must declare a 'review_pr' ingredient to gate the optional "
            "review-research-pr step"
        )
        assert recipe.ingredients["review_pr"].default == "false"

    def test_research_has_review_research_pr_step(self, recipe) -> None:
        """research.yaml must include a review_research_pr step."""
        assert "review_research_pr" in recipe.steps

    def test_research_review_step_skip_when_false(self, recipe) -> None:
        """review_research_pr step must use skip_when_false: inputs.review_pr."""
        step = recipe.steps["review_research_pr"]
        assert step.skip_when_false == "inputs.review_pr"

    def test_research_review_step_routes_to_begin_archival_on_any_outcome(self, recipe) -> None:
        """review_research_pr routes to begin_archival on failure and context limit."""
        step = recipe.steps["review_research_pr"]
        assert step.on_failure == "begin_archival"
        assert step.on_context_limit == "begin_archival"

    def test_research_no_issue_number_ingredient(self, recipe) -> None:
        """Removed: issue_number is the phase-2 gate."""
        assert "issue_number" not in recipe.ingredients

    def test_research_no_setup_phases_ingredient(self, recipe) -> None:
        """Removed: setup_phases toggle replaced by always-decompose."""
        assert "setup_phases" not in recipe.ingredients

    def test_research_no_check_phase_step(self, recipe) -> None:
        assert "check_phase" not in recipe.steps

    def test_research_no_phase1_done_step(self, recipe) -> None:
        assert "phase1_done" not in recipe.steps

    def test_research_no_open_plan_issue_step(self, recipe) -> None:
        assert "open_plan_issue" not in recipe.steps

    def test_research_no_save_experiment_plan_step(self, recipe) -> None:
        assert "save_experiment_plan" not in recipe.steps

    def test_research_no_check_setup_needed_step(self, recipe) -> None:
        assert "check_setup_needed" not in recipe.steps

    def test_research_no_implement_experiment_step(self, recipe) -> None:
        assert "implement_experiment" not in recipe.steps

    def test_research_has_issue_url_ingredient(self, recipe) -> None:
        assert "issue_url" in recipe.ingredients
        assert not recipe.ingredients["issue_url"].required

    def test_research_has_review_design_ingredient(self, recipe) -> None:
        assert "review_design" in recipe.ingredients
        assert recipe.ingredients["review_design"].default == "true"

    def test_research_has_review_design_step(self, recipe) -> None:
        assert "review_design" in recipe.steps

    def test_review_design_step_skip_when_false(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.skip_when_false == "inputs.review_design"

    def test_review_design_step_retries_and_exhausted(self, recipe) -> None:
        step = recipe.steps["review_design"]
        assert step.retries == 2
        assert step.on_exhausted == "create_worktree"

    def test_plan_experiment_routes_to_review_design(self, recipe) -> None:
        step = recipe.steps["plan_experiment"]
        assert step.on_success == "review_design"

    def test_review_design_on_result_routing(self, recipe) -> None:
        """review_design STOP verdict routes to resolve_design_review (not design_rejected)."""
        step = recipe.steps["review_design"]
        assert step.on_result is not None
        routes = {c.when: c.route for c in step.on_result.conditions if c.when}
        assert any("GO" in (w or "") for w in routes), "Missing GO route"
        go_route = next(c.route for c in step.on_result.conditions if c.when and "GO" in c.when)
        assert go_route == "create_worktree"
        revise_route = next(
            c.route for c in step.on_result.conditions if c.when and "REVISE" in c.when
        )
        assert revise_route == "revise_design"
        stop_route = next(
            c.route for c in step.on_result.conditions if c.when and "STOP" in c.when
        )
        assert stop_route == "resolve_design_review"

    def test_has_revise_design_step(self, recipe) -> None:
        assert "revise_design" in recipe.steps

    def test_has_design_rejected_step(self, recipe) -> None:
        assert "design_rejected" in recipe.steps

    def test_design_rejected_step_is_action_stop(self, recipe) -> None:
        """design_rejected must halt the pipeline (action=stop) with an explanatory message.

        Regression guard: changing action to 'route' would silently break
        the hard-halt guarantee for fundamentally flawed designs.
        """
        step = recipe.steps["design_rejected"]
        assert step.action == "stop"
        assert step.message, "design_rejected must have a non-empty message"
        assert "STOP" in step.message, (
            "design_rejected message must reference the STOP verdict for clarity."
        )

    def test_kitchen_rule_6_acknowledges_stop_verdict_exception(self, recipe) -> None:
        """Kitchen rule #6 must scope 'do not stop' to exhaustion/failure, not STOP verdict.

        The current blanket 'do not stop' language contradicts the design_rejected
        hard halt. The rule must explicitly note that verdict=STOP is the sole exception.
        """
        rule6 = recipe.kitchen_rules[5]  # 0-indexed; rule #6 is index 5
        rule6_lower = rule6.lower()
        assert "stop" in rule6_lower, (
            "Kitchen rule #6 must reference 'stop' in the verdict exception context."
        )
        assert "verdict" in rule6_lower or "exception" in rule6_lower, (
            "Kitchen rule #6 must acknowledge that verdict=STOP is an exception to "
            "'do not stop on exhaustion/failure'. Current language is overbroad and "
            "contradicts the design_rejected routing."
        )

    def test_has_resolve_design_review_step(self, recipe) -> None:
        """resolve_design_review step must be present in research.yaml."""
        assert "resolve_design_review" in recipe.steps

    def test_resolve_design_review_routes_revised_to_revise_design(self, recipe) -> None:
        """resolve_design_review routes resolution=revised back to revise_design."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        revised_route = next(
            c.route for c in step.on_result.conditions if c.when and "revised" in c.when
        )
        assert revised_route == "revise_design"

    def test_resolve_design_review_routes_failed_to_design_rejected(self, recipe) -> None:
        """resolve_design_review routes resolution=failed to design_rejected."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_result is not None
        failed_route = next(
            c.route for c in step.on_result.conditions if c.when and "failed" in c.when
        )
        assert failed_route == "design_rejected"

    def test_resolve_design_review_fallbacks_to_design_rejected(self, recipe) -> None:
        """resolve_design_review routes on_failure and on_context_limit to design_rejected."""
        step = recipe.steps["resolve_design_review"]
        assert step.on_failure == "design_rejected"
        assert step.on_context_limit == "design_rejected"

    def test_resolve_design_review_captures_revision_guidance(self, recipe) -> None:
        """resolve_design_review must capture revision_guidance for the revise_design loop."""
        step = recipe.steps["resolve_design_review"]
        assert "revision_guidance" in step.capture, (
            "resolve_design_review must capture revision_guidance so revise_design → "
            "plan_experiment picks up the triage-generated guidance"
        )

    def test_has_resolve_research_review_step(self, recipe) -> None:
        step = recipe.steps["resolve_research_review"]
        assert step.retries == 2
        assert step.on_exhausted == "begin_archival"
        assert step.on_success == "check_escalations"
        assert step.on_failure == "begin_archival"

    def test_has_re_push_research_step(self, recipe) -> None:
        assert "re_push_research" in recipe.steps

    def test_review_research_pr_has_on_result_routing(self, recipe) -> None:
        """review_research_pr routes changes_requested to resolve_research_review."""
        step = recipe.steps["review_research_pr"]
        assert step.on_result is not None
        matching = [
            c.route for c in step.on_result.conditions if "changes_requested" in (c.when or "")
        ]
        assert matching, "No condition with changes_requested"
        assert matching[0] == "resolve_research_review"

    def test_open_research_pr_is_run_skill(self, recipe) -> None:
        """open_research_pr changed from run_cmd to run_skill."""
        step = recipe.steps["open_research_pr"]
        assert step.tool == "run_skill"

    def test_open_research_pr_passes_report_path_first(self, recipe) -> None:
        """open_research_pr skill_command must pass report_path as first positional arg."""
        step = recipe.steps["open_research_pr"]
        cmd = step.with_args["skill_command"]
        report_idx = cmd.find("report_path")
        worktree_idx = cmd.find("worktree_path")
        assert report_idx != -1, "report_path not found in skill_command"
        assert worktree_idx != -1, "worktree_path not found in skill_command"
        assert report_idx < worktree_idx, (
            "report_path must be the first positional arg in open_research_pr skill_command"
        )

    def test_open_research_pr_passes_experiment_plan_path(self, recipe) -> None:
        """open_research_pr skill_command must pass experiment_plan_path."""
        step = recipe.steps["open_research_pr"]
        cmd = step.with_args["skill_command"]
        assert "experiment-plan" in cmd or "experiment_plan" in cmd, (
            "open_research_pr must pass experiment_plan_path to the skill"
        )

    def test_open_research_pr_captures_pr_url(self, recipe) -> None:
        """open_research_pr must capture pr_url for downstream use by review_research_pr."""
        step = recipe.steps["open_research_pr"]
        assert step.capture is not None, "open_research_pr must declare capture"
        assert "pr_url" in step.capture, "open_research_pr must capture pr_url"

    def test_research_validates_cleanly(self, recipe) -> None:
        """validate_recipe returns no errors on the simplified recipe."""
        from autoskillit.recipe.validator import validate_recipe

        errors = validate_recipe(recipe)
        assert errors == [], f"Validation errors: {errors}"

    def test_requires_packs_includes_exp_lens(self, recipe) -> None:
        assert "exp-lens" in recipe.requires_packs
        assert "research" in recipe.requires_packs

    def test_resolve_research_review_captures_needs_rerun(self, recipe) -> None:
        step = recipe.steps["resolve_research_review"]
        assert "needs_rerun" in step.capture
        assert "result.needs_rerun" in step.capture["needs_rerun"]

    def test_resolve_research_review_routes_to_check_escalations(self, recipe) -> None:
        step = recipe.steps["resolve_research_review"]
        assert step.on_success == "check_escalations"

    def test_check_escalations_step_exists(self, recipe) -> None:
        assert "check_escalations" in recipe.steps
        step = recipe.steps["check_escalations"]
        assert step.action == "route"

    def test_check_escalations_routes_rerun_on_needs_rerun(self, recipe) -> None:
        step = recipe.steps["check_escalations"]
        assert step.on_result is not None
        conditions = step.on_result.conditions
        rerun_route = next(
            (c for c in conditions if c.when and "needs_rerun" in c.when and "true" in c.when),
            None,
        )
        assert rerun_route is not None
        assert rerun_route.route == "re_run_experiment"

    def test_check_escalations_default_routes_to_push(self, recipe) -> None:
        step = recipe.steps["check_escalations"]
        conditions = step.on_result.conditions
        default = next((c for c in conditions if not c.when), None)
        assert default is not None
        assert default.route == "re_push_research"

    def test_re_run_experiment_step(self, recipe) -> None:
        assert "re_run_experiment" in recipe.steps
        step = recipe.steps["re_run_experiment"]
        assert step.tool == "run_skill"
        assert "--adjust" in step.with_args.get("skill_command", "")
        assert step.on_success == "re_write_report"

    def test_re_write_report_step(self, recipe) -> None:
        assert "re_write_report" in recipe.steps
        step = recipe.steps["re_write_report"]
        assert step.tool == "run_skill"
        assert step.on_success == "re_test"

    def test_re_test_step(self, recipe) -> None:
        assert "re_test" in recipe.steps
        step = recipe.steps["re_test"]
        assert step.tool == "test_check"
        assert step.on_success == "re_push_research"

    def test_revalidation_loop_all_paths_reach_begin_archival(self, recipe) -> None:
        """Every path from check_escalations reaches begin_archival."""
        for step_name in ("re_run_experiment", "re_write_report", "re_test"):
            step = recipe.steps[step_name]
            assert step.on_failure in ("begin_archival", "re_push_research")
        assert recipe.steps["re_push_research"].on_success == "begin_archival"

    # --- Archival phase tests ---

    def test_archival_begin_archival_step_exists(self, recipe) -> None:
        """begin_archival must be a route step gating archival on pr_url."""
        assert "begin_archival" in recipe.steps
        step = recipe.steps["begin_archival"]
        assert step.action == "route"

    def test_archival_begin_archival_routes_to_capture(self, recipe) -> None:
        """begin_archival routes to capture_experiment_branch when pr_url is truthy."""
        step = recipe.steps["begin_archival"]
        assert step.on_result is not None
        conditions = step.on_result.conditions
        truthy_route = next((c for c in conditions if c.when and "pr_url" in c.when), None)
        assert truthy_route is not None
        assert truthy_route.route == "capture_experiment_branch"

    def test_archival_begin_archival_default_to_complete(self, recipe) -> None:
        """begin_archival default route goes to research_complete."""
        step = recipe.steps["begin_archival"]
        conditions = step.on_result.conditions
        default = next((c for c in conditions if not c.when), None)
        assert default is not None
        assert default.route == "research_complete"

    def test_archival_capture_experiment_branch_step(self, recipe) -> None:
        """capture_experiment_branch captures experiment_branch from git rev-parse."""
        assert "capture_experiment_branch" in recipe.steps
        step = recipe.steps["capture_experiment_branch"]
        assert step.tool == "run_cmd"
        assert "experiment_branch" in step.capture
        assert step.on_success == "create_artifact_branch"
        assert step.on_failure == "research_complete"

    def test_archival_create_artifact_branch_step(self, recipe) -> None:
        """create_artifact_branch creates temp worktree and captures artifact_branch."""
        assert "create_artifact_branch" in recipe.steps
        step = recipe.steps["create_artifact_branch"]
        assert step.tool == "run_cmd"
        assert "artifact_branch" in step.capture
        assert step.on_success == "open_artifact_pr"
        assert step.on_failure == "research_complete"

    def test_archival_create_artifact_branch_uses_research_checkout(self, recipe) -> None:
        """create_artifact_branch must use git checkout <branch> -- research/ pattern."""
        step = recipe.steps["create_artifact_branch"]
        cmd = step.with_args["cmd"]
        assert "research/" in cmd, "Must checkout research/ directory from experiment branch"
        assert "worktree add" in cmd, "Must create a temporary worktree"

    def test_archival_open_artifact_pr_step(self, recipe) -> None:
        """open_artifact_pr creates the artifact-only PR and captures artifact_pr_url."""
        assert "open_artifact_pr" in recipe.steps
        step = recipe.steps["open_artifact_pr"]
        assert step.tool == "run_cmd"
        assert "artifact_pr_url" in step.capture
        assert step.on_success == "tag_experiment_branch"
        assert step.on_failure == "research_complete"

    def test_archival_tag_experiment_branch_step(self, recipe) -> None:
        """tag_experiment_branch creates annotated archive tag and captures archive_tag."""
        assert "tag_experiment_branch" in recipe.steps
        step = recipe.steps["tag_experiment_branch"]
        assert step.tool == "run_cmd"
        assert "archive_tag" in step.capture
        assert step.on_success == "close_experiment_pr"
        assert step.on_failure == "research_complete"

    def test_archival_tag_uses_archive_prefix(self, recipe) -> None:
        """Tag name must use archive/research/ prefix convention."""
        step = recipe.steps["tag_experiment_branch"]
        cmd = step.with_args["cmd"]
        assert "archive/research/" in cmd

    def test_archival_close_experiment_pr_step(self, recipe) -> None:
        """close_experiment_pr closes the original PR and routes to research_complete."""
        assert "close_experiment_pr" in recipe.steps
        step = recipe.steps["close_experiment_pr"]
        assert step.tool == "run_cmd"
        assert step.on_success == "research_complete"
        assert step.on_failure == "research_complete"

    def test_archival_close_pr_references_artifact_and_tag(self, recipe) -> None:
        """close_experiment_pr comment must reference both artifact PR and archive tag."""
        step = recipe.steps["close_experiment_pr"]
        cmd = step.with_args["cmd"]
        assert "artifact_pr_url" in cmd, "Must reference artifact PR URL"
        assert "archive_tag" in cmd, "Must reference archive tag"

    def test_archival_graceful_degradation(self, recipe) -> None:
        """Every archival step must route on_failure to research_complete."""
        archival_steps = [
            "capture_experiment_branch",
            "create_artifact_branch",
            "open_artifact_pr",
            "tag_experiment_branch",
            "close_experiment_pr",
        ]
        for name in archival_steps:
            step = recipe.steps[name]
            assert step.on_failure == "research_complete", (
                f"{name}.on_failure must be research_complete for graceful degradation"
            )

    def test_review_research_pr_routes_to_begin_archival(self, recipe) -> None:
        """review_research_pr non-changes verdicts and failures route to begin_archival."""
        step = recipe.steps["review_research_pr"]
        assert step.on_failure == "begin_archival"
        assert step.on_context_limit == "begin_archival"
        # Default on_result route
        conditions = step.on_result.conditions
        default = next((c for c in conditions if not c.when), None)
        assert default is not None
        assert default.route == "begin_archival"

    def test_resolve_research_review_routes_to_begin_archival(self, recipe) -> None:
        """resolve_research_review exhaustion and failure route to begin_archival."""
        step = recipe.steps["resolve_research_review"]
        assert step.on_exhausted == "begin_archival"
        assert step.on_failure == "begin_archival"

    def test_re_push_research_routes_to_begin_archival(self, recipe) -> None:
        """re_push_research routes to begin_archival."""
        step = recipe.steps["re_push_research"]
        assert step.on_success == "begin_archival"
        assert step.on_failure == "begin_archival"

    def test_create_worktree_copies_review_cycle_artifacts(self, recipe) -> None:
        """create_worktree must copy review-design dashboards and revision guidance."""
        step = recipe.steps["create_worktree"]
        cmd = step.with_args["cmd"]
        assert "review-cycles" in cmd, (
            "create_worktree must create artifacts/review-cycles/ subdirectory"
        )
        assert "evaluation_dashboard" in cmd and "review-cycles" in cmd, (
            "create_worktree must copy evaluation dashboards to review-cycles/"
        )
        assert "revision_guidance" in cmd and "review-cycles" in cmd, (
            "create_worktree must copy revision guidance to review-cycles/"
        )

    def test_create_worktree_copies_plan_version_artifacts(self, recipe) -> None:
        """create_worktree must copy intermediate plan versions."""
        step = recipe.steps["create_worktree"]
        cmd = step.with_args["cmd"]
        assert "plan-versions" in cmd, (
            "create_worktree must create artifacts/plan-versions/ subdirectory"
        )
        assert "experiment_plan" in cmd and "plan-versions" in cmd, (
            "create_worktree must copy plan versions to plan-versions/"
        )

    def test_commit_research_artifacts_step_exists(self, recipe) -> None:
        """A commit_research_artifacts step must exist to capture phase artifacts."""
        assert "commit_research_artifacts" in recipe.steps, (
            "research.yaml must have a commit_research_artifacts step for phase artifacts"
        )
        step = recipe.steps["commit_research_artifacts"]
        cmd = step.with_args["cmd"]
        assert "phase-groups" in cmd, "Must copy make-groups output"
        assert "phase-plans" in cmd, "Must copy make-plan output"

    def test_test_routes_to_commit_research_artifacts(self, recipe) -> None:
        """test step must route to commit_research_artifacts, not directly to push_branch."""
        step = recipe.steps["test"]
        assert step.on_success == "commit_research_artifacts", (
            "test.on_success must be commit_research_artifacts to capture phase artifacts"
            " before push"
        )

    def test_retest_routes_to_commit_research_artifacts(self, recipe) -> None:
        """retest step must route to commit_research_artifacts, not directly to push_branch."""
        step = recipe.steps["retest"]
        assert step.on_success == "commit_research_artifacts", (
            "retest.on_success must be commit_research_artifacts to capture phase artifacts"
            " before push"
        )

    def test_commit_research_artifacts_routes_to_push_branch(self, recipe) -> None:
        """commit_research_artifacts must route to push_branch on both success and failure."""
        step = recipe.steps["commit_research_artifacts"]
        assert step.on_success == "push_branch"
        assert step.on_failure == "push_branch"
