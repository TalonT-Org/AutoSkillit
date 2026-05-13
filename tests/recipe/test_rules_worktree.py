"""Tests for worktree-safety semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import (
    _parse_recipe,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeIngredient,
    RecipeStep,
)
from autoskillit.recipe.validator import (
    run_semantic_rules,
)
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

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
# retries-on-worktree-modifying-skill tests (new rules replacing old worktree-retry-creates-new)
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
        f.rule == "retries-on-worktree-modifying-skill" and "implement" in f.step_name
        for f in errors
    ), f"Expected retries-on-worktree-modifying-skill ERROR on implement step, got: {findings}"


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
    assert not any(f.rule == "retries-on-worktree-modifying-skill" for f in errors), (
        f"Unexpected retries-on-worktree-modifying-skill ERROR with retries=0: {findings}"
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
    assert not any(f.rule == "retries-on-worktree-modifying-skill" for f in findings)


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


def test_worktree_modifying_skills_includes_experiment() -> None:
    """_WORKTREE_MODIFYING_SKILLS must include implement-experiment.

    implement-experiment creates a worktree and emits early tokens; excluding it
    means recipes using it without on_context_limit silently bypass the
    missing-context-limit-on-worktree rule.
    """
    from autoskillit.recipe.rules.rules_worktree import _WORKTREE_MODIFYING_SKILLS

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


# ---------------------------------------------------------------------------
# relative-worktree-path-in-cmd rule tests
# ---------------------------------------------------------------------------


def test_run_cmd_with_relative_worktree_path_fires_warning() -> None:
    """run_cmd step with '../worktrees/' in cmd fires relative-worktree-path-in-cmd WARNING."""
    wf = _make_workflow(
        {
            "create_wt": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        'WORKTREE_PATH="../worktrees/${WORKTREE_NAME}"; '
                        "git worktree add -b '${WORKTREE_NAME}' '${WORKTREE_PATH}'"
                    ),
                    "cwd": "${{ inputs.source_dir }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    warnings = [f for f in findings if f.severity == Severity.WARNING]
    assert any(f.rule == "relative-worktree-path-in-cmd" for f in warnings), (
        f"Expected relative-worktree-path-in-cmd WARNING for run_cmd with '../worktrees/' in cmd. "
        f"Got: {findings}"
    )


def test_run_cmd_with_absolute_worktree_path_no_finding() -> None:
    """run_cmd step with absolute worktree path has no relative-worktree-path-in-cmd finding."""
    wf = _make_workflow(
        {
            "create_wt": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        'MAIN_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"; '
                        'WORKTREE_DIR="${MAIN_GIT_DIR}/../worktrees"; '
                        "mkdir -p '${WORKTREE_DIR}'; "
                        "WORKTREE_PATH='${WORKTREE_DIR}/${WORKTREE_NAME}'; "
                        "git worktree add -b '${WORKTREE_NAME}' '${WORKTREE_PATH}'"
                    ),
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "relative-worktree-path-in-cmd" for f in findings), (
        f"Unexpected relative-worktree-path-in-cmd finding for run_cmd with absolute path: "
        f"{findings}"
    )


def test_run_cmd_non_worktree_cmd_no_finding() -> None:
    """run_cmd step with no worktree path has no relative-worktree-path-in-cmd finding."""
    wf = _make_workflow(
        {
            "echo": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello world"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "relative-worktree-path-in-cmd" for f in findings)


def test_run_cmd_other_tool_no_finding() -> None:
    """Non-run_cmd steps are not checked by the rule."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge plan.md"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "relative-worktree-path-in-cmd" for f in findings)


def test_relative_worktree_path_warning_includes_step_name() -> None:
    """The finding message contains the step name for actionable output."""
    wf = _make_workflow(
        {
            "make_worktree": {
                "tool": "run_cmd",
                "with": {
                    "cmd": 'WORKTREE_PATH="../worktrees/test-wt"; git worktree add test-wt',
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    matched = [f for f in findings if f.rule == "relative-worktree-path-in-cmd"]
    assert len(matched) >= 1
    assert "make_worktree" in matched[0].message


# ---------------------------------------------------------------------------
# superseded-input-after-capture tests
# ---------------------------------------------------------------------------


def test_superseded_input_after_capture_fires_error() -> None:
    """Step using inputs.X as cwd after worktree-modifying skill captures context.X → ERROR."""
    wf = Recipe(
        name="test",
        description="test",
        ingredients={"worktree_path": RecipeIngredient(description="path", required=True)},
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-experiment plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="audit",
                on_failure="done",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:audit-impl manifest.json",
                    "cwd": "${{ inputs.worktree_path }}",
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
    assert any(
        f.rule == "superseded-input-after-capture" and f.step_name == "audit" for f in errors
    ), f"Expected superseded-input-after-capture ERROR on audit step, got: {findings}"


def test_superseded_input_before_capture_no_finding() -> None:
    """Step using inputs.X as cwd BEFORE the capture step → no finding."""
    wf = Recipe(
        name="test",
        description="test",
        ingredients={"worktree_path": RecipeIngredient(description="path", required=True)},
        steps={
            "pre_step": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:stage-data plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                on_success="implement",
                on_failure="done",
            ),
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-experiment plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "superseded-input-after-capture" for f in findings), (
        f"Unexpected superseded-input-after-capture finding before capture: {findings}"
    )


def test_superseded_input_context_cwd_no_finding() -> None:
    """Step using context.X as cwd after capture → no finding."""
    wf = Recipe(
        name="test",
        description="test",
        ingredients={"worktree_path": RecipeIngredient(description="path", required=True)},
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-experiment plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="audit",
                on_failure="done",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:audit-impl manifest.json",
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
    assert not any(f.rule == "superseded-input-after-capture" for f in findings), (
        f"Unexpected superseded-input-after-capture finding with context.X cwd: {findings}"
    )


def test_superseded_input_non_worktree_skill_no_finding() -> None:
    """Capture by non-worktree-modifying skill does not trigger the rule."""
    wf = Recipe(
        name="test",
        description="test",
        ingredients={"worktree_path": RecipeIngredient(description="path", required=True)},
        steps={
            "some_skill": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:make-plan plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="audit",
                on_failure="done",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:audit-impl manifest.json",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done."),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "superseded-input-after-capture" for f in findings), (
        f"Unexpected superseded-input-after-capture finding for non-worktree skill: {findings}"
    )


def test_superseded_input_in_skill_command_fires_error() -> None:
    """inputs.X in skill_command after capture also fires ERROR."""
    wf = Recipe(
        name="test",
        description="test",
        ingredients={"worktree_path": RecipeIngredient(description="path", required=True)},
        steps={
            "implement": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:implement-experiment plan.md",
                    "cwd": "${{ inputs.worktree_path }}",
                },
                capture={"worktree_path": "${{ result.worktree_path }}"},
                on_success="audit",
                on_failure="done",
            ),
            "audit": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:audit-impl ${{ inputs.worktree_path }}",
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
    assert any(
        f.rule == "superseded-input-after-capture" and f.step_name == "audit" for f in errors
    ), f"Expected superseded-input-after-capture ERROR for skill_command ref, got: {findings}"
