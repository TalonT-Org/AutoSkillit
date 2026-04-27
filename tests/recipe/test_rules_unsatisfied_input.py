from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe, RecipeIngredient
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_unsatisfied_input_replaces_worktree_path_check() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retries": 0,
                "on_context_limit": "retry_step",
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "missing-ingredient" and "worktree_path" in f.message for f in errors)


def test_unsatisfied_input_clean_when_provided() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retries": 0,
                "on_context_limit": "retry_step",
                "on_success": "retry_step",
            },
            "retry_step": {
                "tool": "run_skill",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_not_available() -> None:
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:retry-worktree ${{ context.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [
        f for f in findings if f.rule == "missing-ingredient" and f.severity == Severity.ERROR
    ]
    assert any("worktree_path" in f.message for f in errors)


def test_unsatisfied_input_unknown_skill_ignored() -> None:
    wf = _make_workflow(
        {
            "step": {
                "tool": "run_skill",
                "with": {"skill_command": "/some-unknown-skill"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_from_pipeline_inputs() -> None:
    wf = Recipe(
        name="test",
        description="test",
        ingredients={
            "plan_path": RecipeIngredient(description="Plan file", required=True),
            "worktree_path": RecipeIngredient(description="Worktree", required=True),
        },
        steps={
            "retry_step": _parse_step(
                {
                    "tool": "run_skill",
                    "with": {
                        "skill_command": (
                            "/autoskillit:retry-worktree "
                            "${{ inputs.plan_path }} ${{ inputs.worktree_path }}"
                        ),
                    },
                    "on_success": "done",
                }
            ),
            "done": _parse_step({"action": "stop", "message": "Done."}),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)


def test_unsatisfied_input_inline_positional_args_skipped() -> None:
    wf = _make_workflow(
        {
            "investigate": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:investigate the test failures"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "missing-ingredient" for f in findings)
