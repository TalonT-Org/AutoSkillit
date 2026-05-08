"""Tests for callable-requires-scoped-discovery semantic rule."""

from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_callable_requires_scoped_discovery_flags_batch_create_issues():
    """Rule must ERROR when batch_create_issues step lacks audit_run_dir.

    Reproduces the bug: batch_create_issues uses an unscoped glob on the flat
    validate-audit/ directory. Without audit_run_dir, it cannot distinguish
    current-run files from prior-run files.
    """
    recipe = _make_workflow(
        {
            "init": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.recipe._cmd_rpc.batch_create_issues",
                    "workspace": "${{ inputs.workspace }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    scoped_rule_findings = [f for f in findings if f.rule == "callable-requires-scoped-discovery"]
    assert len(scoped_rule_findings) == 1, (
        "callable-requires-scoped-discovery must fire exactly once when batch_create_issues "
        "is called without audit_run_dir"
    )
    assert all(f.severity == Severity.ERROR for f in scoped_rule_findings)


def test_callable_requires_scoped_discovery_passes_with_audit_run_dir():
    """Rule must NOT fire when batch_create_issues receives audit_run_dir.

    audit_run_dir scopes the glob to a per-run directory, preventing cross-run
    file accumulation.
    """
    recipe = _make_workflow(
        {
            "init": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.recipe._cmd_rpc.batch_create_issues",
                    "workspace": "${{ inputs.workspace }}",
                    "audit_run_dir": "${{ context.audit_run_dir }}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    scoped_rule_findings = [f for f in findings if f.rule == "callable-requires-scoped-discovery"]
    assert len(scoped_rule_findings) == 0, (
        "callable-requires-scoped-discovery must not fire when audit_run_dir is provided"
    )


def test_callable_requires_scoped_discovery_not_fired_for_other_callables():
    """Rule must only fire for known file-discovering callables.

    Unknown callables that are not in SCOPED_CALLABLES should not trigger the rule.
    """
    recipe = _make_workflow(
        {
            "init": {
                "tool": "run_python",
                "with": {
                    "callable": "autoskillit.planner.create_run_dir",
                    "temp_dir": "{{AUTOSKILLIT_TEMP}}",
                },
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        }
    )
    findings = run_semantic_rules(recipe)
    scoped_rule_findings = [f for f in findings if f.rule == "callable-requires-scoped-discovery"]
    assert len(scoped_rule_findings) == 0, (
        "callable-requires-scoped-discovery must not fire for create_run_dir"
    )
