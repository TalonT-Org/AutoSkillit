"""Tests for exhaustive server-authored audit outcome recipe routing."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "audit-outcome-routing-incomplete"
_RECIPE_NAMES = (
    "implementation",
    "implementation-groups",
    "remediation",
    "merge-prs",
    "research",
    "research-implement",
)


def _recipe(name: str = "implementation"):
    return load_recipe(builtin_recipes_dir() / f"{name}.yaml")


def _findings(recipe) -> list:
    return [finding for finding in run_semantic_rules(recipe) if finding.rule == _RULE_NAME]


@pytest.mark.parametrize("recipe_name", _RECIPE_NAMES)
def test_bundled_audit_protocol_recipes_route_every_outcome(recipe_name: str) -> None:
    assert _findings(_recipe(recipe_name)) == []


def test_rule_rejects_missing_server_owned_capture() -> None:
    recipe = _recipe()
    recipe.steps["audit_impl"].capture.pop("audit_attempt_id")

    findings = _findings(recipe)

    assert len(findings) == 1
    assert "capture audit_attempt_id" in findings[0].message


def test_rule_rejects_correction_token_inside_skill_inputs() -> None:
    recipe = _recipe()
    audit_step = recipe.steps["audit_impl"]
    audit_step.with_args.pop("retry_after_audit_attempt_id")
    audit_step.with_args["skill_inputs"]["retry_after_audit_attempt_id"] = (
        "${{ context.audit_attempt_id }}"
    )

    findings = _findings(recipe)

    assert len(findings) == 1
    assert "top-level" in findings[0].message
    assert "must not appear inside skill_inputs" in findings[0].message


def test_rule_rejects_child_authored_verdict_routing() -> None:
    recipe = _recipe()
    conditions = recipe.steps["audit_impl"].on_result.conditions
    published_go = next(
        condition
        for condition in conditions
        if condition.when
        and "PUBLISHED" in condition.when
        and "EXACT_REPLAY" not in condition.when
        and "== GO" in condition.when
    )
    published_go.when = "${{ result.verdict }} == GO"

    findings = _findings(recipe)

    assert len(findings) == 1
    assert "child-authored result.verdict" in findings[0].message


def test_rule_rejects_semantic_and_infrastructure_route_conflation() -> None:
    recipe = _recipe()
    conditions = recipe.steps["audit_impl"].on_result.conditions
    semantic_route = next(
        condition.route
        for condition in conditions
        if condition.when and "SEMANTIC_REJECTED" in condition.when
    )
    conflict = next(
        condition for condition in conditions if condition.when and "CONFLICT" in condition.when
    )
    conflict.route = semantic_route

    findings = _findings(recipe)

    assert len(findings) == 1
    assert "semantic correction must not share" in findings[0].message


def test_rule_rejects_status_routing_after_verdict_branches() -> None:
    recipe = _recipe()
    conditions = recipe.steps["audit_impl"].on_result.conditions
    conflict_index = next(
        index
        for index, condition in enumerate(conditions)
        if condition.when and "CONFLICT" in condition.when
    )
    conflict = conditions.pop(conflict_index)
    default_index = next(
        index for index, condition in enumerate(conditions) if condition.when is None
    )
    conditions.insert(default_index, conflict)

    findings = _findings(recipe)

    assert len(findings) == 1
    assert "statuses must route first" in findings[0].message
