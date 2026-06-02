"""Tests for stop-sentinel-success-mismatch semantic validation rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

RULE_NAME = "stop-sentinel-success-mismatch"


class TestStopSentinelDirection:
    def test_rule_registered(self) -> None:
        from autoskillit.recipe.registry import _RULE_REGISTRY

        rule_names = [r.name for r in _RULE_REGISTRY]
        assert RULE_NAME in rule_names

    def test_catches_escalate_stop_with_success_true(self) -> None:
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done",
                    "on_failure": "escalate_stop",
                },
                "done": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "done"}',
                },
                "escalate_stop": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "escalated"}',
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) >= 1
        assert matched[0].step_name == "escalate_stop"
        assert matched[0].severity == Severity.ERROR

    def test_allows_done_with_success_true(self) -> None:
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done",
                    "on_failure": "escalate_failure",
                },
                "done": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "done"}',
                },
                "escalate_failure": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": false, "reason": "failed"}',
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) == 0

    def test_catches_failure_path_stop_with_success_true(self) -> None:
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done",
                    "on_failure": "error_stop",
                },
                "done": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "done"}',
                },
                "error_stop": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "oops"}',
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) >= 1
        assert any(f.step_name == "error_stop" for f in matched)

    def test_allows_failure_path_stop_with_success_false(self) -> None:
        recipe = _make_workflow(
            {
                "start": {
                    "tool": "run_python",
                    "with": {"callable": "test"},
                    "on_success": "done",
                    "on_failure": "handle_error",
                },
                "handle_error": {
                    "tool": "run_python",
                    "with": {"callable": "cleanup"},
                    "on_success": "failure_stop",
                    "on_failure": "failure_stop",
                },
                "done": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": true, "reason": "done"}',
                },
                "failure_stop": {
                    "action": "stop",
                    "message": 'Emit sentinel: {"success": false, "reason": "failed"}',
                },
            }
        )
        findings = run_semantic_rules(recipe)
        matched = [f for f in findings if f.rule == RULE_NAME]
        assert len(matched) == 0

    def test_bundled_recipes_all_stops_correct(self) -> None:
        recipes_dir = builtin_recipes_dir()
        for name in ["implementation", "remediation", "implementation-groups"]:
            recipe = load_recipe(recipes_dir / f"{name}.yaml")
            findings = run_semantic_rules(recipe)
            matched = [f for f in findings if f.rule == RULE_NAME]
            assert not matched, f"{name}: {[(f.step_name, f.message) for f in matched]}"
