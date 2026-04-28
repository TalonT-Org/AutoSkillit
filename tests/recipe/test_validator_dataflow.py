"""Tests for recipe dataflow quality analysis and context-ref validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    iter_steps_with_context,
    load_recipe,
)
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import analyze_dataflow, validate_recipe
from tests.recipe.conftest import _make_workflow, _write_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# TestDataFlowQuality — migrated from test_recipe_parser.py
# ---------------------------------------------------------------------------


class TestDataFlowQuality:
    """Tests for data-flow quality analysis (DFQ prefix)."""

    def _make_recipe(self, steps: dict[str, dict]) -> Recipe:
        parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
        return Recipe(
            name="test",
            description="test",
            steps=parsed_steps,
            kitchen_rules=["test"],
        )

    # DFQ1
    def test_analyze_dataflow_returns_report(self) -> None:
        from autoskillit.recipe.schema import DataFlowReport

        wf = self._make_recipe(
            {
                "run": {"tool": "test_check", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert isinstance(report, DataFlowReport)
        assert isinstance(report.warnings, list)
        assert isinstance(report.summary, str)
        assert report.warnings == []
        assert report.summary is not None
        assert len(report.summary) > 0

    # DFQ2
    def test_dead_output_detected(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "finish",
                },
                "finish": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 1
        assert dead[0].step_name == "impl"
        assert dead[0].field == "worktree_path"

    # DFQ3
    def test_consumed_output_not_flagged(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "test",
                },
                "test": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT"]
        assert len(dead) == 0

    # DFQ5
    def test_implicit_handoff_detected(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 1
        assert implicit[0].step_name == "impl"

    # DFQ6
    def test_non_skill_step_no_implicit_handoff(self) -> None:
        wf = self._make_recipe(
            {
                "test": {"tool": "test_check", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        implicit = [w for w in report.warnings if w.code == "IMPLICIT_HANDOFF"]
        assert len(implicit) == 0

    # DFQ11
    def test_summary_reports_counts(self) -> None:
        wf = self._make_recipe(
            {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "run",
                },
                "run": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done"},
            }
        )
        report = analyze_dataflow(wf)
        assert "2 data-flow warnings" in report.summary

    # DFQ13
    def test_bundled_recipes_produce_reports(self) -> None:
        from autoskillit.recipe.schema import DataFlowReport

        wf_dir = builtin_recipes_dir()
        yaml_files = list(wf_dir.glob("*.yaml")) + list(wf_dir.glob("*.yml"))
        assert len(yaml_files) > 0
        for yaml_file in yaml_files:
            wf = load_recipe(yaml_file)
            report = analyze_dataflow(wf)
            assert isinstance(report, DataFlowReport)
            assert isinstance(report.warnings, list)


# ---------------------------------------------------------------------------
# iter_steps_with_context integration
# ---------------------------------------------------------------------------


def test_validate_recipe_uses_iter_steps_with_context_for_capture_refs(tmp_path: Path) -> None:
    """validate_recipe catches context refs not captured by preceding steps."""
    data = {
        "name": "ctx-test",
        "description": "Context validation test",
        "kitchen_rules": ["test"],
        "steps": {
            "step1": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello"},
                "on_success": "step2",
            },
            "step2": {
                "tool": "test_check",
                "with": {"worktree_path": "${{ context.worktree_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "ok"},
        },
    }
    wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
    # step1 has no captures, so step2 should see empty context
    steps = list(iter_steps_with_context(wf))
    assert steps[1][2] == frozenset()
    # validate_recipe should catch the unsatisfied context reference
    errors = validate_recipe(wf)
    assert any("worktree_path" in e for e in errors)


# ---------------------------------------------------------------------------
# isinstance guard tests (T_GD1, T_GV1)
# ---------------------------------------------------------------------------


class TestIsInstanceGuards:
    def test_gd1_analyze_dataflow_no_raise_on_bool_with_arg(self) -> None:
        """T_GD1: _detect_dead_outputs does not raise TypeError for boolean with_args."""
        data = {
            "name": "bool-guard",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "plan": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:make-plan do the task"},
                    "capture": {"plan_path": "${{ result.plan_path }}"},
                    "on_success": "downstream",
                },
                "downstream": {
                    "tool": "run_cmd",
                    "with": {"worktree_path": True},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        recipe = _parse_recipe(data)
        report = analyze_dataflow(recipe)
        assert report is not None

    def test_gv1_validate_recipe_no_raise_on_bool_with_arg(self) -> None:
        """T_GV1: validate_recipe does not raise TypeError for boolean with_args."""
        data = {
            "name": "bool-guard-validate",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "step1": {
                    "tool": "run_cmd",
                    "with": {"flag": True},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        recipe = _parse_recipe(data)
        result = validate_recipe(recipe)
        assert isinstance(result, list)
        assert not any("dead" in str(f).lower() for f in result), (
            "DEAD_OUTPUT rule fired on a valid recipe — false positive"
        )


# ---------------------------------------------------------------------------
# on_result self-consumption tests (T_OR1, T_OR2)
# ---------------------------------------------------------------------------


class TestOnResultConsumption:
    def test_or1_on_result_field_is_not_dead_output(self) -> None:
        """T_OR1: verdict captured and used as on_result.field is NOT flagged DEAD_OUTPUT."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": {
                    "field": "verdict",
                    "routes": {"GO": "done", "NO GO": "done"},
                },
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert dead == []

    def test_or2_different_on_result_field_flags_dead_output(self) -> None:
        """T_OR2: verdict is flagged DEAD_OUTPUT when on_result.field is a different key."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": {
                    "field": "restart_scope",
                    "routes": {"full_restart": "done", "partial_restart": "done"},
                },
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        report = analyze_dataflow(recipe)
        dead = [w for w in report.warnings if w.code == "DEAD_OUTPUT" and w.field == "verdict"]
        assert len(dead) == 1
