"""Tests for workspace isolation semantic rules.

Covers source-isolation-violation and git-mutation-on-source rules.
"""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import _RULE_REGISTRY, run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Test 1a: Rule registration
# ---------------------------------------------------------------------------


def test_source_isolation_rule_registered() -> None:
    """source-isolation-violation must be in the rule registry."""
    names = {spec.name for spec in _RULE_REGISTRY}
    assert "source-isolation-violation" in names


# ---------------------------------------------------------------------------
# Test 1b: create_unique_branch with inputs.* cwd fires ERROR
# ---------------------------------------------------------------------------


def test_create_unique_branch_with_inputs_cwd_fires_error() -> None:
    wf = _make_workflow(
        {
            "create_branch": {
                "tool": "create_unique_branch",
                "with": {
                    "base_branch_name": "smoke/canary",
                    "cwd": "${{ inputs.workspace }}",
                },
                "capture": {"branch_name": "${{ result.branch_name }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.rule == "source-isolation-violation"]
    assert len(errors) == 1
    assert errors[0].severity == Severity.ERROR
    assert errors[0].step_name == "create_branch"


# ---------------------------------------------------------------------------
# Test 1c: create_unique_branch with context.* cwd passes
# ---------------------------------------------------------------------------


def test_create_unique_branch_with_context_cwd_passes() -> None:
    wf = _make_workflow(
        {
            "create_branch": {
                "tool": "create_unique_branch",
                "with": {
                    "base_branch_name": "smoke/canary",
                    "cwd": "${{ context.work_dir }}",
                },
                "capture": {"branch_name": "${{ result.branch_name }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    isolation = [f for f in findings if f.rule == "source-isolation-violation"]
    assert isolation == []


# ---------------------------------------------------------------------------
# Test 1d: run_cmd git checkout on inputs.* fires WARNING
# ---------------------------------------------------------------------------


def test_run_cmd_git_checkout_on_inputs_fires_warning() -> None:
    wf = _make_workflow(
        {
            "checkout": {
                "tool": "run_cmd",
                "with": {
                    "cmd": 'git checkout -b "my-branch"',
                    "cwd": "${{ inputs.source_dir }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    warnings = [f for f in findings if f.rule == "git-mutation-on-source"]
    assert len(warnings) == 1
    assert warnings[0].severity == Severity.WARNING
    assert warnings[0].step_name == "checkout"


# ---------------------------------------------------------------------------
# Test 1e: run_cmd git checkout after clone passes
# ---------------------------------------------------------------------------


def test_run_cmd_git_checkout_after_clone_passes() -> None:
    wf = _make_workflow(
        {
            "clone": {
                "tool": "clone_repo",
                "with": {
                    "source_dir": "${{ inputs.source_dir }}",
                    "run_name": "test",
                    "branch": "main",
                },
                "capture": {"work_dir": "${{ result.clone_path }}"},
                "on_success": "checkout",
            },
            "checkout": {
                "tool": "run_cmd",
                "with": {
                    "cmd": 'git checkout -b "my-branch"',
                    "cwd": "${{ context.work_dir }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    mutation = [f for f in findings if f.rule == "git-mutation-on-source"]
    assert mutation == []


# ---------------------------------------------------------------------------
# Test 1f: run_skill with inputs.* cwd fires WARNING (no clone)
# ---------------------------------------------------------------------------


def test_run_skill_with_inputs_cwd_fires_warning() -> None:
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill",
                "with": {
                    "skill_command": "/autoskillit:implement-worktree plan.md",
                    "cwd": "${{ inputs.workspace }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    warnings = [f for f in findings if f.rule == "source-isolation-violation"]
    assert len(warnings) == 1
    assert warnings[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# Test 1g: All bundled recipes pass isolation rules
# ---------------------------------------------------------------------------


def test_bundled_recipes_pass_isolation_rules() -> None:
    """No bundled recipe may trigger an ERROR-level isolation finding.

    WARNINGs are advisory (e.g. research.yaml legitimately operates on the
    source directory without cloning).
    """
    bd = builtin_recipes_dir()
    for yaml_path in sorted(bd.glob("*.yaml")):
        recipe = load_recipe(yaml_path)
        findings = run_semantic_rules(recipe)
        errors = [
            f
            for f in findings
            if f.rule in ("source-isolation-violation", "git-mutation-on-source")
            and f.severity == Severity.ERROR
        ]
        assert errors == [], f"{yaml_path.name} triggered isolation ERROR: {errors}"
