"""Keep every public audit substitution rule enforced at both parent floors."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core.audit import audit_semantic_codec

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FLOORS = {
    "_validate_semantic_assessment_floor": (
        _REPOSITORY_ROOT / "src/autoskillit/server/_audit_authority_materializer.py"
    ),
    "_verify_rows": _REPOSITORY_ROOT / "src/autoskillit/core/audit/closure_verifier.py",
}


def _direct_calls(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }


def test_public_substitution_rules_are_enforced_by_both_parent_floors() -> None:
    rules = {name for name in audit_semantic_codec.__all__ if name.startswith("evaluate_")}
    assert rules, "audit_semantic_codec must export at least one evaluate_* substitution rule"

    calls_by_floor = {floor: _direct_calls(path, floor) for floor, path in _FLOORS.items()}
    missing = {
        rule: sorted(floor for floor, calls in calls_by_floor.items() if rule not in calls)
        for rule in sorted(rules)
    }
    missing = {rule: floors for rule, floors in missing.items() if floors}

    assert not missing, (
        f"orphan audit substitution rules: {missing}; enforce each rule in both parent floors "
        "(_validate_semantic_assessment_floor and _verify_rows) or delete it; child-side-only "
        "substitution rules are forbidden (#5177)"
    )
