"""Tests for ClosureReport and ClosureRow schemas."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from autoskillit.core.closure_hashing import compute_report_hash, compute_row_hash
from autoskillit.core.io import read_versioned_json
from autoskillit.core.types import (
    CLOSURE_REPORT_SCHEMA_VERSION,
    ClosureReport,
    ClosureRow,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _good_row(req_id: str = "REQ-1", assessment: str = "COVERED") -> ClosureRow:
    text = "Some requirement text"
    evidence = "Some evidence summary"
    return ClosureRow(
        requirement_id=req_id,
        requirement_text=text,
        source_file="/some/file.py",
        source_line=42,
        source_section="Section",
        assessment=assessment,
        evidence_summary=evidence,
        row_hash=compute_row_hash(req_id, text, assessment, evidence),
    )


def _good_report(rows: tuple[ClosureRow, ...] | None = None) -> ClosureReport:
    if rows is None:
        rows = (_good_row("REQ-1"),)
    req_ids = tuple(r.requirement_id for r in rows)
    req_hash = "sha256:" + "a" * 64
    request_hash = "sha256:" + "b" * 64
    return ClosureReport(
        schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
        request_hash=request_hash,
        authority_hash=req_hash,
        plan_hashes=(),
        base_sha="",
        diff_sha="",
        target_sha="",
        requirement_ids=req_ids,
        rows=rows,
        verdict="GO",
        report_hash=compute_report_hash(request_hash, [r.row_hash for r in rows], "GO"),
        remediation_path=None,
        generated_at="2026-07-13T00:00:00Z",
    )


class TestRoundtrip:
    def test_roundtrip(self) -> None:
        report = _good_report()
        data = report.to_dict()
        restored = ClosureReport.from_dict(data)
        assert restored == report


class TestValidate:
    def test_rejects_empty_request_hash(self) -> None:
        report = _good_report()
        mutated = replace(report, request_hash="")
        errors = mutated.validate()
        assert any("malformed format" in e for e in errors)
        assert any("report_hash" in e for e in errors)

    def test_rejects_empty_rows(self) -> None:
        report = _good_report(rows=())
        errors = report.validate()
        assert any(
            "rows must be non-empty" in e or "requirement_ids must be non-empty" in e
            for e in errors
        )

    def test_rejects_duplicate_requirement_ids(self) -> None:
        rows = (_good_row("REQ-1"), _good_row("REQ-1"))
        report = _good_report(rows)
        errors = report.validate()
        assert any("duplicate" in e for e in errors)

    def test_rejects_extra_rows(self) -> None:
        rows = (_good_row("REQ-1"), _good_row("REQ-2"))
        report = _good_report(rows)
        mutated = replace(report, requirement_ids=("REQ-1",))
        errors = mutated.validate()
        assert any("length mismatch" in e for e in errors)

    def test_rejects_row_id_mismatch(self) -> None:
        text = "Some requirement text"
        evidence = "Some evidence summary"
        r1 = _good_row("REQ-1")
        bad = ClosureRow(
            requirement_id="WRONG-ID",
            requirement_text=text,
            source_file="/some/file.py",
            source_line=1,
            source_section="Section",
            assessment="COVERED",
            evidence_summary=evidence,
            row_hash=compute_row_hash("WRONG-ID", text, "COVERED", evidence),
        )
        rows = (r1, bad)
        req_hash = "sha256:" + "a" * 64
        request_hash = "sha256:" + "b" * 64
        report = ClosureReport(
            schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
            request_hash=request_hash,
            authority_hash=req_hash,
            plan_hashes=(),
            base_sha="",
            diff_sha="",
            target_sha="",
            requirement_ids=("REQ-1", "REQ-2"),
            rows=rows,
            verdict="GO",
            report_hash=compute_report_hash(request_hash, [r.row_hash for r in rows], "GO"),
            remediation_path=None,
            generated_at="2026-07-13T00:00:00Z",
        )
        errors = report.validate()
        assert any("requirement_id" in e and "requirement_ids" in e for e in errors)

    def test_schema_version_mismatch(self, tmp_path) -> None:
        report = _good_report()
        data = report.to_dict()
        data["schema_version"] = 999
        f = tmp_path / "report.json"
        f.write_text(json.dumps(data))
        assert read_versioned_json(f, expected_version=CLOSURE_REPORT_SCHEMA_VERSION) is None

    def test_row_hash_tampered(self) -> None:
        rows = (_good_row("REQ-1"),)
        report = _good_report(rows)
        text = rows[0].requirement_text
        evidence = rows[0].evidence_summary
        tampered = ClosureRow(
            requirement_id="REQ-1",
            requirement_text=text,
            source_file=rows[0].source_file,
            source_line=rows[0].source_line,
            source_section=rows[0].source_section,
            assessment=rows[0].assessment,
            evidence_summary=evidence,
            row_hash="sha256:" + "0" * 64,
        )
        tampered_report = replace(report, rows=(tampered,))
        errors = tampered_report.validate()
        assert any("row_hash" in e for e in errors)


class TestVerdictRules:
    def test_go_with_missing_assessment_rejected(self) -> None:
        rows = (_good_row("REQ-1", "MISSING"),)
        report = _good_report(rows)
        errors = report.validate()
        assert any("verdict=GO" in e or "MISSING" in e for e in errors)

    def test_no_go_requires_remediation_path(self) -> None:
        rows = (_good_row("REQ-1", "MISSING"),)
        req_ids = ("REQ-1",)
        request_hash = "sha256:" + "b" * 64
        report = ClosureReport(
            schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
            request_hash=request_hash,
            authority_hash="sha256:" + "a" * 64,
            plan_hashes=(),
            base_sha="",
            diff_sha="",
            target_sha="",
            requirement_ids=req_ids,
            rows=rows,
            verdict="NO GO",
            report_hash=compute_report_hash(request_hash, [r.row_hash for r in rows], "NO GO"),
            remediation_path=None,
            generated_at="2026-07-13T00:00:00Z",
        )
        errors = report.validate()
        assert any("remediation" in e for e in errors)
