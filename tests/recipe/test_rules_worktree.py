"""Tests for worktree-safety semantic rules."""

from __future__ import annotations

from autoskillit.core.types import Severity
from autoskillit.recipe.io import (
    _parse_recipe,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
)
from autoskillit.recipe.validator import (
    run_semantic_rules,
)
from tests.recipe.conftest import _make_workflow

# ---------------------------------------------------------------------------
# retry-worktree-cwd tests
# ---------------------------------------------------------------------------


def test_retry_worktree_cwd_inputs_triggers_error() -> None:
    """retry-worktree step with cwd=inputs.* fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_context_limit": "retry_step",
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ inputs.work_dir }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_context_clean() -> None:
    """retry-worktree step with cwd=context.worktree_path has no retry-worktree-cwd finding."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_context_limit": "retry_step",
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ context.worktree_path }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


def test_retry_worktree_cwd_missing_triggers_error() -> None:
    """retry-worktree step with no cwd fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_non_skill_step_ignored() -> None:
    """retry-worktree-cwd rule only fires on skill steps, not run_cmd."""
    wf = _make_workflow(
        {
            "cmd": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello", "cwd": "${{ inputs.work_dir }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


# ---------------------------------------------------------------------------
# retries-on-worktree-creating-skill tests (new rules replacing old worktree-retry-creates-new)
# ---------------------------------------------------------------------------


def test_retries_on_worktree_creating_skill_triggers() -> None:
    """retries > 0 on implement-worktree skill → ERROR (creates orphaned worktrees)."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    )
                },
                retries=3,  # DEFAULT retries on a worktree-creating skill → ERROR
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "retries-on-worktree-creating-skill" and "implement" in f.step_name
        for f in errors
    ), f"Expected retries-on-worktree-creating-skill ERROR on implement step, got: {findings}"


def test_retries_zero_on_worktree_creating_skill_is_clean() -> None:
    """retries: 0 with on_context_limit on implement-worktree → no error."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    )
                },
                retries=0,
                on_context_limit="retry_wt",
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="done",
                on_failure="done",
            ),
            "retry_wt": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:retry-worktree "
                    "${{ context.plan_path }} ${{ context.worktree_path }}",
                    "cwd": "${{ context.worktree_path }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not any(f.rule == "retries-on-worktree-creating-skill" for f in errors), (
        f"Unexpected retries-on-worktree-creating-skill ERROR with retries=0: {findings}"
    )


def test_on_context_limit_on_worktree_skill_is_clean() -> None:
    """on_context_limit: retry_worktree on implement-worktree step → no error."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    )
                },
                retries=0,
                on_context_limit="retry_wt",
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="done",
                on_failure="done",
            ),
            "retry_wt": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:retry-worktree "
                    "${{ context.plan_path }} ${{ context.worktree_path }}",
                    "cwd": "${{ context.worktree_path }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retries-on-worktree-creating-skill" for f in findings)


# ---------------------------------------------------------------------------
# missing-context-limit-on-worktree tests
# ---------------------------------------------------------------------------


def test_missing_context_limit_on_worktree_step_warns() -> None:
    """Recipe with implement-worktree-no-merge step and no on_context_limit
    should emit a WARNING-level finding."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    )
                },
                on_failure="done",
                retries=0,
                # on_context_limit deliberately absent
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    warning_rules = [f.rule for f in findings if f.severity == Severity.WARNING]
    assert "missing-context-limit-on-worktree" in warning_rules


def test_worktree_step_with_context_limit_no_warning() -> None:
    """Recipe with implement-worktree-no-merge + on_context_limit should be clean."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    )
                },
                on_failure="done",
                on_context_limit="retry_worktree",
                retries=0,
            ),
            "retry_worktree": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                    "cwd": "${{ context.worktree_path }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    warning_rules = [f.rule for f in findings if f.severity == Severity.WARNING]
    assert "missing-context-limit-on-worktree" not in warning_rules


# ---------------------------------------------------------------------------
# advisory-step-missing-context-limit tests
# ---------------------------------------------------------------------------


def test_advisory_step_missing_context_limit_fires_warning() -> None:
    """run_skill step with skip_when_false but no on_context_limit → WARNING."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "review": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-approach plan.md"},
                skip_when_false="inputs.review_approach",
                on_success="next_step",
                on_failure="abort",
            ),
            "next_step": RecipeStep(action="stop", message="Done."),
            "abort": RecipeStep(action="stop", message="Abort."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "advisory-step-missing-context-limit" for f in findings), (
        f"Expected advisory-step-missing-context-limit, got: {findings}"
    )
    matched = next(f for f in findings if f.rule == "advisory-step-missing-context-limit")
    assert matched.severity == Severity.WARNING


def test_advisory_step_with_context_limit_no_warning() -> None:
    """run_skill step with skip_when_false and on_context_limit set → no warning."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "review": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-approach plan.md"},
                skip_when_false="inputs.review_approach",
                on_success="next_step",
                on_failure="abort",
                on_context_limit="next_step",
            ),
            "next_step": RecipeStep(action="stop", message="Done."),
            "abort": RecipeStep(action="stop", message="Abort."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "advisory-step-missing-context-limit" for f in findings)


def test_non_advisory_step_does_not_trigger_rule() -> None:
    """run_skill step without skip_when_false and no on_context_limit → no finding."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "investigate": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:investigate plan.md"},
                on_success="next_step",
                on_failure="abort",
            ),
            "next_step": RecipeStep(action="stop", message="Done."),
            "abort": RecipeStep(action="stop", message="Abort."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "advisory-step-missing-context-limit" for f in findings)


def test_non_run_skill_step_with_skip_when_false_does_not_trigger() -> None:
    """run_cmd step with skip_when_false → rule is run_skill-only, no finding."""
    wf = _make_workflow(
        {
            "check": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello"},
                "skip_when_false": "inputs.some_flag",
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "advisory-step-missing-context-limit" for f in findings)


def test_advisory_step_rule_finding_includes_step_name() -> None:
    """The finding message contains the step name for actionable output."""
    wf = Recipe(
        name="test",
        description="test",
        steps={
            "my_review_step": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:review-approach plan.md"},
                skip_when_false="inputs.review_approach",
                on_success="next_step",
                on_failure="abort",
            ),
            "next_step": RecipeStep(action="stop", message="Done."),
            "abort": RecipeStep(action="stop", message="Abort."),
        },
        kitchen_rules=[],
    )
    findings = run_semantic_rules(wf)
    advisory_findings = [f for f in findings if f.rule == "advisory-step-missing-context-limit"]
    assert len(advisory_findings) >= 1
    assert "my_review_step" in advisory_findings[0].message


# ---------------------------------------------------------------------------
# TestCloneRootAsWorktreeRule
# ---------------------------------------------------------------------------


class TestCloneRootAsWorktreeRule:
    def test_crw1_rule_in_registry(self) -> None:
        """T_CRW1: clone-root-as-worktree is registered in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        assert "clone-root-as-worktree" in {r.name for r in _RULE_REGISTRY}

    def _bad_recipe_test_check(self) -> Recipe:
        """Helper: recipe where work_dir is captured from clone_path, test_check uses it."""
        return _parse_recipe(
            {
                "name": "bad-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.work_dir }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )

    def test_crw2_rule_fires_for_test_check(self) -> None:
        """T_CRW2: ERROR when test_check passes work_dir (from clone_path) as worktree_path."""
        recipe = self._bad_recipe_test_check()
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "test"

    def test_crw3_rule_fires_for_merge_worktree(self) -> None:
        """T_CRW3: ERROR when merge_worktree passes work_dir (from clone_path) as worktree_path."""
        recipe = _parse_recipe(
            {
                "name": "bad-merge",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "merge",
                        "on_failure": "stop_err",
                    },
                    "merge": {
                        "tool": "merge_worktree",
                        "with": {
                            "worktree_path": "${{ context.work_dir }}",
                            "base_branch": "main",
                        },
                        "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "merge"

    def test_crw4_passes_for_worktree_from_result_worktree_path(self) -> None:
        """T_CRW4: no finding when worktree_path captured from result.worktree_path (correct)."""
        recipe = _parse_recipe(
            {
                "name": "good-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "implement": {
                        "tool": "run_skill",
                        "with": {
                            "skill_command": "/autoskillit:implement-worktree-no-merge plan.md"
                        },
                        "capture": {"implementation_ref": "${{ result.worktree_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.implementation_ref }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert crw == []

    def test_crw5_bundled_recipes_pass_clone_root_rule(self) -> None:
        """T_CRW5: no bundled recipe triggers clone-root-as-worktree."""
        bd = builtin_recipes_dir()
        for yaml_path in sorted(bd.glob("*.yaml")):
            recipe = load_recipe(yaml_path)
            findings = run_semantic_rules(recipe)
            crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
            assert crw == [], f"{yaml_path.name} triggered clone-root-as-worktree: {crw}"


# ---------------------------------------------------------------------------
# _WORKTREE_MODIFYING_SKILLS membership guard
# ---------------------------------------------------------------------------


def test_worktree_creating_skills_includes_experiment() -> None:
    """_WORKTREE_MODIFYING_SKILLS must include implement-experiment.

    implement-experiment creates a worktree and emits early tokens; excluding it
    means recipes using it without on_context_limit silently bypass the
    missing-context-limit-on-worktree rule.
    """
    from autoskillit.recipe.rules_worktree import _WORKTREE_MODIFYING_SKILLS

    assert "implement-experiment" in _WORKTREE_MODIFYING_SKILLS, (
        "_WORKTREE_MODIFYING_SKILLS must include implement-experiment so that the "
        "missing-context-limit-on-worktree rule fires for recipes using it without "
        "on_context_limit."
    )


# ---------------------------------------------------------------------------
# file-writing-skill-missing-context-limit rule tests
# ---------------------------------------------------------------------------


def test_file_writing_skill_missing_context_limit_fires() -> None:
    """run_skill step with write_behavior=always and no on_context_limit → WARNING.

    Uses generate-report which has write_behavior=always in the bundled manifest.
    """
    wf = _make_workflow(
        {
            "report": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:generate-report /tmp/wt /tmp/results.json"
                },
                "on_success": "done",
                "on_failure": "done",
                # on_context_limit deliberately absent
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert any(f.rule == "file-writing-skill-missing-context-limit" for f in findings), (
        f"Expected file-writing-skill-missing-context-limit WARNING for generate-report "
        f"step (write_behavior=always) without on_context_limit. Got: {findings}"
    )
    matched = next(f for f in findings if f.rule == "file-writing-skill-missing-context-limit")
    assert matched.severity == Severity.WARNING


def test_file_writing_skill_with_context_limit_no_warning() -> None:
    """run_skill step with write_behavior=always and on_context_limit set → no warning."""
    wf = _make_workflow(
        {
            "report": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:generate-report /tmp/wt /tmp/results.json"
                },
                "on_context_limit": "done",
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "file-writing-skill-missing-context-limit" for f in findings), (
        f"Unexpected file-writing-skill-missing-context-limit finding when "
        f"on_context_limit is set: {findings}"
    )


def test_file_writing_skill_advisory_step_not_flagged() -> None:
    """run_skill step with skip_when_false (advisory) is NOT flagged by this rule.

    Advisory steps are already covered by advisory-step-missing-context-limit.
    """
    wf = _make_workflow(
        {
            "report": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:generate-report /tmp/wt /tmp/results.json"
                },
                "skip_when_false": "inputs.run_report",
                "on_success": "done",
                "on_failure": "done",
                # on_context_limit absent but advisory → should not fire this rule
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "file-writing-skill-missing-context-limit" for f in findings), (
        f"Advisory steps (skip_when_false) should not trigger "
        f"file-writing-skill-missing-context-limit: {findings}"
    )
