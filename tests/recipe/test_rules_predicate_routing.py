from __future__ import annotations

import pytest

from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultRoute
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestPredicateOnResultValidation:
    """Structural validation for predicate-format on_result (conditions list)."""

    def _make_merge_recipe(self, merge_step: dict, extra_steps: dict | None = None) -> Recipe:
        steps: dict = {
            "merge": merge_step,
            "assess": {"action": "stop", "message": "Assess."},
            "cleanup_failure": {"action": "stop", "message": "Cleanup."},
            "push": {"action": "stop", "message": "Push."},
        }
        if extra_steps:
            steps.update(extra_steps)
        return _make_workflow(steps)

    def test_predicate_on_result_on_success_mutually_exclusive(self) -> None:
        """Step with predicate on_result (list) + on_success → validation error."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_success": "push",  # mutually exclusive
            }
        )
        errors = validate_recipe(wf)
        assert any("on_result" in e and "on_success" in e for e in errors)

    def test_predicate_condition_invalid_route_target_rejected(self) -> None:
        """A condition referencing an unknown step name is a validation error."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "nonexistent_step"},
                    {"route": "push"},
                ],
            }
        )
        errors = validate_recipe(wf)
        assert any("nonexistent_step" in e for e in errors)

    def test_predicate_condition_route_valid_step_accepted(self) -> None:
        """All condition routes pointing to valid step names pass validation."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
            }
        )
        errors = validate_recipe(wf)
        assert errors == []

    def test_predicate_on_result_empty_conditions_rejected(self) -> None:
        """on_result with conditions=[] bypasses predicate path; emits field error.

        When StepResultRoute(conditions=[]) is constructed directly (bypassing _parse_step,
        which collapses empty conditions to on_result=None), the validator falls through to
        legacy format validation and emits an explicit error for the missing field.
        """
        from autoskillit.recipe.validator import validate_recipe

        recipe = Recipe(
            name="test-predicate-empty",
            description="test",
            steps={
                "start": RecipeStep(
                    tool="run_skill",
                    with_args={"skill_command": "x", "cwd": "y"},
                    on_result=StepResultRoute(conditions=[]),
                ),
                "done": RecipeStep(action="stop", message="done"),
            },
        )
        errors = validate_recipe(recipe)
        assert any("on_result.field must be non-empty" in e for e in errors)

    def test_predicate_format_with_on_failure_allowed(self) -> None:
        """validator.py must not reject on_failure alongside on_result.conditions."""
        from autoskillit.recipe.validator import validate_recipe

        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_failure": "cleanup_failure",
            }
        )
        errors = validate_recipe(wf)
        assert not any("mutually exclusive" in e for e in errors), errors

    def test_on_result_missing_failure_route_fires_for_predicate_format(self) -> None:
        """Predicate-format steps with no on_failure must trigger ERROR finding."""
        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                # no on_failure — should trigger finding
            }
        )
        findings = run_semantic_rules(wf)
        names = [f.rule for f in findings]
        assert "on-result-missing-failure-route" in names

    def test_on_result_missing_failure_route_clear_when_predicate_has_on_failure(self) -> None:
        """Predicate-format step with on_failure must not trigger the rule."""
        wf = self._make_merge_recipe(
            {
                "tool": "merge_worktree",
                "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                "on_result": [
                    {"when": "result.error", "route": "cleanup_failure"},
                    {"route": "push"},
                ],
                "on_failure": "cleanup_failure",
            }
        )
        findings = run_semantic_rules(wf)
        names = [f.rule for f in findings]
        assert "on-result-missing-failure-route" not in names


class TestPredicateBuildStepGraph:
    """_build_step_graph includes condition.route edges."""

    def test_build_step_graph_includes_condition_routes(self) -> None:
        """_build_step_graph produces edges for condition.route targets."""
        from autoskillit.recipe.validator import _build_step_graph

        wf = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "assess": {"action": "stop", "message": "Assess."},
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            }
        )
        graph = _build_step_graph(wf)
        assert "assess" in graph["merge"]
        assert "cleanup" in graph["merge"]
        assert "push" in graph["merge"]


class TestPredicateSemanticRules:
    """Semantic rules behave correctly for predicate-format on_result."""

    def test_unreachable_step_includes_condition_routes(self) -> None:
        """A step reachable only via condition route is NOT flagged as unreachable."""
        wf = _make_workflow(
            {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                    "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                },
                "assess": {"action": "stop", "message": "Assess."},
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            }
        )
        findings = run_semantic_rules(wf)
        unreachable = [f for f in findings if f.rule == "unreachable-step"]
        step_names = {f.step_name for f in unreachable}
        assert "assess" not in step_names
        assert "cleanup" not in step_names
        assert "push" not in step_names

    def test_on_result_missing_failure_route_still_fires_for_legacy_format(
        self,
    ) -> None:
        """RCA1 rule continues to fire for legacy format with no on_failure (no regression)."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    # no on_failure — the gap
                },
                "fix": {"action": "stop", "message": "Fix."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert any(f.rule == "on-result-missing-failure-route" for f in findings)
