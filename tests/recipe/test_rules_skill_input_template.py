"""Tests for unresolved templates in run_skill inputs.

Composite values such as ``PR-{pr_number}`` and exotic forms such as ``{0}``,
``{x!r}``, ``${x}``, and ``<x>`` remain outside the rule's scope. The
``dispatch_items`` exemption applies only to the per-item slot of an accepted
``dispatch_items`` fan-out (origin ``DISPATCH_ITEM``).
"""

from __future__ import annotations

from typing import Any

import pytest

from autoskillit.core import BindingFailureCode, RecipeBindingProjection
from autoskillit.core.types import Severity
from autoskillit.recipe._binding import bind_recipe
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


def _planner_dispatch_with(
    item_argument: str, *, dispatch_items: str = "${{ context.phase_ids }}"
) -> dict[str, Any]:
    return {
        "skill_command": (
            f"/autoskillit:planner-elaborate-phase /tmp/plan.json {item_argument} /tmp/phases"
        ),
        "dispatch_items": dispatch_items,
    }


@pytest.mark.parametrize(
    "item_argument",
    [
        pytest.param("{phase_id}", id="named-placeholder"),
        pytest.param('"{ phase_id }"', id="quoted-inline-argument"),
        pytest.param("{phase-id}", id="hyphenated-placeholder"),
        pytest.param("{phase.id}", id="dotted-placeholder"),
    ],
)
def test_dispatch_items_without_producer_reports_placeholder(item_argument: str) -> None:
    findings = _skill_input_template_findings(
        _planner_dispatch_with(item_argument, dispatch_items="P1,P2"),
        step_name="elaborate_phases",
    )

    _assert_error_names_input(findings, "target_phase_id")


def test_dispatch_step_with_two_placeholder_names_reports_them() -> None:
    findings = _skill_input_template_findings(
        {
            "skill_command": "/autoskillit:planner-elaborate-phase",
            "skill_inputs": {
                "plan_snapshot_path": "/tmp/plan.json",
                "target_phase_id": "{phase_id}",
                "output_dir": "{other}",
            },
            "dispatch_items": "P1,P2",
        },
        step_name="elaborate_phases",
    )

    assert len(findings) == 2
    assert all(finding.severity == Severity.ERROR for finding in findings)
    assert any("target_phase_id" in finding.message for finding in findings)
    assert any("output_dir" in finding.message for finding in findings)


@pytest.mark.parametrize(
    "item_argument",
    [
        pytest.param("prefix{phase_id}", id="embedded-placeholder"),
        pytest.param("{phase_id", id="unterminated-placeholder"),
        pytest.param("{phase_id} {phase_id}", id="duplicate-slot"),
    ],
)
def test_invalid_dispatch_shape_reports_binding_error(item_argument: str) -> None:
    recipe = _make_workflow(
        {
            "elaborate_phases": {
                "tool": "run_skill",
                "with": _planner_dispatch_with(item_argument),
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    invocation = bind_recipe(recipe).for_step("elaborate_phases")
    assert invocation is not None
    failure = next(
        failure
        for failure in invocation.failures
        if failure.code is BindingFailureCode.INVALID_DISPATCH_ITEMS
    )
    assert failure.name == "dispatch_items"

    findings = run_semantic_rules(recipe)
    assert any(
        finding.rule == "invalid-skill-invocation-binding"
        and finding.severity == Severity.ERROR
        and "dispatch_items" in finding.message
        for finding in findings
    )


def test_well_formed_dispatch_slot_is_not_reported() -> None:
    with_args = _planner_dispatch_with("{phase_id}")
    recipe = _make_workflow(
        {
            "elaborate_phases": {
                "tool": "run_skill",
                "with": with_args,
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    invocation = bind_recipe(recipe).for_step("elaborate_phases")
    assert invocation is not None
    assert not any(
        failure.code is BindingFailureCode.INVALID_DISPATCH_ITEMS
        for failure in invocation.failures
    )
    assert not any(
        finding.rule == "invalid-skill-invocation-binding" and "dispatch_items" in finding.message
        for finding in run_semantic_rules(recipe)
    )
    assert _skill_input_template_findings(with_args, step_name="elaborate_phases") == []


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
        *[
            pytest.param("/autoskillit:merge-pr", "pr_number", value, id=f"literal-{value}")
            for value in ("PR-{pr_number}", "{0}", "{x!r}", "${x}", "<x>")
        ],
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


def test_dynamic_skill_templates_are_deferred_to_fanout() -> None:
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
    assert (
        compute_recipe_validity([], findings, [], binding_projection=RecipeBindingProjection({}))
        is False
    )
