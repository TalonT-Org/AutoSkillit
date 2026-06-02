from __future__ import annotations

import pytest

from autoskillit.core.types import CaptureEntrySpec, Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE = "failure-verdict-bypass-reachable"

_SENTINEL_SUCCESS = (
    'Output the following sentinel JSON:\n\nExample sentinel: {"success": true, "reason": "done"}'
)
_SENTINEL_FAILURE = (
    "Output the following sentinel JSON:\n\n"
    'Example sentinel: {"success": false, "reason": "failed"}'
)


def _minimal_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(
        name="test-bypass",
        description="test",
        summary="test",
        ingredients={},
        kitchen_rules=["test"],
        steps=steps,
    )


def _bypass_findings(recipe: Recipe) -> list:
    findings = run_semantic_rules(recipe)
    return [f for f in findings if f.rule == _RULE]


class TestFailureVerdictBypassRule:
    def test_rule_registered(self) -> None:
        from autoskillit.recipe.registry import _RULE_REGISTRY

        assert any(r.name == _RULE for r in _RULE_REGISTRY)

    def test_catches_context_limit_bypass_to_success_terminal(self) -> None:
        recipe = _minimal_recipe(
            {
                "fix": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:resolve-failures x y z",
                        "cwd": "/tmp",
                        "output_dir": ".",
                    },
                    capture={
                        "verdict": CaptureEntrySpec(
                            from_="${{ result.verdict }}", value_type="string"
                        )
                    },
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(
                                when="${{ result.verdict }} == 'ci_only_failure'",
                                route="fail_stop",
                            ),
                            StepResultCondition(when=None, route="test_step"),
                        ]
                    ),
                    on_context_limit="test_step",
                    on_failure="fail_stop",
                ),
                "test_step": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
                    on_success="done_stop",
                    on_failure="fail_stop",
                ),
                "fail_stop": RecipeStep(action="stop", message=_SENTINEL_FAILURE),
                "done_stop": RecipeStep(action="stop", message=_SENTINEL_SUCCESS),
            }
        )
        hits = _bypass_findings(recipe)
        assert len(hits) >= 1
        assert hits[0].severity == Severity.ERROR
        assert "on_context_limit" in hits[0].message
        assert "test_step" in hits[0].message

    def test_catches_rate_limit_bypass_to_success_terminal(self) -> None:
        recipe = _minimal_recipe(
            {
                "fix": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:resolve-failures x y z",
                        "cwd": "/tmp",
                        "output_dir": ".",
                    },
                    capture={
                        "verdict": CaptureEntrySpec(
                            from_="${{ result.verdict }}", value_type="string"
                        )
                    },
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(
                                when="${{ result.verdict }} == 'ci_only_failure'",
                                route="fail_stop",
                            ),
                            StepResultCondition(when=None, route="test_step"),
                        ]
                    ),
                    on_rate_limit="test_step",
                    on_failure="fail_stop",
                ),
                "test_step": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
                    on_success="done_stop",
                    on_failure="fail_stop",
                ),
                "fail_stop": RecipeStep(action="stop", message=_SENTINEL_FAILURE),
                "done_stop": RecipeStep(action="stop", message=_SENTINEL_SUCCESS),
            }
        )
        hits = _bypass_findings(recipe)
        assert len(hits) >= 1
        assert "on_rate_limit" in hits[0].message

    def test_no_false_positive_when_bypass_routes_to_failure(self) -> None:
        recipe = _minimal_recipe(
            {
                "fix": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:resolve-failures x y z",
                        "cwd": "/tmp",
                        "output_dir": ".",
                    },
                    capture={
                        "verdict": CaptureEntrySpec(
                            from_="${{ result.verdict }}", value_type="string"
                        )
                    },
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(
                                when="${{ result.verdict }} == 'ci_only_failure'",
                                route="fail_stop",
                            ),
                            StepResultCondition(when=None, route="fail_stop"),
                        ]
                    ),
                    on_context_limit="fail_stop",
                    on_failure="fail_stop",
                ),
                "fail_stop": RecipeStep(action="stop", message=_SENTINEL_FAILURE),
            }
        )
        hits = _bypass_findings(recipe)
        assert len(hits) == 0

    def test_no_false_positive_when_step_has_no_failure_verdicts(self) -> None:
        recipe = _minimal_recipe(
            {
                "run": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:implement-worktree-no-merge x",
                        "cwd": "/tmp",
                    },
                    on_success="done_stop",
                    on_context_limit="done_stop",
                    on_failure="fail_stop",
                ),
                "fail_stop": RecipeStep(action="stop", message=_SENTINEL_FAILURE),
                "done_stop": RecipeStep(action="stop", message=_SENTINEL_SUCCESS),
            }
        )
        hits = _bypass_findings(recipe)
        assert len(hits) == 0

    def test_catches_transitive_bypass(self) -> None:
        recipe = _minimal_recipe(
            {
                "fix": RecipeStep(
                    tool="run_skill",
                    with_args={
                        "skill_command": "/autoskillit:resolve-failures x y z",
                        "cwd": "/tmp",
                        "output_dir": ".",
                    },
                    capture={
                        "verdict": CaptureEntrySpec(
                            from_="${{ result.verdict }}", value_type="string"
                        )
                    },
                    on_result=StepResultRoute(
                        conditions=[
                            StepResultCondition(
                                when="${{ result.verdict }} == 'ci_only_failure'",
                                route="fail_stop",
                            ),
                            StepResultCondition(when=None, route="step_a"),
                        ]
                    ),
                    on_context_limit="step_a",
                    on_failure="fail_stop",
                ),
                "step_a": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:foo", "cwd": "/tmp"},
                    on_success="step_b",
                    on_failure="fail_stop",
                ),
                "step_b": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:bar", "cwd": "/tmp"},
                    on_success="step_c",
                    on_failure="fail_stop",
                ),
                "step_c": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "/autoskillit:baz", "cwd": "/tmp"},
                    on_success="done_stop",
                    on_failure="fail_stop",
                ),
                "fail_stop": RecipeStep(action="stop", message=_SENTINEL_FAILURE),
                "done_stop": RecipeStep(action="stop", message=_SENTINEL_SUCCESS),
            }
        )
        hits = _bypass_findings(recipe)
        assert len(hits) >= 1

    def test_bundled_implementation_yaml_no_bypass(self) -> None:
        recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
        hits = _bypass_findings(recipe)
        assert len(hits) == 0, f"Expected zero bypass findings after fix, got: {hits}"

    def test_bundled_remediation_yaml_no_bypass(self) -> None:
        recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
        hits = _bypass_findings(recipe)
        assert len(hits) == 0, f"Expected zero bypass findings after fix, got: {hits}"
