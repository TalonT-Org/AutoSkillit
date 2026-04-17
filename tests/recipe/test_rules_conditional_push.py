"""Tests for the conditional-skill-ungated-push semantic rule.

A step invoking a write_behavior='conditional' skill must not reach a
push_to_remote tool step without passing through an on_result: edge that
dispatches on a declared verdict-like output.
"""

from __future__ import annotations

import pytest

import autoskillit.recipe.rules_fixing as _rf
from autoskillit.core.types import Severity
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RULE = "conditional-skill-ungated-push"

# Minimal manifest with a fake conditional-write skill and a declared verdict output.
_FAKE_MANIFEST: dict = {
    "version": "0.1.0",
    "skills": {
        "fake_fixer": {
            "inputs": [{"name": "worktree_path", "type": "directory_path", "required": True}],
            "outputs": [
                {
                    "name": "verdict",
                    "type": "string",
                    "allowed_values": ["real_fix", "no_fix"],
                }
            ],
            "write_behavior": "conditional",
            "write_expected_when": ["verdict\\s*=\\s*real_fix"],
        },
        # Include a non-conditional skill so the rule skips it cleanly.
        "smoke-task": {
            "inputs": [],
            "outputs": [],
            "write_behavior": "always",
            "write_expected_when": [],
        },
    },
}


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    return Recipe(name="test", description="test", steps=steps, kitchen_rules=["test"])


def _push_step(name: str = "push") -> RecipeStep:
    return RecipeStep(
        tool="push_to_remote",
        with_args={
            "clone_path": "${{ context.work_dir }}",
            "remote_url": "${{ context.remote_url }}",
            "branch": "${{ context.branch }}",
        },
        on_success="done",
        on_failure="escalate",
    )


def _terminal_steps() -> dict[str, RecipeStep]:
    return {
        "done": RecipeStep(action="stop", with_args={"message": "Done."}),
        "escalate": RecipeStep(action="stop", with_args={"message": "Escalate."}),
    }


def _fix_step_unconditional(on_success: str = "push") -> RecipeStep:
    """fix step: unconditional on_success → violation."""
    return RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:fake_fixer /tmp/wt"},
        on_success=on_success,
        on_failure="escalate",
    )


def _fix_step_gated() -> RecipeStep:
    """fix step: on_result with verdict dispatch → compliant."""
    return RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:fake_fixer /tmp/wt"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.verdict }} == 'real_fix'",
                    route="push",
                ),
                StepResultCondition(
                    when="${{ result.verdict }} == 'no_fix'",
                    route="escalate",
                ),
            ]
        ),
        on_failure="escalate",
    )


# ---------------------------------------------------------------------------
# Case A — unconditional on_success → push (violation)
# ---------------------------------------------------------------------------


def test_case_a_unconditional_on_success_to_push_fires(monkeypatch) -> None:
    """Case A: unconditional on_success to push_to_remote must fire ERROR."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    steps = {"fix": _fix_step_unconditional(), "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1, (
        "conditional-skill-ungated-push must fire when a conditional-write skill "
        "routes unconditionally to push_to_remote"
    )
    assert any(f.severity == Severity.ERROR for f in findings), (
        "conditional-skill-ungated-push findings must have ERROR severity"
    )
    # Finding must reference the offending step
    assert any("fix" in f.step_name for f in findings), "finding must reference the 'fix' step"


# ---------------------------------------------------------------------------
# Case B — on_result with verdict dispatch → compliant (no finding)
# ---------------------------------------------------------------------------


def test_case_b_verdict_gated_on_result_passes(monkeypatch) -> None:
    """Case B: on_result with explicit verdict conditions must NOT fire."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    steps = {"fix": _fix_step_gated(), "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) == 0, (
        "conditional-skill-ungated-push must NOT fire when verdict-gated on_result "
        f"is present; got findings: {findings}"
    )


# ---------------------------------------------------------------------------
# Case C — on_result present but no when: (catch-all only) → violation
# ---------------------------------------------------------------------------


def test_case_c_catchall_only_on_result_fires(monkeypatch) -> None:
    """Case C: on_result with only a catch-all condition must fire."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    # on_result with only a when-less catch-all → not a real gate
    catchall_step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:fake_fixer /tmp/wt"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(when=None, route="push"),  # catch-all, no when
            ]
        ),
        on_failure="escalate",
    )
    steps = {"fix": catchall_step, "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1, (
        "conditional-skill-ungated-push must fire when on_result has only a "
        "catch-all condition with no verdict reference"
    )


# ---------------------------------------------------------------------------
# Case D — indirect path (fix → verify → push) → violation
# ---------------------------------------------------------------------------


def test_case_d_indirect_path_fires(monkeypatch) -> None:
    """Case D: rule must fire even when push is 2 hops away via an intermediate step."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    # fix → verify → push (2 hops; no on_result gate on fix)
    fix_step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:fake_fixer /tmp/wt"},
        on_success="verify",
        on_failure="escalate",
    )
    verify_step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:smoke-task"},
        on_success="push",
        on_failure="escalate",
    )
    steps = {
        "fix": fix_step,
        "verify": verify_step,
        "push": _push_step(),
        **_terminal_steps(),
    }
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1, (
        "conditional-skill-ungated-push must walk multi-hop paths and fire "
        "when fix → verify → push has no verdict gate"
    )


# ---------------------------------------------------------------------------
# Case E — on_result gates on undeclared field → violation
# ---------------------------------------------------------------------------


def test_case_e_on_result_gates_on_undeclared_field_fires(monkeypatch) -> None:
    """Case E: on_result referencing a field not in the contract's outputs must fire."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    # Gates on ${{ result.fixes_applied }} which is NOT declared in fake_fixer's outputs
    bad_gate_step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:fake_fixer /tmp/wt"},
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(
                    when="${{ result.fixes_applied }} > 0",
                    route="push",
                ),
                StepResultCondition(when=None, route="escalate"),
            ]
        ),
        on_failure="escalate",
    )
    steps = {"fix": bad_gate_step, "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1, (
        "conditional-skill-ungated-push must fire when on_result gates on a field "
        "that is not declared in the skill contract's outputs"
    )


# ---------------------------------------------------------------------------
# Severity and step_name contract
# ---------------------------------------------------------------------------


def test_finding_severity_is_error(monkeypatch) -> None:
    """All findings from this rule must have ERROR severity."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    steps = {"fix": _fix_step_unconditional(), "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1
    for f in findings:
        assert f.severity == Severity.ERROR, f"Expected ERROR severity, got {f.severity}"


def test_finding_message_references_fix_and_push(monkeypatch) -> None:
    """Finding message must reference both the fix step and the push step."""
    monkeypatch.setattr(_rf, "load_bundled_manifest", lambda: _FAKE_MANIFEST)
    steps = {"fix": _fix_step_unconditional(), "push": _push_step(), **_terminal_steps()}
    recipe = _make_recipe(steps)
    findings = [f for f in run_semantic_rules(recipe) if f.rule == _RULE]
    assert len(findings) >= 1
    messages = " ".join(f.message for f in findings)
    assert "fix" in messages, "Finding message must reference the 'fix' step"
    assert "push" in messages, "Finding message must reference the 'push' step"
