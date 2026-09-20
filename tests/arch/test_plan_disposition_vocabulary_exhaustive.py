"""Require an exhaustive dispatch for persisted plan dispositions."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/core/types/_type_audit_cycle_disposition.py"
)


def test_plan_disposition_dispatch_is_exhaustive() -> None:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    row = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PlanDispositionRow"
    )
    post_init = next(
        node
        for node in row.body
        if isinstance(node, ast.FunctionDef) and node.name == "__post_init__"
    )

    assert any(isinstance(node, ast.Match) for node in ast.walk(post_init))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(post_init)
    )
