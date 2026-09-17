"""Guard the exhaustiveness of AuditAssessment.disposition."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/core/types/_type_audit_cycle_authority.py"
)


def test_audit_assessment_disposition_uses_match_and_assert_never() -> None:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    audit_assessment = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AuditAssessment"
    )
    disposition = next(
        node
        for node in audit_assessment.body
        if isinstance(node, ast.FunctionDef) and node.name == "disposition"
    )

    assert any(isinstance(node, ast.Match) for node in ast.walk(disposition)), (
        "AuditAssessment.disposition must use match for exhaustive enum dispatch"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(disposition)
    ), "AuditAssessment.disposition must call assert_never in its fallthrough case"
