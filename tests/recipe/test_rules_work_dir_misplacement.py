"""Tests for the work-dir-arg-misplacement semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# T4a: rule fires when work_dir in nested args for callable that doesn't accept it
def test_work_dir_misplacement_rule_fires():
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.smoke_utils.annotate_pr_diff",
                    "args": {
                        "pr_number": "1",
                        "cwd": "/path",
                        "output_dir": ".autoskillit/temp/test",
                        "work_dir": "/path",
                    },
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    misplaced = [f for f in findings if f.rule == "work-dir-arg-misplacement"]
    assert len(misplaced) == 1
    assert misplaced[0].severity == Severity.WARNING


# T4b: rule does NOT fire when callable accepts work_dir
def test_work_dir_misplacement_rule_silent_for_valid_callable():
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.recipe._cmd_rpc.review_path_rebase",
                    "args": {"work_dir": "/path", "base_branch": "main"},
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    misplaced = [f for f in findings if f.rule == "work-dir-arg-misplacement"]
    assert len(misplaced) == 0


# T4c: rule does NOT fire when work_dir is top-level (correct usage)
def test_work_dir_misplacement_rule_silent_for_toplevel():
    recipe = _make_workflow(
        {
            "step": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.smoke_utils.annotate_pr_diff",
                    "work_dir": "/path",
                    "pr_number": "1",
                    "cwd": "/path",
                    "output_dir": ".autoskillit/temp/test",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    misplaced = [f for f in findings if f.rule == "work-dir-arg-misplacement"]
    assert len(misplaced) == 0
