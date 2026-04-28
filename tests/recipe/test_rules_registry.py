from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import RuleFinding, run_semantic_rules
from tests.recipe.conftest import NO_AUTOSKILLIT_IMPORT as _NO_AUTOSKILLIT_IMPORT
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_registry_collects_rules() -> None:
    wf = _make_workflow(
        {
            "do_thing": {"tool": "run_cmd", "on_success": "done"},
            "orphan": {"tool": "run_cmd", "on_success": "done"},
            "done": {"action": "stop", "message": "Done."},
        }
    )
    rule_ids = [f.rule for f in run_semantic_rules(wf)]
    assert "unreachable-step" in rule_ids


def test_rule_finding_to_dict() -> None:
    finding = RuleFinding(
        rule="test-rule",
        severity=Severity.WARNING,
        step_name="some_step",
        message="Something is wrong.",
    )
    d = finding.to_dict()
    assert d == {
        "rule": "test-rule",
        "severity": "warning",
        "step": "some_step",
        "message": "Something is wrong.",
    }


def test_old_rule_removed() -> None:
    from autoskillit.recipe.validator import _RULE_REGISTRY

    assert not any(r.name == "retry-without-worktree-path" for r in _RULE_REGISTRY)


def test_bundled_workflows_pass_semantic_rules() -> None:
    wf_dir = builtin_recipes_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files

    for path in yaml_files:
        wf = load_recipe(path)
        findings = run_semantic_rules(wf)
        errors = [
            f
            for f in findings
            if f.severity == Severity.ERROR and f.rule != _NO_AUTOSKILLIT_IMPORT
        ]
        assert not errors, (
            f"Bundled workflow {path.name} has error-severity semantic findings: {errors}"
        )
        undeclared_findings = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert undeclared_findings == [], (
            f"Recipe '{wf.name}' has undeclared-capture-key findings: " + repr(undeclared_findings)
        )
