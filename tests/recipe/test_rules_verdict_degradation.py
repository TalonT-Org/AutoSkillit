"""Tests for the verdict-ungated-degradation semantic rule.

Verifies that skills with graceful-degradation paths cannot emit a verdict
that is semantically indistinguishable from the nominal success verdict.
"""

from __future__ import annotations

from pathlib import Path

import autoskillit.recipe.rules.rules_verdict_degradation as _rvd
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

_FAKE_SKILL_NAME = "fake-review-pr"

# SKILL.md where the degradation path emits the same verdict as the nominal path.
_SKILL_MD_SHARED_VERDICT = """\
## Critical Constraints

**NEVER:**
- Post review comments when gh is unavailable — output verdict=approved and exit 0

**ALWAYS:**
- Output verdict= on the final line

## Workflow

### Step 1: Find the PR

If gh is unavailable or not authenticated:
- Log "No PR found or gh unavailable — skipping review"
- Output verdict=approved
- Exit 0 (graceful degradation)

### Step 2: Full Review

Review the PR thoroughly.

At the end:
- verdict = "approved"
"""

# SKILL.md where the degradation path emits a distinct verdict (needs_human).
_SKILL_MD_DISTINCT_VERDICT = """\
## Critical Constraints

**NEVER:**
- Post review comments when gh is unavailable — output verdict=needs_human and exit 0

**ALWAYS:**
- Output verdict= on the final line

## Workflow

### Step 1: Find the PR

If gh is unavailable or not authenticated:
- Log "No PR found or gh unavailable — skipping review"
- Output verdict=needs_human
- Exit 0 (graceful degradation)

### Step 2: Full Review

Review the PR thoroughly.

At the end:
- verdict = "approved"
"""

_MANIFEST: dict = {
    "version": "0.1.0",
    "skills": {
        _FAKE_SKILL_NAME: {
            "inputs": [],
            "outputs": [
                {
                    "name": "verdict",
                    "type": "string",
                    "allowed_values": ["approved", "needs_human", "changes_requested"],
                }
            ],
        }
    },
}


def _make_recipe() -> Recipe:
    return Recipe(
        name="test",
        description="test",
        kitchen_rules=["test"],
        steps={
            "review_step": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": f"/autoskillit:{_FAKE_SKILL_NAME} main main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="fix",
                            when="${{ result.verdict }} == changes_requested",
                        ),
                        StepResultCondition(route="done", when="true"),
                    ]
                ),
                on_failure="done",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:resolve-review main main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
    )


def test_verdict_ungated_degradation_fires_when_shared_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule fires ERROR when degradation path emits the same verdict as nominal path."""
    skill_dir = tmp_path / "skills_extended" / _FAKE_SKILL_NAME
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD_SHARED_VERDICT)

    monkeypatch.setattr(_rvd, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(_contracts, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _make_recipe()
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "verdict-ungated-degradation"]

    assert len(rule_findings) >= 1, (
        "verdict-ungated-degradation must fire when degradation path emits 'approved' "
        f"(same as nominal path). All findings: {[f.rule for f in findings]}"
    )
    assert rule_findings[0].severity == Severity.ERROR
    assert _FAKE_SKILL_NAME in rule_findings[0].message


def test_verdict_ungated_degradation_does_not_fire_with_distinct_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rule does NOT fire when degradation path emits a distinct verdict (needs_human)."""
    skill_dir = tmp_path / "skills_extended" / _FAKE_SKILL_NAME
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_MD_DISTINCT_VERDICT)

    monkeypatch.setattr(_rvd, "pkg_root", lambda: tmp_path)
    monkeypatch.setattr(_contracts, "load_bundled_manifest", lambda: _MANIFEST)

    recipe = _make_recipe()
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "verdict-ungated-degradation"]

    assert rule_findings == [], (
        "verdict-ungated-degradation must NOT fire when degradation path emits "
        f"'needs_human' (distinct from nominal 'approved'). Got: {rule_findings}"
    )
