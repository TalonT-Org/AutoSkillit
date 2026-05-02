"""Tests for inline-script-in-cmd and inline-python-in-cmd lint rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _make_recipe(steps: dict) -> object:
    """Build a minimal Recipe from a dict of step dicts, with a terminal END step."""
    all_steps = {**steps, "END": {"action": "stop", "message": "Done"}}
    return _make_workflow(all_steps)


# ---------------------------------------------------------------------------
# inline-script-in-cmd: ERROR cases
# ---------------------------------------------------------------------------


def test_inline_script_fires_on_control_flow():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {
                    "cmd": 'VAR=1 && if [ "$VAR" -gt 0 ]; then echo "yes"; else echo "no"; fi'
                },
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-script-in-cmd" in codes


def test_inline_script_fires_on_for_loop():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'for i in $(seq 1 10); do echo "$i"; done'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-script-in-cmd" in codes


def test_inline_script_fires_on_while_loop():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "while true; do sleep 1; done"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-script-in-cmd" in codes


def test_inline_script_fires_on_excessive_vars_and_chains():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'A=$(foo) && B=$(bar) && C=$(baz) && echo "$A $B $C" && cleanup'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-script-in-cmd" in codes


def test_inline_script_fires_on_bash_builtins():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'declare -A map && map[key]=value && echo "${map[key]}"'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-script-in-cmd" in codes


def test_inline_script_error_on_control_flow():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'if [ -f foo ]; then echo "yes"; else echo "no"; fi'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    inline_findings = [f for f in findings if f.rule == "inline-script-in-cmd"]
    assert len(inline_findings) > 0
    assert inline_findings[0].severity == Severity.ERROR


# ---------------------------------------------------------------------------
# inline-script-in-cmd: WARNING cases
# ---------------------------------------------------------------------------


def test_inline_script_warning_on_multiline_with_vars():
    cmd = (
        "REMOTE=$(git remote) &&\n"
        "SHA=$(git rev-parse HEAD) &&\n"
        "BRANCH=$(git branch --show-current) &&\n"
        'echo "$REMOTE $SHA $BRANCH"'
    )
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": cmd},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    inline_findings = [f for f in findings if f.rule == "inline-script-in-cmd"]
    assert len(inline_findings) > 0
    assert inline_findings[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# inline-python-in-cmd
# ---------------------------------------------------------------------------


def test_inline_python_fires_on_python3_c():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": 'python3 -c "import json; print(json.dumps({}))"'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    codes = [f.rule for f in findings]
    assert "inline-python-in-cmd" in codes


# ---------------------------------------------------------------------------
# Clean cases: rule must NOT fire
# ---------------------------------------------------------------------------


def test_inline_script_clean_for_simple_commands():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "git push -u origin HEAD"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "inline-script-in-cmd" for f in findings)


def test_inline_script_clean_for_flag_continuation():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {"cmd": "gh pr create --base main --head feat --title 'foo' --body 'bar'"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "inline-script-in-cmd" for f in findings)


def test_inline_script_clean_for_jq_expressions():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_cmd",
                "with": {
                    "cmd": (
                        "gh pr view 42 --json statusCheckRollup "
                        "--jq '.statusCheckRollup | if length == 0 "
                        'then "none" else "exists" end\''
                    )
                },
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "inline-script-in-cmd" for f in findings)


def test_inline_script_fires_for_formerly_allowlisted_steps():
    recipe = _make_recipe(
        {
            "compute_branch": {
                "tool": "run_cmd",
                "with": {"cmd": 'SLUG="foo" && if [ -n "$SLUG" ]; then printf "%s" "$SLUG"; fi'},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "inline-script-in-cmd" for f in findings)


def test_inline_script_ignores_non_run_cmd_steps():
    recipe = _make_recipe(
        {
            "step_a": {
                "tool": "run_python",
                "with": {"callable": "autoskillit.smoke_utils.check_loop_iteration"},
                "on_success": "END",
            }
        }
    )
    findings = run_semantic_rules(recipe)
    assert all(f.rule != "inline-script-in-cmd" for f in findings)
