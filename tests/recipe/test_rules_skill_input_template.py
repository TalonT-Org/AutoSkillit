"""Tests for unresolved templates in run_skill inputs.

Composite values such as ``PR-{pr_number}`` and exotic forms such as ``{0}``,
``{x!r}``, ``${x}``, and ``<x>`` remain outside the rule's scope. The
``dispatch_items`` exemption preserves inline arguments for per-item fan-out;
revisit that exemption with #5307.
"""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.registry import (
    RuleFinding,
    compute_recipe_validity,
    run_semantic_rules,
)
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE = "unresolved-skill-input-template"


def _skill_input_template_findings(
    with_args: dict[str, Any], *, step_name: str = "step"
) -> list[RuleFinding]:
    recipe = _make_workflow(
        {
            step_name: {
                "tool": "run_skill",
                "with": with_args,
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    return [finding for finding in run_semantic_rules(recipe) if finding.rule == _RULE]


def _assert_error_names_input(findings: list[RuleFinding], input_name: str) -> None:
    assert len(findings) == 1
    assert findings[0].severity == Severity.ERROR
    assert input_name in findings[0].message


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("{pr_number}", id="whole-placeholder"),
        pytest.param(" {pr_number} ", id="padded-placeholder"),
        pytest.param("{{ pr_number }}", id="double-braced-placeholder"),
        pytest.param("${{ result.pr_number }}", id="unknown-result-namespace"),
        pytest.param(
            "${{ context.pr_number }}/${{ result.x }}",
            id="context-does-not-hide-result-expression",
        ),
        pytest.param(
            "${{ inputs.pr_number }}/${{ result.x }}",
            id="input-does-not-hide-result-expression",
        ),
        pytest.param("${{ context.pr_number", id="unterminated-expression"),
        pytest.param("${{ context.pr_number | trim }}", id="filter-expression"),
        pytest.param(
            "${{AUTOSKILLIT_TEMP}}/${{ result.x }}",
            id="macro-does-not-hide-unresolved-expression",
        ),
    ],
)
def test_unresolved_structured_skill_input_templates_are_errors(value: str) -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:merge-pr",
            "skill_inputs": {
                "pr_number": value,
                "complexity": "${{ context.c }}",
            },
        }
    )

    _assert_error_names_input(findings, "pr_number")


@pytest.mark.parametrize(
    "skill_command",
    [
        pytest.param("/autoskillit:merge-pr {pr_number} simple", id="named-placeholder"),
        pytest.param("/autoskillit:merge-pr ${{ result.x }} simple", id="unresolved-expression"),
    ],
)
def test_unresolved_inline_skill_arguments_are_errors(skill_command: str) -> None:
    findings = _skill_input_template_findings({"skill_command": skill_command})

    _assert_error_names_input(findings, "pr_number")


@pytest.mark.parametrize(
    ("skill_command", "input_name", "value"),
    [
        pytest.param(
            "/autoskillit:merge-pr",
            "pr_number",
            "${{ context.pr_number }}",
            id="valid-context-reference",
        ),
        pytest.param(
            "/autoskillit:merge-pr",
            "pr_number",
            "iter_${{ context.n }}",
            id="expression-with-literal-prefix",
        ),
        pytest.param(
            "/autoskillit:merge-pr",
            "pr_number",
            "{{AUTOSKILLIT_TEMP}}/x",
            id="path-macro",
        ),
        pytest.param(
            "/autoskillit:merge-pr",
            "pr_number",
            "{{AUTOSKILLIT_TEMP}}",
            id="whole-value-brace-macro",
        ),
        pytest.param(
            "/autoskillit:merge-pr",
            "pr_number",
            "${{AUTOSKILLIT_TEMP}}",
            id="whole-value-expression-macro",
        ),
        pytest.param("/autoskillit:merge-pr", "pr_number", "{}", id="empty-braces"),
        pytest.param("/autoskillit:merge-pr", "pr_number", "42", id="string-literal"),
        pytest.param("/autoskillit:merge-pr", "pr_number", 42, id="integer-literal"),
        pytest.param(
            "/autoskillit:investigate",
            "topic",
            "${{ '--depth deep ' if inputs.depth == 'deep' else '' }}"
            "${{ inputs.task }} ${{ inputs.issue_url }}",
            id="remediation-investigate-topic",
        ),
    ],
)
def test_resolved_or_accepted_skill_input_values_are_clean(
    skill_command: str, input_name: str, value: Any
) -> None:
    skill_inputs = {input_name: value}
    if input_name == "pr_number":
        skill_inputs["complexity"] = "${{ context.c }}"
    findings = _skill_input_template_findings(
        {
            "skill_command": skill_command,
            "skill_inputs": skill_inputs,
        },
        step_name="investigate" if input_name == "topic" else "step",
    )

    assert findings == []


def test_dynamic_skill_with_context_path_placeholder_is_clean() -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:arch-lens-{slug}",
            "skill_inputs": {"context_path": "{context_path}"},
        }
    )

    assert findings == []


def test_simple_dispatch_items_allows_inline_per_item_skill_arguments() -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:merge-pr {pr_number} simple",
            "dispatch_items": "${{ context.items }}",
        }
    )

    assert findings == []


def test_dispatch_items_does_not_exempt_unresolved_expression() -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:merge-pr ${{ result.x }} simple",
            "dispatch_items": "${{ context.items }}",
        }
    )

    _assert_error_names_input(findings, "pr_number")


def test_merge_pr_structured_placeholder_reproduction_is_invalid() -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:merge-pr",
            "skill_inputs": {
                "complexity": "{complexity}",
                "pr_number": "{pr_number}",
            },
        }
    )

    assert len(findings) == 2
    assert all(finding.severity == Severity.ERROR for finding in findings)
    assert any("complexity" in finding.message for finding in findings)
    assert any("pr_number" in finding.message for finding in findings)
    assert compute_recipe_validity([], findings, []) is False
