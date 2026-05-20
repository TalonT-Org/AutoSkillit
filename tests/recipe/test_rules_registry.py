from __future__ import annotations

import pytest

from autoskillit.core.types import Severity
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.validator import RuleFinding, run_semantic_rules
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


def test_rule_registry_hash_changes_on_rule_addition(monkeypatch) -> None:
    """Adding a rule to the registry changes the computed hash."""
    from autoskillit.recipe.registry import (
        _RULE_REGISTRY,
        RuleDef,
        compute_rule_registry_hash,
    )

    h1 = compute_rule_registry_hash()

    dummy = RuleDef(
        name="test-dummy-rule",
        description="dummy",
        severity=Severity.WARNING,
        check=lambda ctx: [],
    )
    monkeypatch.setattr(
        "autoskillit.recipe.registry._RULE_REGISTRY",
        list(_RULE_REGISTRY) + [dummy],
    )

    h2 = compute_rule_registry_hash()
    assert h1 != h2


def test_rule_registry_hash_stable_across_calls() -> None:
    """Hash is deterministic — same input produces same output."""
    from autoskillit.recipe.registry import compute_rule_registry_hash

    assert compute_rule_registry_hash() == compute_rule_registry_hash()


def test_rule_registry_hash_nonempty_after_import() -> None:
    """RULE_REGISTRY_HASH is non-empty after import autoskillit.recipe."""
    from autoskillit.recipe.registry import RULE_REGISTRY_HASH

    assert RULE_REGISTRY_HASH, "RULE_REGISTRY_HASH must be non-empty after finalization"
    assert len(RULE_REGISTRY_HASH) == 64  # sha256 hex digest length


def test_semantic_rule_after_finalization_raises() -> None:
    """Registering a rule after finalization raises RuntimeError."""
    from autoskillit.recipe.registry import _REGISTRY_FINALIZED, semantic_rule

    assert _REGISTRY_FINALIZED, "Registry should be finalized after import"
    with pytest.raises(RuntimeError, match="after registry finalization"):

        @semantic_rule(name="post-finalize-test", description="should fail")
        def _check(ctx):
            return []


def test_old_rule_removed() -> None:
    from autoskillit.recipe.validator import _RULE_REGISTRY

    assert not any(r.name == "retry-without-worktree-path" for r in _RULE_REGISTRY)


def test_bundled_workflows_pass_semantic_rules() -> None:
    wf_dir = builtin_recipes_dir()
    yaml_files = list(wf_dir.glob("*.yaml"))
    assert yaml_files

    _KNOWN_NON_CONFORMING: dict[str, set[str]] = {
        "research.yaml": {"audit-impl-remediation-route"},
    }
    for path in yaml_files:
        wf = load_recipe(path)
        findings = run_semantic_rules(wf)
        excluded = _KNOWN_NON_CONFORMING.get(path.name, set())
        if excluded:
            fired_rules = {f.rule for f in findings if f.severity == Severity.ERROR}
            for rule_name in excluded:
                assert rule_name in fired_rules, (
                    f"Recipe '{path.name}': exclusion for '{rule_name}' is stale — "
                    f"rule no longer fires. Remove from _KNOWN_NON_CONFORMING."
                )
        errors = [f for f in findings if f.severity == Severity.ERROR and f.rule not in excluded]
        assert not errors, (
            f"Bundled workflow {path.name} has error-severity semantic findings: {errors}"
        )
        undeclared_findings = [f for f in findings if f.rule == "undeclared-capture-key"]
        assert undeclared_findings == [], (
            f"Recipe '{wf.name}' has undeclared-capture-key findings: " + repr(undeclared_findings)
        )
