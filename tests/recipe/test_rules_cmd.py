"""Tests for run_cmd echo-capture alignment rules."""

from __future__ import annotations

import pytest

from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict) -> object:
    """Build a minimal Recipe from a dict of step dicts, with a terminal END step."""
    all_steps = {**steps, "END": {"action": "stop", "message": "Done"}}
    return _make_workflow(all_steps)


def test_emit_alignment_errors_on_missing_echo():
    """run_cmd with capture key K but no echo "K=..." in cmd → ERROR."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "RESULT=$(compute_it)"},
                "capture": {"my_path": "${{ result.my_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" in codes
    finding = next(f for f in findings if f.rule == "run-cmd-emit-alignment")
    assert "step_a" in finding.step_name
    assert "my_path" in finding.message


def test_emit_alignment_passes_with_echo():
    """run_cmd with matching echo "K=..." → no alignment error."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'RESULT=$(compute_it) && echo "my_path=${RESULT}"'},
                "capture": {"my_path": "${{ result.my_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" not in codes


def test_emit_alignment_ignores_stdout_capture():
    """capture: {K: "${{ result.stdout }}"} does not require echo."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git rev-parse HEAD"},
                "capture": {"sha": "${{ result.stdout | trim }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-emit-alignment" not in codes


def test_emit_alignment_ignores_exit_code_capture():
    """capture: {K: "${{ result.exit_code }}"} does not require echo."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "test -f something"},
                "capture": {"ok": "${{ result.exit_code }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-emit-alignment" for f in findings)


def test_emit_alignment_ignores_run_skill():
    """run_skill steps are not subject to the emit-alignment rule."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_skill",
                "with": {"skill_command": "/foo:bar"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-emit-alignment" for f in findings)


def test_find_rediscovery_warns_on_find_sort_tail():
    """find|sort|tail -1 in run_cmd cmd → WARNING."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "DIR=$(find /some/path -maxdepth 1 -type d"
                        " -name '????-??-??-*' | sort | tail -1)"
                    )
                },
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "run-cmd-find-rediscovery" in codes


def test_find_rediscovery_no_warning_without_heuristic():
    """find without sort|tail does not trigger the warning."""
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "find /path -name '*.md' | xargs wc -l"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "run-cmd-find-rediscovery" for f in findings)
