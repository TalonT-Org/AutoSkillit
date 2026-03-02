"""Tests for structural assertions on individual bundled YAML recipe files."""

from __future__ import annotations

import yaml
import pytest

from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import analyze_dataflow


# ---------------------------------------------------------------------------
# TestImplementationPipelineStructure
# ---------------------------------------------------------------------------


class TestImplementationPipelineStructure:
    @pytest.fixture(scope="class", autouse=True)
    def _load_recipe(self, request) -> None:
        request.cls.recipe = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")

    def test_ip1_group_step_captures_group_files(self) -> None:
        """T_IP1: group step has capture containing key group_files (not groups_path)."""
        assert "group_files" in self.recipe.steps["group"].capture
        assert "groups_path" not in self.recipe.steps["group"].capture

    def test_ip2_review_step_captures_review_path(self) -> None:
        """T_IP2: review step has capture containing key review_path."""
        assert "review_path" in self.recipe.steps["review"].capture

    def test_ip3_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_IP3: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_ip4_verify_step_references_context_review_path(self) -> None:
        """T_IP4: verify step with_args contains a reference to context.review_path."""
        verify_with = self.recipe.steps["verify"].with_args
        assert any("context.review_path" in str(v) for v in verify_with.values())

    def test_ip5_audit_impl_has_on_failure(self) -> None:
        """T_IP5: audit_impl must declare on_failure for tool-failure routing.
        The old assertion (on_failure is None) was wrong: on_result only fires
        when run_skill succeeds and returns a verdict. Tool-level failures
        require on_failure as a separate escape hatch.
        """
        step = self.recipe.steps["audit_impl"]
        assert step.on_success is None  # on_result is used; on_success remains absent
        assert step.on_failure is not None, (
            "audit_impl must declare on_failure. "
            "on_result routing does not handle run_skill tool failures."
        )

    def test_ip6_plan_step_note_contains_glob_pattern(self) -> None:
        """T_IP6: plan step note must contain *_part_*.md glob pattern for multi-part discovery."""
        note = self.recipe.steps["plan"].note or ""
        assert "*_part_*.md" in note, (
            "plan step note must contain glob pattern for multi-part discovery; "
            "if removed, agents will not discover part files"
        )

    def test_ip7_verify_step_note_sequential_constraint(self) -> None:
        """T_IP7: verify step note must contain sequential execution constraint."""
        note = self.recipe.steps["verify"].note or ""
        assert "SEQUENTIAL EXECUTION" in note or "full cycle" in note.lower(), (
            "verify step note must contain sequential constraint; "
            "without it agents may batch-verify all parts before implementing any"
        )

    def test_ip8_next_or_done_routes_more_parts_to_verify(self) -> None:
        """T_IP8: next_or_done routes more_parts back to verify for sequential processing."""
        step = self.recipe.steps["next_or_done"]
        assert step.on_result is not None
        assert step.on_result.routes.get("more_parts") == "verify", (
            "next_or_done must route more_parts back to verify for sequential part processing"
        )

    def test_ip9_next_or_done_routes_all_done_to_audit_impl(self) -> None:
        """T_IP9: next_or_done must route all_done to audit_impl."""
        step = self.recipe.steps["next_or_done"]
        assert step.on_result is not None
        assert step.on_result.routes.get("all_done") == "audit_impl"

    def test_ip_audit_impl_uses_base_sha_as_ref(self) -> None:
        """T_IP_B2: audit_impl must use context.base_sha (not context.branch_name).

        branch_name is deleted by git branch -D inside merge_worktree. A commit SHA
        names a git object and survives unconditionally.
        """
        step = self.recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.base_sha" in skill_cmd, (
            "audit_impl must use context.base_sha as implementation_ref — "
            "context.branch_name is deleted by merge_worktree before audit_impl runs"
        )
        assert "context.branch_name" not in skill_cmd, (
            "audit_impl must NOT use context.branch_name (deleted by merge_worktree)"
        )

    def test_ip_c1_fix_step_routes_on_success_to_test(self) -> None:
        """T_IP_C1: fix step must route on_success back to test (not next_or_done).

        assess-and-merge used to merge internally, so fix routed to next_or_done
        after the internal merge. resolve-failures does not merge, so the orchestrator
        must route fix → test to re-validate before entering merge_worktree.
        """
        step = self.recipe.steps["fix"]
        assert step.on_success == "test", (
            "fix step must route back to test — resolve-failures only fixes failures, "
            "it does not merge. The orchestrator must re-validate before merge_worktree."
        )

    def test_ip_base_sha_captured_before_implement(self) -> None:
        """A1: base_sha must be captured by a step that precedes the implement loop."""
        steps = list(self.recipe.steps.items())
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
                if step.tool in {"run_skill", "run_skill_retry"}
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

    def test_ip_audit_impl_uses_base_sha_not_branch_name(self) -> None:
        """A2: audit_impl must reference context.base_sha, NOT context.branch_name."""
        audit_step = self.recipe.steps.get("audit_impl")
        assert audit_step is not None, "audit_impl step not found"
        skill_cmd = audit_step.with_args.get("skill_command", "")
        assert "context.base_sha" in skill_cmd, (
            "audit_impl must use context.base_sha as implementation_ref"
        )
        assert "context.branch_name" not in skill_cmd, (
            "audit_impl must NOT use context.branch_name (deleted by merge_worktree)"
        )

    def test_ip_merge_target_unconditionally_set(self) -> None:
        """A3: merge_target must be captured by a non-optional step before merge/push."""
        steps = list(self.recipe.steps.items())
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

    def test_ip_base_sha_capture_uses_work_dir(self) -> None:
        """A4: The base_sha capture command must reference context.work_dir."""
        sha_step = next(
            (
                step
                for name, step in self.recipe.steps.items()
                if step.capture and "base_sha" in step.capture
            ),
            None,
        )
        assert sha_step is not None, "No step captures base_sha"
        with_args_str = str(sha_step.with_args)
        assert "context.work_dir" in with_args_str, (
            "base_sha capture must run inside context.work_dir (the clone directory)"
        )

    def test_ip_base_sha_used_by_audit_impl(self) -> None:
        """A5: base_sha captured must be consumed — specifically by audit_impl."""
        report = analyze_dataflow(self.recipe)
        dead = {w.field for w in report.warnings if w.code == "DEAD_OUTPUT"}
        assert "base_sha" not in dead, (
            "base_sha is captured but never consumed — audit_impl must reference it"
        )


# ---------------------------------------------------------------------------
# TestBugfixLoopStructure
# ---------------------------------------------------------------------------


class TestBugfixLoopStructure:
    @pytest.fixture(scope="class", autouse=True)
    def _load_recipe(self, request) -> None:
        request.cls.recipe = load_recipe(builtin_recipes_dir() / "bugfix-loop.yaml")

    def test_bl1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_BL1: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_bl2_remediate_step_exists_with_on_success_plan(self) -> None:
        """T_BL2: a step named remediate exists with on_success == 'plan'."""
        assert "remediate" in self.recipe.steps
        assert self.recipe.steps["remediate"].on_success == "plan"

    def test_bl_b1_implement_captures_branch_name(self) -> None:
        """T_BL_B1: implement step must capture branch_name from result."""
        step = self.recipe.steps["implement"]
        assert "branch_name" in step.capture, (
            "implement step must capture branch_name so audit_impl can pass a "
            "stable git ref to audit-impl after merge_worktree deletes the worktree"
        )

    def test_bl_b2_audit_impl_uses_branch_name_as_ref(self) -> None:
        """T_BL_B2: audit_impl with: must reference context.branch_name as implementation_ref."""
        step = self.recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.branch_name" in skill_cmd, (
            "audit_impl must pass context.branch_name as implementation_ref — not "
            "context.implementation_ref or context.worktree_path (stale after merge)"
        )

    def test_bl_b3_retry_worktree_captures_branch_name(self) -> None:
        """T_BL_B3: retry_worktree step must also capture branch_name."""
        step = self.recipe.steps["retry_worktree"]
        assert "branch_name" in step.capture, (
            "retry_worktree also updates the active worktree reference; "
            "it must capture branch_name for downstream audit_impl use"
        )


# ---------------------------------------------------------------------------
# TestInvestigateFirstStructure
# ---------------------------------------------------------------------------


class TestInvestigateFirstStructure:
    @pytest.fixture(scope="class", autouse=True)
    def _load_recipe(self, request) -> None:
        request.cls.recipe = load_recipe(builtin_recipes_dir() / "investigate-first.yaml")

    def test_if1_audit_impl_has_verdict_and_remediation_capture_and_on_result(
        self,
    ) -> None:
        """T_IF1: audit_impl captures verdict+remediation_path and routes via on_result."""
        step = self.recipe.steps["audit_impl"]
        assert "verdict" in step.capture
        assert "remediation_path" in step.capture
        assert step.on_result is not None
        assert step.on_result.field == "verdict"

    def test_if2_remediate_step_routes_to_make_plan(self) -> None:
        """T_IF2: remediate step exists and routes to make_plan (not rectify)."""
        assert "remediate" in self.recipe.steps
        assert self.recipe.steps["remediate"].on_success == "make_plan"

    def test_if5_make_plan_step_has_correct_structure(self) -> None:
        """T_IF5: make_plan step calls make-plan with remediation_path and captures outputs."""
        assert "make_plan" in self.recipe.steps
        step = self.recipe.steps["make_plan"]
        assert step.tool == "run_skill"
        skill_cmd = step.with_args.get("skill_command", "")
        assert "/autoskillit:make-plan" in skill_cmd
        assert "context.remediation_path" in skill_cmd
        assert "plan_path" in step.capture
        assert "plan_parts" in step.capture_list
        assert step.on_success == "review"
        assert step.on_failure == "cleanup_failure"

    def test_if3_verify_step_uses_implementation_ref(self) -> None:
        """T_IF3: verify step worktree_path must reference context.implementation_ref."""
        worktree_arg = self.recipe.steps["verify"].with_args.get("worktree_path", "")
        assert "context.implementation_ref" in worktree_arg
        assert "context.work_dir" not in worktree_arg

    def test_if4_merge_step_uses_implementation_ref(self) -> None:
        """T_IF4: merge step worktree_path must reference context.implementation_ref."""
        worktree_arg = self.recipe.steps["merge"].with_args.get("worktree_path", "")
        assert "context.implementation_ref" in worktree_arg
        assert "context.work_dir" not in worktree_arg

    def test_if_b1_implement_captures_branch_name(self) -> None:
        """T_IF_B1: implement step must capture branch_name from result."""
        step = self.recipe.steps["implement"]
        assert "branch_name" in step.capture, (
            "implement step must capture branch_name so audit_impl can pass a "
            "stable git ref to audit-impl after merge_worktree deletes the worktree"
        )

    def test_if_b2_audit_impl_uses_branch_name_as_ref(self) -> None:
        """T_IF_B2: audit_impl with: must reference context.branch_name as implementation_ref."""
        step = self.recipe.steps["audit_impl"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "context.branch_name" in skill_cmd, (
            "audit_impl must pass context.branch_name as implementation_ref — not "
            "context.implementation_ref or context.worktree_path (stale after merge)"
        )

    def test_if_b3_retry_worktree_captures_branch_name(self) -> None:
        """T_IF_B3: retry_worktree step must also capture branch_name."""
        step = self.recipe.steps["retry_worktree"]
        assert "branch_name" in step.capture, (
            "retry_worktree also updates the active worktree reference; "
            "it must capture branch_name for downstream audit_impl use"
        )

    def test_if_c1_implement_uses_no_merge_skill(self) -> None:
        """T_IF_C1: implement step must use implement-worktree-no-merge.

        implement-worktree merges and deletes the worktree internally; subsequent
        verify (test_check) and assess (resolve-failures) steps would run against
        a non-existent path. implement-worktree-no-merge leaves the worktree intact
        for the orchestrator's gate-test-merge cycle.
        """
        step = self.recipe.steps["implement"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "implement-worktree-no-merge" in skill_cmd, (
            "implement step must use implement-worktree-no-merge; "
            "implement-worktree merges immediately, making verify and assess unreachable"
        )


# ---------------------------------------------------------------------------
# TestAuditAndFixStructure
# ---------------------------------------------------------------------------


class TestAuditAndFixStructure:
    @pytest.fixture(scope="class", autouse=True)
    def _load_recipe(self, request) -> None:
        request.cls.recipe = load_recipe(builtin_recipes_dir() / "audit-and-fix.yaml")

    def test_aaf1_implement_uses_no_merge_skill(self) -> None:
        """T_AAF1: implement step must use implement-worktree-no-merge."""
        step = self.recipe.steps["implement"]
        skill_cmd = step.with_args.get("skill_command", "")
        assert "implement-worktree-no-merge" in skill_cmd, (
            "implement step must use implement-worktree-no-merge; "
            "implement-worktree merges immediately so test_check runs on a deleted worktree"
        )

    def test_aaf2_has_merge_step(self) -> None:
        """T_AAF2: recipe must have a merge step (merge_worktree) after test passes."""
        assert "merge" in self.recipe.steps, (
            "audit-and-fix must have a merge step; "
            "without it the worktree is never merged after passing tests"
        )

    def test_aaf3_test_success_routes_to_merge(self) -> None:
        """T_AAF3: test step must route to merge (not push) on success."""
        assert self.recipe.steps["test"].on_success == "merge", (
            "test must route to merge on success — push comes after merge, "
            "not directly after test_check"
        )

    def test_aaf4_has_fix_step_for_test_failures(self) -> None:
        """T_AAF4: test on_failure must route to a fix/assess step, not cleanup_failure."""
        step = self.recipe.steps["test"]
        assert step.on_failure not in ("cleanup_failure", "escalate_stop"), (
            "test on_failure must route to a fix/assess step; "
            "going directly to cleanup_failure discards fixable failures"
        )

    def test_aaf5_fix_step_routes_back_to_test(self) -> None:
        """T_AAF5: fix step must exist and route on_success back to test."""
        assert "fix" in self.recipe.steps, "fix step must exist for resolve-failures loop"
        assert self.recipe.steps["fix"].on_success == "test", (
            "fix step must route back to test on success to re-validate the worktree"
        )

    def test_aaf6_merge_step_routes_to_push(self) -> None:
        """T_AAF6: merge step must route to push on success."""
        assert self.recipe.steps["merge"].on_success == "push", (
            "merge step must route to push — the push step propagates the merged branch "
            "from the clone back to the upstream remote"
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
        """check_summary step has on_result with field non_empty and routes true/false."""
        on_result = smoke_yaml["steps"]["check_summary"]["on_result"]
        assert on_result["field"] == "non_empty"
        assert "true" in on_result["routes"]
        assert "false" in on_result["routes"]

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
