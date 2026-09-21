"""Tests for merge gate context forwarding and diagnosis provenance rules."""

from __future__ import annotations

import pytest

from autoskillit.core import CaptureEntrySpec, Severity, pkg_root
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-merge-context-forward",
        description="Test recipe for merge-test-gate-context-not-forwarded rule.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _merge_on_result_conditions() -> list[StepResultCondition]:
    return [
        StepResultCondition(when="result.failed_step == 'dirty_tree'", route="check_loop"),
        StepResultCondition(when="result.failed_step == 'test_gate'", route="check_loop"),
        StepResultCondition(
            when="result.failed_step == 'post_rebase_test_gate'", route="check_loop"
        ),
        StepResultCondition(when="result.failed_step == 'rebase'", route="escalate"),
        StepResultCondition(when="result.failed_step == 'dirty_main_repo'", route="escalate"),
        StepResultCondition(when="result.error", route="escalate"),
        StepResultCondition(when=None, route="done"),
    ]


def test_merge_step_missing_test_output_capture_fires_error() -> None:
    """merge step routes test_gate but capture has no test_stdout/stderr → ERROR."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    # test_stdout and test_stderr are absent — should trigger the rule
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)


def test_merge_step_with_test_output_capture_is_clean() -> None:
    """merge step captures test_stdout and test_stderr → no finding."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    "merge_test_stdout": "${{ result.test_stdout }}",
                    "merge_test_stderr": "${{ result.test_stderr }}",
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                        " ${{ context.merge_gate_ci_conclusion }}"
                        " - ${{ context.merge_gate_diagnosis_path }}"
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert not flagged


def test_assess_step_missing_failure_context_fires_error() -> None:
    """assess invokes resolve-failures with only 3 args when reachable from merge test_gate."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    # Missing test_stdout/test_stderr
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                        # Only 3 args — no failure context
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) >= 1
    assert all(f.severity == Severity.ERROR for f in flagged)


def test_assess_step_with_failure_context_is_clean() -> None:
    """assess invokes resolve-failures with 6 args including merge_gate context → no finding."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture={
                    "cleanup_succeeded": "${{ result.cleanup_succeeded }}",
                    "worktree_path": "${{ result.worktree_path }}",
                    "merge_test_stdout": "${{ result.test_stdout }}",
                    "merge_test_stderr": "${{ result.test_stderr }}",
                },
                on_result=StepResultRoute(conditions=_merge_on_result_conditions()),
                on_failure="escalate",
            ),
            "check_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                capture={"merge_fix_count": "${{ result.next_iteration }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.max_exceeded }} == true", route="escalate"
                        ),
                        StepResultCondition(when=None, route="assess"),
                    ]
                ),
                on_failure="escalate",
            ),
            "assess": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures"
                        " ${{ context.worktree_path }}"
                        " ${{ context.plan_path }}"
                        " ${{ inputs.base_branch }}"
                        " ${{ context.merge_gate_ci_conclusion }}"
                        " - ${{ context.merge_gate_diagnosis_path }}"
                    ),
                    "step_name": "assess",
                },
                capture={
                    "verdict": "${{ result.verdict }}",
                    "fixes_applied": "${{ result.fixes_applied }}",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            when="${{ result.verdict }} == 'flake_suspected'", route="test"
                        ),
                        StepResultCondition(when=None, route="escalate"),
                    ]
                ),
                on_failure="escalate",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "${{ context.worktree_path }}"},
                on_success="done",
                on_failure="escalate",
            ),
            "done": RecipeStep(action="stop", with_args={}, message="done"),
            "escalate": RecipeStep(action="stop", with_args={}, message="escalate"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert not flagged


@pytest.mark.parametrize(
    "recipe_name",
    ["remediation.yaml", "implementation.yaml", "implementation-groups.yaml"],
)
def test_rule_does_not_fire_for_fixed_recipes(recipe_name: str) -> None:
    """Post recipe fix: rule no longer fires for the three pipeline recipes."""
    from autoskillit.core import pkg_root
    from autoskillit.recipe.io import load_recipe

    recipe_path = pkg_root() / "recipes" / recipe_name
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-test-gate-context-not-forwarded"]
    assert len(flagged) == 0


_DIAGNOSE_FIELDS = (
    "test_stdout",
    "test_stderr",
    "failed_step",
    "timed_out",
    "outer_timeout_seconds",
    "raw_output_artifact_path",
)
_OPTIONAL_FIELDS = {"timed_out", "outer_timeout_seconds", "raw_output_artifact_path"}


def _diagnosis_recipe(
    *,
    omit_capture: str | None = None,
    omit_binding: str | None = None,
    plain_capture: str | None = None,
    direct: bool = False,
) -> Recipe:
    captures = {
        f"merge_{field}": CaptureEntrySpec(
            from_=f"${{{{ result.{field} }}}}",
            value_type=(
                "string"
                if field == plain_capture or field not in _OPTIONAL_FIELDS
                else "optional_string"
            ),
        )
        for field in _DIAGNOSE_FIELDS
        if field != omit_capture
    }
    bindings = {
        field: f"${{{{ context.merge_{field} }}}}"
        for field in _DIAGNOSE_FIELDS
        if field != omit_binding
    }
    steps = {
        "merge": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
            capture=captures,
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        when="result.failed_step == 'test_gate'",
                        route="diagnose" if direct else "guard",
                    ),
                    StepResultCondition(when=None, route="done"),
                ]
            ),
        ),
        "diagnose": RecipeStep(
            tool="run_python",
            with_args={
                "callable": "autoskillit.smoke_utils.diagnose_merge_gate",
                "output_dir": "/tmp/diagnose-merge-gate",
                **bindings,
            },
            on_success="done",
        ),
        "done": RecipeStep(action="stop", message="Done."),
    }
    if not direct:
        steps["guard"] = RecipeStep(tool="run_python", on_success="diagnose")
    return _make_recipe(steps)


def _diagnosis_findings(recipe: Recipe) -> list:
    return [
        finding
        for finding in run_semantic_rules(recipe)
        if finding.rule == "merge-diagnosis-context-not-captured"
    ]


@pytest.mark.parametrize("field", _DIAGNOSE_FIELDS)
def test_diagnosis_context_rule_fires_for_each_missing_result_field(field: str) -> None:
    findings = _diagnosis_findings(_diagnosis_recipe(omit_capture=field))
    assert len(findings) == 1
    assert f"merge_{field}" in findings[0].message
    assert f"result.{field}" in findings[0].message
    assert findings[0].step_name == "merge"


def test_diagnosis_context_rule_fires_when_diagnose_step_does_not_bind_field() -> None:
    findings = _diagnosis_findings(_diagnosis_recipe(omit_binding="timed_out"))
    assert len(findings) == 1
    assert findings[0].step_name == "diagnose"
    assert "timed_out" in findings[0].message


@pytest.mark.parametrize("field", sorted(_OPTIONAL_FIELDS))
def test_diagnosis_context_rule_requires_optional_string_for_conditional_fields(
    field: str,
) -> None:
    findings = _diagnosis_findings(_diagnosis_recipe(plain_capture=field))
    assert len(findings) == 1
    assert "optional_string" in findings[0].message
    assert f"result.{field}" in findings[0].message
    assert _diagnosis_findings(_diagnosis_recipe()) == []


@pytest.mark.parametrize(
    "recipe_name", ["implementation.yaml", "implementation-groups.yaml", "remediation.yaml"]
)
def test_diagnosis_context_rule_clean_on_bundled_recipes(recipe_name: str) -> None:
    recipe = load_recipe(pkg_root() / "recipes" / recipe_name)
    assert _diagnosis_findings(recipe) == []


@pytest.mark.parametrize("omitted", ["capture", "binding"])
def test_diagnosis_context_rule_checks_direct_diagnose_route(omitted: str) -> None:
    recipe = _diagnosis_recipe(
        omit_capture="failed_step" if omitted == "capture" else None,
        omit_binding="failed_step" if omitted == "binding" else None,
        direct=True,
    )
    findings = _diagnosis_findings(recipe)
    assert len(findings) == 1
    assert "failed_step" in findings[0].message
