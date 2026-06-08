"""Tests for the context-param-not-forwarded semantic rule.

The ``_TOOL_CONTEXT_PARAMS`` registry in ``rules_tools.py`` declares which
context variables must be forwarded to specific tool steps when they are
captured upstream. This rule is the structural guard that prevented the
PR #3901 bug — without it, a recipe could silently omit ``auto_merge_available``
from a ``wait_for_merge_queue`` step and the watcher would default to
``enablePullRequestAutoMerge`` on repos where auto-merge is disabled.
"""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_context_param_not_forwarded_fires_for_wait_for_merge_queue() -> None:
    """A wait_for_merge_queue step that omits auto_merge_available fires ERROR."""
    recipe = _make_workflow(
        {
            "check_repo_merge_state": {
                "tool": "check_repo_merge_state",
                "with": {"branch": "main"},
                "capture": {"auto_merge_available": "${{ result.auto_merge_available }}"},
            },
            "wait": {
                "tool": "wait_for_merge_queue",
                "with": {
                    "pr_number": "${{ context.pr_number }}",
                    "target_branch": "main",
                    "cwd": "/tmp",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(recipe)
    findings = [f for f in findings if f.rule == "context-param-not-forwarded"]
    assert findings, (
        "Expected context-param-not-forwarded finding for wait_for_merge_queue "
        "step that omits auto_merge_available"
    )
    assert all(f.severity == Severity.ERROR for f in findings)
    assert any("auto_merge_available" in f.message and "wait" in f.step_name for f in findings)


def test_context_param_forwarded_suppresses_rule() -> None:
    """Forwarding auto_merge_available via ${{ context.auto_merge_available }} suppresses the rule.

    The rule's check is satisfied when the with: value contains
    ``context.auto_merge_available`` — even a plain string interpolation
    counts. Recipes don't need to re-format the value.
    """
    recipe = _make_workflow(
        {
            "check_repo_merge_state": {
                "tool": "check_repo_merge_state",
                "with": {"branch": "main"},
                "capture": {"auto_merge_available": "${{ result.auto_merge_available }}"},
            },
            "wait": {
                "tool": "wait_for_merge_queue",
                "with": {
                    "pr_number": "${{ context.pr_number }}",
                    "target_branch": "main",
                    "cwd": "/tmp",
                    "auto_merge_available": "${{ context.auto_merge_available }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(recipe)
    findings = [f for f in findings if f.rule == "context-param-not-forwarded"]
    assert not findings, (
        "Forwarding auto_merge_available must suppress the rule; got: "
        f"{[(f.step_name, f.message) for f in findings]}"
    )


def test_context_param_not_forwarded_fires_for_enqueue_pr() -> None:
    """An enqueue_pr step that omits auto_merge_available fires ERROR."""
    recipe = _make_workflow(
        {
            "check_repo_merge_state": {
                "tool": "check_repo_merge_state",
                "with": {"branch": "main"},
                "capture": {"auto_merge_available": "${{ result.auto_merge_available }}"},
            },
            "enqueue": {
                "tool": "enqueue_pr",
                "with": {
                    "pr_number": "${{ context.pr_number }}",
                    "target_branch": "main",
                    "cwd": "/tmp",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(recipe)
    findings = [f for f in findings if f.rule == "context-param-not-forwarded"]
    assert findings, (
        "Expected context-param-not-forwarded for enqueue_pr omitting auto_merge_available"
    )
    assert any(f.step_name == "enqueue" for f in findings)


def test_context_param_irrelevant_tool_does_not_fire() -> None:
    """A step calling a tool that does NOT consume auto_merge_available must not fire the rule."""
    recipe = _make_workflow(
        {
            "check_repo_merge_state": {
                "tool": "check_repo_merge_state",
                "with": {"branch": "main"},
                "capture": {"auto_merge_available": "${{ result.auto_merge_available }}"},
            },
            "wait_ci": {
                "tool": "wait_for_ci",
                "with": {"branch": "main", "auto_trigger": "true"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "done"},
        }
    )
    findings = run_semantic_rules(recipe)
    findings = [f for f in findings if f.rule == "context-param-not-forwarded"]
    assert not findings, (
        "wait_for_ci does not consume auto_merge_available; the rule must not fire. "
        f"Got: {[(f.step_name, f.message) for f in findings]}"
    )
