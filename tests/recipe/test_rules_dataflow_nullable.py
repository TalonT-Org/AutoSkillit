"""Tests for the nullable-optional-context-ref semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestNullableOptionalContextRefRule:
    def test_optional_context_ref_to_non_nullable_input_is_error(self) -> None:
        recipe = _make_workflow(
            {
                "check_loop": {
                    "tool": "run_python",
                    "with": {
                        "callable": "autoskillit.smoke_utils.check_loop_iteration",
                        "args": {"current_iteration": "${{ context.ci_loop_count }}"},
                    },
                    "optional_context_refs": ["ci_loop_count"],
                    "on_result": {
                        "max_exceeded": {"'true'": "done", "'false'": "check_loop"}
                    },
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        nullable_findings = [
            f for f in findings if f.rule == "nullable-optional-context-ref"
        ]
        assert any(
            f.severity == Severity.ERROR for f in nullable_findings
        ), f"Expected ERROR finding for nullable-optional-context-ref, got: {nullable_findings}"

    def test_non_optional_context_ref_does_not_trigger_rule(self) -> None:
        recipe = _make_workflow(
            {
                "check_loop": {
                    "tool": "run_python",
                    "with": {
                        "callable": "autoskillit.smoke_utils.check_loop_iteration",
                        "args": {"current_iteration": "${{ context.ci_loop_count }}"},
                    },
                    "on_result": {
                        "max_exceeded": {"'true'": "done", "'false'": "check_loop"}
                    },
                },
                "done": {"action": "stop", "message": "done"},
            }
        )
        findings = run_semantic_rules(recipe)
        nullable_findings = [
            f for f in findings if f.rule == "nullable-optional-context-ref"
        ]
        assert not any(
            f.severity == Severity.ERROR for f in nullable_findings
        ), f"Should not flag required context refs, got: {nullable_findings}"
