"""Require an exhaustive counter effect for remediation outcomes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE = Path(__file__).resolve().parents[2] / "src/autoskillit/smoke_utils/_review.py"


def test_remediation_outcome_dispatch_is_exhaustive() -> None:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_audit_remediation_outcome"
    )

    assert any(isinstance(node, ast.Match) for node in ast.walk(guard))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(guard)
    )
