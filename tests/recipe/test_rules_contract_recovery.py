"""Tests for the contract-recovery-requires-salvage-route semantic rule.

Verifies that a ``run_skill`` step invoking a skill whose contract can trigger
``retry_reason=contract_recovery`` at runtime (non-empty ``expected_output_patterns``
and not ``read_only``) must declare an ``on_context_limit`` salvage route distinct
from ``on_failure`` — otherwise a completed-but-unparsed artifact is discarded
instead of salvaged (issue #4305).
"""

from __future__ import annotations

import pytest

import autoskillit.recipe.rules.rules_contract_recovery as _rules_contract_recovery
from autoskillit.core.types import Severity
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE_NAME = "contract-recovery-requires-salvage-route"

_RECOVERY_CAPABLE_SKILL = "make-a-plan"
_READ_ONLY_SKILL = "read-only-reviewer"
_NO_PATTERNS_SKILL = "chatty-skill"

_MANIFEST: dict = {
    "version": "0.1.0",
    "skills": {
        _RECOVERY_CAPABLE_SKILL: {
            "inputs": [],
            "outputs": [],
            "expected_output_patterns": [r"PLAN_READY::\S+"],
        },
        _READ_ONLY_SKILL: {
            "inputs": [],
            "outputs": [],
            "expected_output_patterns": [r"REVIEW_DONE::\S+"],
            "read_only": True,
        },
        _NO_PATTERNS_SKILL: {
            "inputs": [],
            "outputs": [],
        },
    },
}


def _plan_step(
    *, on_context_limit: str | None = None, on_failure: str | None = "escalate_stop"
) -> RecipeStep:
    return RecipeStep(
        name="make_plan",
        tool="run_skill",
        with_args={"skill_command": f"/autoskillit:{_RECOVERY_CAPABLE_SKILL}"},
        on_success="next",
        on_failure=on_failure,
        on_context_limit=on_context_limit,
    )


def _recipe_with(step: RecipeStep, *, extra_steps: dict[str, RecipeStep] | None = None) -> Recipe:
    steps = {"make_plan": step}
    steps.update(extra_steps or {})
    steps.setdefault(
        "next", RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}, on_success="stop_ok")
    )
    steps.setdefault("escalate_stop", RecipeStep(action="stop", message="escalation"))
    return Recipe(name="test", description="test", kitchen_rules=["test"], steps=steps)


def _rule_findings(recipe: Recipe) -> list:
    findings = run_semantic_rules(recipe)
    return [f for f in findings if f.rule == _RULE_NAME]


def test_fires_when_on_context_limit_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule fires WARNING when a contract-recovery-capable step has no on_context_limit.

    Severity is WARNING (not ERROR) pending a follow-up remediation pass across bundled
    recipes beyond the nine audited sites — see the rule module's docstring.
    """
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _recipe_with(_plan_step(on_context_limit=None))
    findings = _rule_findings(recipe)

    assert len(findings) == 1, (
        f"Rule must fire when on_context_limit is missing for a contract-recovery-capable "
        f"step. All findings: {[(f.rule, f.severity) for f in run_semantic_rules(recipe)]}"
    )
    assert findings[0].severity == Severity.WARNING
    assert findings[0].step_name == "make_plan"
    assert "make-a-plan" in findings[0].message


def test_fires_when_on_context_limit_is_decorative_alias_of_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule fires WARNING when on_context_limit is set but equals on_failure (no real salvage)."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _recipe_with(_plan_step(on_context_limit="escalate_stop", on_failure="escalate_stop"))
    findings = _rule_findings(recipe)

    assert len(findings) == 1, "Rule must fire when on_context_limit == on_failure (decorative)"
    assert findings[0].step_name == "make_plan"


def test_does_not_fire_when_salvage_route_exists_and_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire when on_context_limit is set and differs from on_failure."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _recipe_with(
        _plan_step(on_context_limit="salvage_plan", on_failure="escalate_stop"),
        extra_steps={
            "salvage_plan": RecipeStep(
                tool="run_python",
                with_args={"callable": "verify_plan_artifacts"},
                on_success="next",
                on_failure="escalate_stop",
            )
        },
    )
    findings = _rule_findings(recipe)

    assert findings == [], f"Rule must NOT fire when a distinct salvage route exists: {findings}"


def test_does_not_fire_when_skill_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule does NOT fire for read_only skills — they can't trigger contract_recovery."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    step = RecipeStep(
        name="review",
        tool="run_skill",
        with_args={"skill_command": f"/autoskillit:{_READ_ONLY_SKILL}"},
        on_success="next",
        on_failure="escalate_stop",
        on_context_limit=None,
    )
    recipe = Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "review": step,
            "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
            "escalate_stop": RecipeStep(action="stop", message="escalation"),
        },
    )
    findings = _rule_findings(recipe)

    assert findings == [], f"Rule must NOT fire for a read_only skill: {findings}"


def test_does_not_fire_when_skill_has_no_expected_output_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire when the skill's contract declares no expected_output_patterns."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    step = RecipeStep(
        name="chat",
        tool="run_skill",
        with_args={"skill_command": f"/autoskillit:{_NO_PATTERNS_SKILL}"},
        on_success="next",
        on_failure="escalate_stop",
        on_context_limit=None,
    )
    recipe = Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "chat": step,
            "next": RecipeStep(tool="run_cmd", with_args={"cmd": "echo next"}),
            "escalate_stop": RecipeStep(action="stop", message="escalation"),
        },
    )
    findings = _rule_findings(recipe)

    assert findings == [], f"Rule must NOT fire when expected_output_patterns is empty: {findings}"


def test_does_not_fire_when_step_is_itself_an_on_context_limit_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule does NOT fire on a step that is itself another step's on_context_limit target."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    salvage_step = RecipeStep(
        name="salvage_plan",
        tool="run_skill",
        with_args={"skill_command": f"/autoskillit:{_RECOVERY_CAPABLE_SKILL}"},
        on_success="next",
        on_failure="escalate_stop",
        on_context_limit=None,
    )
    other_step = _plan_step(on_context_limit="salvage_plan", on_failure="escalate_stop")
    recipe = _recipe_with(other_step, extra_steps={"salvage_plan": salvage_step})

    findings = _rule_findings(recipe)

    assert findings == [], (
        f"Rule must NOT fire on a step that is itself an on_context_limit target: {findings}"
    )


def test_does_not_fire_when_step_action_is_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule does NOT fire on stop-action steps (terminal, no routing needed)."""
    monkeypatch.setattr(_rules_contract_recovery, "load_bundled_manifest", lambda: _MANIFEST)

    step = RecipeStep(
        name="make_plan",
        tool="run_skill",
        action="stop",
        message="done",
        with_args={"skill_command": f"/autoskillit:{_RECOVERY_CAPABLE_SKILL}"},
    )
    recipe = Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={"make_plan": step},
    )
    findings = _rule_findings(recipe)

    assert findings == [], f"Rule must NOT fire on a stop-action step: {findings}"
