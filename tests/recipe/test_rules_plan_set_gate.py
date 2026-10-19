"""Dominance checks for gates that verify sealed publication obligations."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from autoskillit.recipe.schema import Recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

RULE_ID = "stage-obligation-gate-dominance"
GATE_CALLABLE = "autoskillit.recipe._cmd_rpc.verify_stage_obligations"
PIPELINE_RECIPES = ("implementation", "remediation", "implementation-groups")
OUT_OF_SCOPE_RECIPES = ("research", "research-implement", "merge-prs")
EXISTING_ERROR_RULES = {
    "wait-for-merge-queue-routing-conforms-to-expected-targets",
    "release-issue-on-unconfirmed-merge",
}


def _load_recipe(name: str) -> Recipe:
    return load_recipe(builtin_recipes_dir() / f"{name}.yaml")


def _gate(recipe: Recipe, name: str):
    step = recipe.steps[name]
    assert step.tool == "run_python"
    assert step.with_args.get("callable") == GATE_CALLABLE
    return step


def _rule_findings(recipe: Recipe):
    return [finding for finding in run_semantic_rules(recipe) if finding.rule == RULE_ID]


def _replace_route_target(recipe: Recipe, old: str, new: str) -> None:
    for step in recipe.steps.values():
        for field in (
            "on_success",
            "on_failure",
            "on_context_limit",
            "on_rate_limit",
            "on_exhausted",
            "on_skip",
        ):
            if getattr(step, field) == old:
                setattr(step, field, new)
        if step.on_result is None:
            continue
        step.on_result.routes = {
            route_key: new if target == old else target
            for route_key, target in step.on_result.routes.items()
        }
        for condition in step.on_result.conditions:
            if condition.route == old:
                condition.route = new


def _delete_gate_and_bypass(recipe: Recipe, gate_name: str, bypass_target: str) -> None:
    _gate(recipe, gate_name)
    _replace_route_target(recipe, gate_name, bypass_target)
    del recipe.steps[gate_name]


def test_rule_is_registered() -> None:
    assert RULE_ID in {rule.name for rule in _RULE_REGISTRY}


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_rule_is_silent_on_gated_pipeline_recipes(recipe_name: str) -> None:
    recipe = _load_recipe(recipe_name)
    assert any(step.with_args.get("callable") == GATE_CALLABLE for step in recipe.steps.values())
    assert not _rule_findings(recipe)


@pytest.mark.parametrize("recipe_name", OUT_OF_SCOPE_RECIPES)
def test_rule_does_not_reject_out_of_scope_recipes(recipe_name: str) -> None:
    assert not _rule_findings(_load_recipe(recipe_name))


@pytest.mark.parametrize(
    ("recipe_name", "gate_name", "bypass_target"),
    [
        ("implementation", "pr_gate", "register_clone_success"),
        (
            "remediation",
            "pr_gate_unconfirmed",
            "analyze_pipeline_health_unconfirmed",
        ),
        ("implementation", "pr_gate_no_changes", "close_issue_no_changes"),
        (
            "implementation",
            "pr_gate_already_done",
            "close_issue_already_done",
        ),
    ],
)
def test_rule_rejects_a_gate_deleted_from_a_success_path(
    recipe_name: str, gate_name: str, bypass_target: str
) -> None:
    recipe = _load_recipe(recipe_name)
    _delete_gate_and_bypass(recipe, gate_name, bypass_target)

    findings = _rule_findings(recipe)
    assert findings
    assert all(finding.severity == Severity.ERROR for finding in findings)


def test_rule_rejects_gate_failure_routed_to_success() -> None:
    recipe = _load_recipe("implementation")
    gate = _gate(recipe, "pr_gate")
    assert gate.on_result is not None
    failure_arm = next(
        condition for condition in gate.on_result.conditions if condition.when is None
    )
    failure_arm.route = "register_clone_success"

    findings = _rule_findings(recipe)
    assert findings
    assert all(finding.severity == Severity.ERROR for finding in findings)


def test_rule_rejects_skippable_gate() -> None:
    recipe = _load_recipe("implementation")
    _gate(recipe, "pr_gate").skip_when_false = "inputs.open_pr"

    findings = _rule_findings(recipe)
    assert findings
    assert all(finding.severity == Severity.ERROR for finding in findings)


@pytest.mark.parametrize("recipe_name", PIPELINE_RECIPES)
def test_existing_error_rules_remain_green(recipe_name: str) -> None:
    recipe = _load_recipe(recipe_name)
    findings = run_semantic_rules(recipe)
    errors = [
        finding
        for finding in findings
        if finding.rule in EXISTING_ERROR_RULES and finding.severity == Severity.ERROR
    ]
    assert not errors
