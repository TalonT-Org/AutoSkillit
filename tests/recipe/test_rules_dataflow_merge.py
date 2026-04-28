"""Tests for data-flow semantic rules — merge cleanup, stale ref after merge, push rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.fixture
def all_bundled_recipes() -> list[tuple[str, Recipe]]:
    """Load all bundled recipe YAML files and return as (name, Recipe) pairs."""
    result = []
    for yaml_file in builtin_recipes_dir().glob("*.yaml"):
        result.append((yaml_file.stem, load_recipe(yaml_file)))
    return result


def _build_merge_worktree_recipe(capture: dict) -> Recipe:
    """Helper: build a minimal Recipe with a merge_worktree step and the given capture dict."""
    return Recipe(
        name="test-merge",
        description="Test merge recipe",
        summary="merge > done",
        steps={
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture=capture,
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )


def _make_stale_worktree_path_recipe() -> Recipe:
    """Return the stale-worktree-path recipe used by tests B1 and B6."""
    return Recipe(
        name="test-stale-path",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="test",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={
                    "worktree_path": "${{ context.worktree_path }}",
                    "base_branch": "main",
                },
                capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                on_success="audit",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:audit-impl plan.md ${{ context.worktree_path }} main"
                    ),
                },
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
    )


# ---------------------------------------------------------------------------
# merge-cleanup-uncaptured tests
# ---------------------------------------------------------------------------


def test_semantic_rule_warns_merge_worktree_without_cleanup_capture() -> None:
    """N12: merge_worktree step without cleanup_succeeded captured emits warning."""
    recipe = _build_merge_worktree_recipe(capture={})
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_semantic_rule_warns_merge_worktree_with_unrelated_capture() -> None:
    """N12: merge_worktree step capturing only merge_succeeded still warns about cleanup."""
    recipe = _build_merge_worktree_recipe(capture={"merged": "${{ result.merge_succeeded }}"})
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_semantic_rule_passes_when_cleanup_captured() -> None:
    """N12: No merge-cleanup-uncaptured warning when cleanup_succeeded is captured."""
    recipe = _build_merge_worktree_recipe(
        capture={"cleanup_ok": "${{ result.cleanup_succeeded }}"}
    )
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_merge_cleanup_uncaptured_rule_not_triggered_on_non_merge_step() -> None:
    """N12: The rule does not fire on non-merge_worktree steps."""
    recipe = Recipe(
        name="test-non-merge",
        description="Test recipe without merge_worktree",
        summary="run > done",
        steps={
            "run": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo hi", "cwd": "/tmp"},
                capture={},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "merge-cleanup-uncaptured" for f in findings)


def test_bundled_recipes_capture_cleanup_succeeded() -> None:
    """N12: All bundled recipes with merge_worktree steps must capture cleanup_succeeded."""
    wf_dir = builtin_recipes_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files

    for path in yaml_files:
        wf = load_recipe(path)
        findings = run_semantic_rules(wf)
        uncaptured = [f for f in findings if f.rule == "merge-cleanup-uncaptured"]
        assert not uncaptured, (
            f"Bundled recipe {path.name} emits merge-cleanup-uncaptured: {uncaptured}"
        )


# ---------------------------------------------------------------------------
# TestStaleRefAfterMerge
# ---------------------------------------------------------------------------


class TestStaleRefAfterMerge:
    """Part B: stale-ref-after-merge semantic rule and _detect_ref_invalidations()."""

    def test_B1_stale_ref_after_merge_fires_for_worktree_path(self) -> None:
        """B1: Rule fires when a worktree_path capture is consumed after merge_worktree."""
        recipe = _make_stale_worktree_path_recipe()
        findings = run_semantic_rules(recipe)
        stale_findings = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert stale_findings, (
            "Expected stale-ref-after-merge finding for worktree_path used after merge"
        )
        assert any(f.step_name == "audit" for f in stale_findings)

    def test_B2_stale_ref_after_merge_fires_for_branch_name(self) -> None:
        """B2: Rule fires when a branch_name capture is consumed after merge_worktree."""
        recipe = Recipe(
            name="test-stale-branch",
            description="test",
            steps={
                "implement": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={"branch_name": "${{ result.branch_name }}"},
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={"worktree_path": "../worktrees/wt", "base_branch": "main"},
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.branch_name }} main"
                        ),
                    },
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert stale, "Expected stale-ref-after-merge finding for branch_name used after merge"
        assert any(f.step_name == "audit" for f in stale)

    def test_B3_stale_ref_after_merge_clean_when_sha_used(self) -> None:
        """B3: Rule does NOT fire when audit_impl uses a stable SHA, not a branch ref."""
        recipe = Recipe(
            name="test-clean-sha",
            description="test",
            steps={
                "capture_sha": RecipeStep(
                    tool="run_cmd",
                    with_args={"cmd": "git rev-parse main", "cwd": "/work"},
                    capture={"base_sha": "${{ result.stdout }}"},
                    on_success="implement",
                ),
                "implement": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={
                        "worktree_path": "${{ result.worktree_path }}",
                        "branch_name": "${{ result.branch_name }}",
                    },
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.base_sha }} main"
                        ),
                    },
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert not stale, f"Expected no stale-ref findings when base_sha is used: {stale}"

    def test_B4_stale_ref_after_merge_clean_before_merge(self) -> None:
        """B4: Rule does NOT fire when worktree_path is only consumed before merge_worktree."""
        recipe = Recipe(
            name="test-before-merge",
            description="test",
            steps={
                "implement": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge plan.md",
                    },
                    capture={"worktree_path": "${{ result.worktree_path }}"},
                    on_success="audit",
                ),
                "audit": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": (
                            "/autoskillit:audit-impl plan.md ${{ context.worktree_path }} main"
                        ),
                    },
                    on_success="merge",
                ),
                "merge": RecipeStep(
                    tool="merge_worktree",
                    with_args={
                        "worktree_path": "${{ context.worktree_path }}",
                        "base_branch": "main",
                    },
                    capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                    on_success="done",
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        findings = run_semantic_rules(recipe)
        stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
        assert not stale, (
            "Expected no stale-ref findings when worktree_path is consumed BEFORE merge: "
            + str(stale)
        )

    def test_B5_bundled_recipes_pass_stale_ref_rule_after_part_a(
        self, all_bundled_recipes: list[tuple[str, Recipe]]
    ) -> None:
        """B5: All bundled recipes must pass the stale-ref-after-merge rule after Part A fixes."""
        for recipe_name, recipe in all_bundled_recipes:
            findings = run_semantic_rules(recipe)
            stale = [f for f in findings if f.rule == "stale-ref-after-merge"]
            assert not stale, (
                f"Bundled recipe '{recipe_name}' has stale-ref-after-merge violations: {stale}"
            )

    def test_B6_detect_ref_invalidations_in_dataflow_report(self) -> None:
        """B6: analyze_dataflow() emits REF_INVALIDATED warnings for stale-ref patterns."""
        from autoskillit.recipe.validator import analyze_dataflow

        recipe = _make_stale_worktree_path_recipe()
        report = analyze_dataflow(recipe)
        ref_warnings = [w for w in report.warnings if w.code == "REF_INVALIDATED"]
        assert ref_warnings, "Expected REF_INVALIDATED warnings in DataFlowReport"
        assert any(w.step_name == "audit" for w in ref_warnings)


# ---------------------------------------------------------------------------
# TestPushBeforeAuditRule
# ---------------------------------------------------------------------------


class TestPushBeforeAuditRule:
    def test_ppb1_audit_before_push_no_finding(self) -> None:
        """PPB1: audit-impl runs before push_to_remote — no warning emitted."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "audit"},
                "audit": {
                    "tool": "run_skill",
                    "on_success": "push",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
                },
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert findings == []

    def test_ppb2_push_before_audit_fires_warning(self) -> None:
        """PPB2: push_to_remote is reachable without any audit-impl step → WARNING."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "push"},
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert findings[0].step_name == "push"

    def test_ppb3_no_push_step_no_finding(self) -> None:
        """PPB3: recipe has no push_to_remote step — rule is silent."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [f for f in run_semantic_rules(recipe) if f.rule == "push-before-audit"]
        assert findings == []

    def test_ip_push_after_audit_now_correctly_has_violation(self) -> None:
        """T_IP_PBA: bypass path via skip_when_false makes push-before-audit fire.

        Uses a synthetic recipe mirroring implementation topology:
          start → audit_impl (optional, skip_when_false) → compose_pr → push
        The skip_when_false bypass allows push to be reached without audit.
        """
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "audit_impl"},
                "audit_impl": {
                    "tool": "run_skill",
                    "optional": True,
                    "skip_when_false": "inputs.audit",
                    "with": {
                        "skill_command": "/autoskillit:audit-impl plan.md",
                        "cwd": "/tmp",
                    },
                    "on_success": "compose_pr",
                    "on_failure": "done",
                },
                "compose_pr": {
                    "tool": "run_skill",
                    "optional": True,
                    "skip_when_false": "inputs.open_pr",
                    "with": {
                        "skill_command": "/autoskillit:compose-pr",
                        "cwd": "/tmp",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "on_success": "done",
                    "with": {
                        "clone_path": "/tmp/clone",
                        "source_dir": "/tmp/src",
                        "branch": "main",
                    },
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        violations = [f for f in findings if f.rule == "push-before-audit"]
        assert len(violations) >= 1
        assert violations[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# TestPushMissingExplicitRemoteUrl
# ---------------------------------------------------------------------------


class TestPushMissingExplicitRemoteUrl:
    """push-missing-explicit-remote-url rule fires when push_to_remote lacks remote_url."""

    def test_warns_when_push_to_remote_has_no_remote_url(self) -> None:
        """Rule fires when push_to_remote step has source_dir but no remote_url."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": "${{ inputs.source_dir }}", "run_name": "test"},
                    "capture": {
                        "work_dir": "${{ result.clone_path }}",
                        "source_dir": "${{ result.source_dir }}",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "source_dir": "${{ context.source_dir }}",
                        "branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "push-missing-explicit-remote-url" in rule_names

    def test_no_warning_when_explicit_remote_url_provided(self) -> None:
        """Rule is silent when push_to_remote step includes an explicit remote_url."""
        recipe = _make_workflow(
            {
                "clone": {
                    "tool": "clone_repo",
                    "with": {"source_dir": "${{ inputs.source_dir }}", "run_name": "test"},
                    "capture": {
                        "work_dir": "${{ result.clone_path }}",
                        "source_dir": "${{ result.source_dir }}",
                        "remote_url": "${{ result.remote_url }}",
                    },
                    "on_success": "push",
                },
                "push": {
                    "tool": "push_to_remote",
                    "with": {
                        "clone_path": "${{ context.work_dir }}",
                        "remote_url": "${{ context.remote_url }}",
                        "branch": "main",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(recipe)
        rule_names = [f.rule for f in findings]
        assert "push-missing-explicit-remote-url" not in rule_names

    def test_no_finding_when_no_push_to_remote_step(self) -> None:
        """Rule is silent when recipe has no push_to_remote step."""
        recipe = _make_workflow(
            {
                "start": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = [
            f for f in run_semantic_rules(recipe) if f.rule == "push-missing-explicit-remote-url"
        ]
        assert findings == []
