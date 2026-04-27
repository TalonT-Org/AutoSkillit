"""Tests for the non-unique-output-path lint rule."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestNonUniqueOutputPath:
    def test_bare_temp_path_in_output_dir_errors(self) -> None:
        wf = _make_workflow(
            {
                "init_step": {
                    "tool": "run_python",
                    "with": {
                        "callable": "some.callable",
                        "output_dir": "{{AUTOSKILLIT_TEMP}}/planner",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.rule == "non-unique-output-path"]
        assert any(f.severity == Severity.ERROR and f.step_name == "init_step" for f in errors)

    def test_bare_temp_path_in_cmd_errors(self) -> None:
        wf = _make_workflow(
            {
                "mk_step": {
                    "tool": "run_cmd",
                    "with": {
                        "command": "mkdir -p {{AUTOSKILLIT_TEMP}}/myrecipe/data",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.rule == "non-unique-output-path"]
        assert any(f.severity == Severity.ERROR and f.step_name == "mk_step" for f in errors)

    def test_bare_temp_path_in_env_var_errors(self) -> None:
        wf = _make_workflow(
            {
                "env_step": {
                    "tool": "run_cmd",
                    "with": {
                        "command": "/some-skill",
                        "env": {"MY_FILE": "{{AUTOSKILLIT_TEMP}}/recipe/data.json"},
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.rule == "non-unique-output-path"]
        assert any(f.severity == Severity.ERROR and f.step_name == "env_step" for f in errors)

    def test_context_scoped_path_passes(self) -> None:
        wf = _make_workflow(
            {
                "scoped_step": {
                    "tool": "run_python",
                    "with": {
                        "callable": "some.callable",
                        "output_dir": "${{ context.planner_dir }}",
                    },
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "non-unique-output-path" for f in findings)

    def test_message_field_not_flagged(self) -> None:
        wf = _make_workflow(
            {
                "done": {
                    "action": "stop",
                    "message": "Output at {{AUTOSKILLIT_TEMP}}/planner/",
                },
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "non-unique-output-path" for f in findings)
