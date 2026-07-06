"""Tests for the verdict-context-precondition semantic rule.

Verifies that a ``run_skill`` step invoking a skill whose ``allowed_values``
contains a CI-context-dependent verdict (``ci_only_failure``) does NOT route
that verdict to escalation without referencing CI context in its
``skill_command``. Without CI context the verdict is semantically impossible
to emit, so routing it to escalation creates a dead-but-deadly trap.
"""

from __future__ import annotations

import pytest

import autoskillit.recipe.contracts as _contracts
from autoskillit.core.types import Severity
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_SKILL_NAME = "resolve-failures"

_MANIFEST: dict = {
    "version": "0.1.0",
    "skills": {
        _SKILL_NAME: {
            "inputs": [],
            "outputs": [
                {
                    "name": "verdict",
                    "type": "string",
                    "allowed_values": [
                        "real_fix",
                        "already_green",
                        "flake_suspected",
                        "ci_only_failure",
                        "no_test_infrastructure",
                    ],
                }
            ],
        }
    },
}


def _make_fix_step_no_ci_context() -> RecipeStep:
    """Pre-merge fix step: no CI context, routes ci_only_failure to escalation."""
    return RecipeStep(
        name="fix",
        tool="run_skill",
        with_args={
            "skill_command": "/autoskillit:resolve-failures main main main",
        },
        capture={"verdict": "${{ result.verdict }}"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    route="release_issue_failure",
                    when="${{ result.verdict }} == ci_only_failure",
                ),
                StepResultCondition(route="test", when="true"),
            ]
        ),
    )


def _make_recipe_with_fix_step() -> Recipe:
    recipe = Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "fix": _make_fix_step_no_ci_context(),
            "release_issue_failure": RecipeStep(
                action="stop",
                message="escalation",
            ),
            "test": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo test"},
            ),
        },
    )
    return recipe


def _make_recipe_with_resolve_ci() -> Recipe:
    """Post-CI resolve_ci step: skill_command references CI context vars."""
    return Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "resolve_ci": RecipeStep(
                name="resolve_ci",
                tool="run_skill",
                with_args={
                    "skill_command": (
                        "/autoskillit:resolve-failures main main main "
                        "${{ context.ci_conclusion }} "
                        "${{ context.ci_failed_jobs }} "
                        "${{ context.diagnosis_path }}"
                    ),
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="release_issue_failure",
                            when="${{ result.verdict }} == ci_only_failure",
                        ),
                        StepResultCondition(route="re_push", when="true"),
                    ]
                ),
            ),
            "release_issue_failure": RecipeStep(
                action="stop",
                message="escalation",
            ),
            "re_push": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo push"},
            ),
        },
    )


def _make_recipe_with_non_terminal_route() -> Recipe:
    """Step that routes ci_only_failure to a continuation step (re-diagnosis), not escalation."""
    return Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "resolve_local": RecipeStep(
                name="resolve_local",
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:resolve-failures main main main",
                },
                capture={"verdict": "${{ result.verdict }}"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="diagnose_again",
                            when="${{ result.verdict }} == ci_only_failure",
                        ),
                        StepResultCondition(route="test", when="true"),
                    ]
                ),
            ),
            "diagnose_again": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo diagnose"},
            ),
            "test": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "echo test"},
            ),
        },
    )


def test_verdict_context_precondition_fires_on_fix_step_without_ci_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule fires ERROR when fix step has no CI context but routes to escalation."""
    monkeypatch.setattr(_contracts, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _make_recipe_with_fix_step()
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "verdict-context-precondition"]

    assert len(rule_findings) >= 1, (
        "verdict-context-precondition must fire when a run_skill step routes "
        "ci_only_failure to escalation but its skill_command has no CI context. "
        f"All findings: {[(f.rule, f.severity) for f in findings]}"
    )
    assert rule_findings[0].severity == Severity.ERROR
    assert rule_findings[0].step_name == "fix"
    assert "ci_only_failure" in rule_findings[0].message


def test_verdict_context_precondition_does_not_fire_with_ci_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire when resolve_ci step has CI context in skill_command."""
    monkeypatch.setattr(_contracts, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _make_recipe_with_resolve_ci()
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "verdict-context-precondition"]

    assert rule_findings == [], (
        "verdict-context-precondition must NOT fire when skill_command references "
        f"CI context (ci_conclusion, diagnosis_path). Got: {rule_findings}"
    )


def test_verdict_context_precondition_does_not_fire_with_non_terminal_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire when ci_only_failure routes to a non-escalation target."""
    monkeypatch.setattr(_contracts, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _make_recipe_with_non_terminal_route()
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "verdict-context-precondition"]

    assert rule_findings == [], (
        "verdict-context-precondition must NOT fire when ci_only_failure routes "
        "to a continuation step (re-diagnosis) rather than escalation. "
        f"Got: {rule_findings}"
    )
