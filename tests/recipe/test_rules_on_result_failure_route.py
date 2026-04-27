from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestOnResultMissingFailureRoute:
    """RCA: on-result-missing-failure-route semantic rule."""

    def test_RCA1_tool_step_on_result_no_on_failure_fires_error(self) -> None:
        """RCA1: run_skill step with on_result but no on_failure → Severity.ERROR finding."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    # no on_failure — the gap
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        rule_names = [f.rule for f in errors]
        assert any(
            f.rule == "on-result-missing-failure-route" and f.step_name == "audit" for f in errors
        ), f"Expected on-result-missing-failure-route ERROR on 'audit'. Got: {rule_names}"

    def test_RCA2_python_step_on_result_no_on_failure_fires_error(self) -> None:
        """RCA2: python step with on_result but no on_failure → Severity.ERROR."""
        wf = _make_workflow(
            {
                "check": {
                    "python": "mymod.check_result",
                    "on_result": {"field": "status", "routes": {"ok": "done", "fail": "fix"}},
                    # no on_failure
                },
                "fix": {"action": "stop", "message": "Fix."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.rule == "on-result-missing-failure-route" for f in errors)

    def test_RCA3_on_result_with_on_failure_no_finding(self) -> None:
        """RCA3: on_result + on_failure present → rule does not fire."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    "on_failure": "done",
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "on-result-missing-failure-route" for f in findings)

    def test_RCA4_action_route_on_result_no_on_failure_not_an_error(self) -> None:
        """RCA4: action:route with on_result but no on_failure → NOT flagged.
        Agent routing decisions are not MCP tool invocations; they cannot fail
        the same way and are exempt from this rule.
        """
        wf = _make_workflow(
            {
                "decide": {
                    "action": "route",
                    "on_result": {
                        "field": "parts",
                        "routes": {"more": "implement", "done": "finish"},
                    },
                    # no on_failure — intentional for action:route
                },
                "implement": {"action": "stop", "message": "Implement."},
                "finish": {"action": "stop", "message": "Finish."},
            }
        )
        findings = run_semantic_rules(wf)
        assert not any(f.rule == "on-result-missing-failure-route" for f in findings)

    def test_RCA5_optional_true_plus_on_result_no_on_failure_fires(self) -> None:
        """RCA5: optional:true does not exempt a step from needing on_failure."""
        wf = _make_workflow(
            {
                "audit": {
                    "tool": "run_skill",
                    "with": {"skill_command": "/autoskillit:audit-impl plan.md impl main"},
                    "capture": {"verdict": "${{ result.verdict }}"},
                    "on_result": {"field": "verdict", "routes": {"GO": "done", "NO GO": "fix"}},
                    "optional": True,
                    # no on_failure
                },
                "fix": {"action": "stop", "message": "Fix needed."},
                "done": {"action": "stop", "message": "Done."},
            }
        )
        findings = run_semantic_rules(wf)
        errors = [f for f in findings if f.severity == Severity.ERROR]
        assert any(f.rule == "on-result-missing-failure-route" for f in errors)
