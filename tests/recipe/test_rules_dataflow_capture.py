"""Tests for data-flow semantic rules — capture output coverage, dead output, implicit handoff."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from autoskillit.core.types import Severity
from autoskillit.recipe.io import _parse_recipe
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _build_merge_worktree_recipe(capture: dict) -> Recipe:
    """Helper: build a minimal Recipe with a merge_worktree step and the given capture dict."""
    return Recipe(
        name="test-merge",
        description="Test merge recipe",
        summary="merge > done",
        steps={
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "${{ context.worktree_path }}", "base_branch": "main"},
                capture=capture,
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
    )


# ---------------------------------------------------------------------------
# TestCaptureOutputCoverageRule
# ---------------------------------------------------------------------------


class TestCaptureOutputCoverageRule:
    def test_capture_declared_output_key_no_warning(self) -> None:
        """A capture that references a key declared in the skill's outputs contract
        must not produce an undeclared-capture-key warning."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-valid
            description: test
            steps:
              implement:
                tool: run_skill
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  worktree_path: "${{ result.worktree_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert undeclared == []

    def test_capture_undeclared_key_emits_error(self) -> None:
        """A capture that references a key NOT listed in the skill's outputs contract
        must produce a Severity.ERROR finding with rule 'undeclared-capture-key'."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-invalid-key
            description: test
            steps:
              implement:
                tool: run_skill
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  nonexistent_output: "${{ result.nonexistent_output }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.ERROR
        assert "nonexistent_output" in undeclared[0].message
        assert "implement-worktree-no-merge" in undeclared[0].message

    def test_capture_from_skill_with_no_contract_emits_error(self) -> None:
        """A capture step whose skill has no entry in skill_contracts.yaml at all
        must produce a Severity.ERROR finding with rule 'undeclared-capture-key'."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-unknown-skill
            description: test
            steps:
              run_custom:
                tool: run_skill
                with:
                  skill_command: /autoskillit:not-a-real-skill some_arg
                capture:
                  result_key: "${{ result.some_key }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.ERROR
        assert "not-a-real-skill" in undeclared[0].message
        assert "no outputs contract entry" in undeclared[0].message

    def test_capture_key_from_empty_outputs_skill_emits_error(self) -> None:
        """audit-friction has outputs: [] — any capture key from it is undeclared."""
        recipe_yaml = textwrap.dedent("""\
            name: capture-empty-outputs
            description: test
            steps:
              audit:
                tool: run_skill
                with:
                  skill_command: /autoskillit:audit-friction
                capture:
                  report_path: "${{ result.report_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.ERROR
        assert "report_path" in undeclared[0].message
        assert "audit-friction" in undeclared[0].message

    def test_undeclared_capture_key_blocks_validity(self) -> None:
        """undeclared-capture-key at ERROR severity must cause compute_recipe_validity=False."""
        from autoskillit.recipe.registry import compute_recipe_validity

        recipe_yaml = textwrap.dedent("""\
            name: capture-invalid-blocks-validity
            description: test
            steps:
              implement:
                tool: run_skill
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  ghost_key: "${{ result.ghost_key }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        assert not compute_recipe_validity(
            errors=[],
            semantic_findings=findings,
            contract_findings=[],
        )


# ---------------------------------------------------------------------------
# TestDeadOutputRule
# ---------------------------------------------------------------------------


class TestDeadOutputRule:
    def test_do1_dead_output_rule_in_registry(self) -> None:
        """T_DO1: dead-output is in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        rule_names = [r.name for r in _RULE_REGISTRY]
        assert "dead-output" in rule_names

    def test_do2_fires_error_for_unconsumed_capture(self) -> None:
        """T_DO2: dead-output fires ERROR when a captured key is never consumed downstream."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output"]
        assert len(dead) >= 1
        assert any(f.severity == Severity.ERROR and f.step_name == "plan" for f in dead)

    def test_do3_does_not_fire_for_on_result_self_consumption(self) -> None:
        """T_DO3: dead-output does NOT fire when on_result.field equals the captured key."""
        steps = {
            "audit_impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:audit-impl plan.md myref main",
                },
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": {
                    "field": "verdict",
                    "routes": {"GO": "done", "NO GO": "done"},
                },
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output" and f.step_name == "audit_impl"]
        assert dead == []

    def test_do4_cleanup_succeeded_from_merge_worktree_not_dead_output(self) -> None:
        """T_DO4: dead-output does NOT fire for cleanup_succeeded captured from merge_worktree.

        cleanup_succeeded is an observability capture required by merge-cleanup-uncaptured.
        It has no downstream consumer by design — the exemption prevents the two rules
        from conflicting.
        """
        recipe = _build_merge_worktree_recipe(
            capture={"cleanup_succeeded": "${{ result.cleanup_succeeded }}"}
        )
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output" and f.step_name == "merge"]
        assert dead == []

    def test_do5_predicate_condition_consumes_captured_var(self) -> None:
        """T_DO5: dead-output does NOT fire when a predicate on_result condition
        references the captured variable via context.X in its when clause."""
        steps = {
            "review_pr": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:review-pr feature main"},
                "capture": {"verdict": "${{ result.verdict }}"},
                "on_result": [
                    {
                        "when": "${{ result.verdict }} == changes_requested",
                        "route": "resolve_review",
                    },
                    {"when": "true", "route": "done"},
                ],
                "on_failure": "resolve_review",
            },
            "resolve_review": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:resolve-failures worktree plan main",
                },
                "on_success": "done",
                "on_failure": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        dead = [f for f in findings if f.rule == "dead-output" and f.step_name == "review_pr"]
        assert dead == []


# ---------------------------------------------------------------------------
# TestImplicitHandoffRule
# ---------------------------------------------------------------------------


class TestImplicitHandoffRule:
    def test_ih1_implicit_handoff_rule_in_registry(self) -> None:
        """T_IH1: implicit-handoff is in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        rule_names = [r.name for r in _RULE_REGISTRY]
        assert "implicit-handoff" in rule_names

    def test_ih2_fires_error_for_skill_with_outputs_and_no_capture(self) -> None:
        """T_IH2: implicit-handoff fires ERROR when skill has outputs but step has no capture."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff"]
        assert len(ih) >= 1
        assert any(f.severity == Severity.ERROR and f.step_name == "plan" for f in ih)

    def test_ih3_does_not_fire_when_capture_block_present(self) -> None:
        """T_IH3: implicit-handoff does NOT fire when the step has a capture: block."""
        steps = {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan do the task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "plan"]
        assert ih == []

    def test_ih4_does_not_fire_for_skill_with_empty_outputs(self) -> None:
        """T_IH4: implicit-handoff does NOT fire for a skill with outputs: [].

        dry-walkthrough has outputs: [] — no fields to capture, so no finding.
        """
        steps = {
            "walkthrough": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:dry-walkthrough plan"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "walkthrough"]
        assert ih == []

    def test_ih5_does_not_fire_for_unknown_skill(self) -> None:
        """T_IH5: implicit-handoff does NOT fire for a skill with no contract entry."""
        steps = {
            "step": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:not-a-real-skill something"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "step"]
        assert ih == []

    def test_implicit_handoff_fires_for_investigate_without_capture(self) -> None:
        """T_IH_1g: implicit-handoff fires for investigate step without capture block.

        After investigate.outputs gains investigation_path in skill_contracts.yaml,
        a recipe step invoking /autoskillit:investigate without a capture block
        triggers the implicit-handoff rule. Uses the real bundled manifest.
        """
        steps = {
            "investigate": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:investigate the bug"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        ih = [f for f in findings if f.rule == "implicit-handoff" and f.step_name == "investigate"]
        assert len(ih) >= 1, (
            "implicit-handoff must fire for an investigate step with no capture block "
            "once investigate declares investigation_path in skill_contracts.yaml"
        )
        assert any(f.severity == Severity.ERROR for f in ih)
