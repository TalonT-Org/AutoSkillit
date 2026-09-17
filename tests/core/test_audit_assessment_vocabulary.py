"""Closed-vocabulary tests for admitted audit assessments."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import (
    AuditAssessment,
    AuditAssessmentRow,
    AuditDisposition,
    ClosureReport,
    ClosureRow,
    compute_canonical_hash,
)
from autoskillit.core.closure_hashing import compute_report_hash, compute_row_hash
from autoskillit.core.types._type_closure_report import (
    CLOSURE_ROW_ALLOWED_ASSESSMENTS,
    CLOSURE_ROW_BLOCKING_ASSESSMENTS,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_ROW_DIGEST_DOMAIN = "autoskillit:audit-cycle:assessment-row:v1:sha256"
_AUDIT_ASSESSMENT_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src/autoskillit/core/types/_type_audit_cycle_authority.py"
)


def _historical_named_deviation_row() -> AuditAssessmentRow:
    payload = {
        "assessment": "NAMED_DEVIATION",
        "evidence_summary": "The historical authority row is retained for later rounds.",
        "requirement_id": "REQ-HISTORY",
        "requirement_text": "Retain persisted audit history.",
    }
    return AuditAssessmentRow.from_dict(
        {
            **payload,
            "row_digest": compute_canonical_hash(payload, domain=_ROW_DIGEST_DOMAIN),
        }
    )


def _closure_report(assessment: str, *, verdict: str) -> ClosureReport:
    row = ClosureRow(
        requirement_id="REQ-001",
        requirement_text="A real child process must be observed.",
        source_file="tests/execution/test_example.py",
        source_line=1,
        source_section="Regression",
        assessment=assessment,
        evidence_summary="Evidence text.",
        row_hash=compute_row_hash(
            "REQ-001",
            "A real child process must be observed.",
            assessment,
            "Evidence text.",
            "tests/execution/test_example.py",
            1,
            "Regression",
        ),
    )
    request_hash = "sha256:" + "a" * 64
    return ClosureReport(
        schema_version=1,
        request_hash=request_hash,
        authority_hash="sha256:" + "b" * 64,
        plan_hashes=(),
        base_sha="base",
        diff_sha="diff",
        target_sha="target",
        requirement_ids=(row.requirement_id,),
        rows=(row,),
        verdict=verdict,
        report_hash=compute_report_hash(request_hash, [row.row_hash], verdict),
        remediation_path="/tmp/remediation.md" if verdict == "NO GO" else None,
        generated_at="2026-09-16T00:00:00Z",
    )


def test_audit_assessment_members_and_dispositions_are_closed() -> None:
    expected_dispositions = {
        AuditAssessment.COVERED: AuditDisposition.NON_BLOCKING,
        AuditAssessment.MISSING: AuditDisposition.BLOCKING,
        AuditAssessment.ODD: AuditDisposition.NON_BLOCKING,
        AuditAssessment.CONFLICT: AuditDisposition.BLOCKING,
        AuditAssessment.NAMED_DEVIATION: AuditDisposition.PRE_SUBMISSION_ONLY,
        AuditAssessment.UNPRESCRIBED_SUBSTITUTION: AuditDisposition.BLOCKING,
    }

    assert {member.name for member in AuditAssessment} == set(expected_dispositions)
    assert {member.value for member in AuditAssessment} == {
        "COVERED",
        "MISSING",
        "ODD",
        "CONFLICT",
        "NAMED_DEVIATION",
        "UNPRESCRIBED_SUBSTITUTION",
    }
    assert {member: member.disposition for member in AuditAssessment} == expected_dispositions
    assert {member.value for member in AuditAssessment if member.blocking} == {
        "MISSING",
        "CONFLICT",
        "UNPRESCRIBED_SUBSTITUTION",
    }


def test_closure_vocabulary_is_derived_from_audit_assessment_dispositions() -> None:
    assert CLOSURE_ROW_ALLOWED_ASSESSMENTS == {
        member.value
        for member in AuditAssessment
        if member.disposition is not AuditDisposition.PRE_SUBMISSION_ONLY
    }
    assert CLOSURE_ROW_BLOCKING_ASSESSMENTS == {
        member.value for member in AuditAssessment if member.blocking
    }


def test_named_deviation_is_rejected_when_minting_but_loadable_from_history() -> None:
    with pytest.raises(ValueError, match="NAMED_DEVIATION.*submittable"):
        AuditAssessmentRow.create(
            requirement_id="REQ-NEW",
            requirement_text="New rows must use a submittable assessment.",
            assessment=AuditAssessment.NAMED_DEVIATION,
            evidence_summary="The named deviation has not yet been resolved.",
        )

    restored = _historical_named_deviation_row()

    assert restored.assessment is AuditAssessment.NAMED_DEVIATION
    assert restored.requirement_id == "REQ-HISTORY"


def test_go_closure_report_rejects_unprescribed_substitution() -> None:
    errors = _closure_report("UNPRESCRIBED_SUBSTITUTION", verdict="GO").validate()

    assert errors


def test_assessment_disposition_uses_an_exhaustive_match() -> None:
    tree = ast.parse(_AUDIT_ASSESSMENT_SOURCE.read_text(encoding="utf-8"))
    assessment_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AuditAssessment"
    )
    disposition = next(
        node
        for node in assessment_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "disposition"
    )

    assert any(isinstance(node, ast.Match) for node in ast.walk(disposition))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assert_never"
        for node in ast.walk(disposition)
    )
