"""Tests for the independent closure verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.closure_hashing import (
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)
from autoskillit.core.closure_verifier import verify_closure_report
from autoskillit.core.io import write_versioned_json
from autoskillit.core.types import (
    CLOSURE_REPORT_SCHEMA_VERSION,
    ClosureReport,
    ClosureRow,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _row(req_id: str, assessment: str = "COVERED") -> ClosureRow:
    text = f"req {req_id} text"
    evidence = f"evidence {req_id}"
    return ClosureRow(
        requirement_id=req_id,
        requirement_text=text,
        source_file="/some/file.py",
        source_line=1,
        source_section="Sec",
        assessment=assessment,
        evidence_summary=evidence,
        row_hash=compute_row_hash(req_id, text, assessment, evidence),
    )


def _write_authority(tmp_path, content: bytes = b"authority-bytes") -> str:
    p = tmp_path / "authority.bin"
    p.write_bytes(content)
    return str(p)


def _write_report(report: ClosureReport, output_root) -> str:
    path = output_root / "closure_report.json"
    write_versioned_json(path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION)
    return str(path)


def _make_report(
    authority_hash: str,
    rows: tuple[ClosureRow, ...],
    base_sha: str = "main",
    diff_sha: str = "diff",
    target_sha: str = "tgt",
    *,
    verdict: str = "GO",
    remediation_path: str | None = None,
    requirement_ids: tuple[str, ...] | None = None,
    plan_hashes: tuple[str, ...] = (),
    generated_at: str = "2026-07-13T00:00:00Z",
) -> ClosureReport:
    if requirement_ids is None:
        requirement_ids = tuple(r.requirement_id for r in rows)
    request_hash = compute_request_hash(
        authority_hash, list(plan_hashes), base_sha, diff_sha, target_sha
    )
    return ClosureReport(
        schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
        request_hash=request_hash,
        authority_hash=authority_hash,
        plan_hashes=plan_hashes,
        base_sha=base_sha,
        diff_sha=diff_sha,
        target_sha=target_sha,
        requirement_ids=requirement_ids,
        rows=rows,
        verdict=verdict,
        report_hash=compute_report_hash(request_hash, [r.row_hash for r in rows], verdict),
        remediation_path=remediation_path,
        generated_at=generated_at,
    )


class TestAcceptsValidReports:
    def test_valid_go_report(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"), _row("REQ-2"))
        report = _make_report(authority_hash, rows)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is True
        assert result.verdict == "GO"
        assert result.errors == ()

    def test_valid_no_go_report(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1", "MISSING"),)
        report = _make_report(
            authority_hash,
            rows,
            verdict="NO GO",
            remediation_path="/tmp/remediation.md",
        )
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is True
        assert result.verdict == "NO GO"


class TestRejection:
    def test_rejects_forged_go(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1", "MISSING"),)
        report = _make_report(authority_hash, rows, verdict="GO")
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("MISSING" in e or "verdict" in e for e in result.errors)

    def test_rejects_authority_hash_mismatch(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        real_authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report("sha256:" + "f" * 64, rows)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=real_authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("authority" in e.lower() for e in result.errors)

    def test_rejects_request_hash_mismatch(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows, base_sha="main")
        tampered = ClosureReport(
            schema_version=report.schema_version,
            request_hash="sha256:" + "9" * 64,
            authority_hash=report.authority_hash,
            plan_hashes=report.plan_hashes,
            base_sha=report.base_sha,
            diff_sha=report.diff_sha,
            target_sha=report.target_sha,
            requirement_ids=report.requirement_ids,
            rows=rows,
            verdict=report.verdict,
            report_hash=compute_report_hash(
                "sha256:" + "9" * 64, [r.row_hash for r in rows], "GO"
            ),
            remediation_path=report.remediation_path,
            generated_at=report.generated_at,
        )
        report_path = _write_report(tampered, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("request_hash" in e for e in result.errors)

    def test_rejects_row_hash_tampered(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows)
        tampered_row = ClosureRow(
            requirement_id="REQ-1",
            requirement_text="req REQ-1 text",
            source_file="/some/file.py",
            source_line=1,
            source_section="Sec",
            assessment="COVERED",
            evidence_summary="evidence REQ-1",
            row_hash="sha256:" + "0" * 64,
        )
        tampered = ClosureReport(
            schema_version=report.schema_version,
            request_hash=report.request_hash,
            authority_hash=report.authority_hash,
            plan_hashes=report.plan_hashes,
            base_sha=report.base_sha,
            diff_sha=report.diff_sha,
            target_sha=report.target_sha,
            requirement_ids=("REQ-1",),
            rows=(tampered_row,),
            verdict=report.verdict,
            report_hash=compute_report_hash(report.request_hash, [tampered_row.row_hash], "GO"),
            remediation_path=report.remediation_path,
            generated_at=report.generated_at,
        )
        report_path = _write_report(tampered, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("row_hash" in e for e in result.errors)

    def test_rejects_report_hash_mismatch(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows)
        tampered = ClosureReport(
            schema_version=report.schema_version,
            request_hash=report.request_hash,
            authority_hash=report.authority_hash,
            plan_hashes=report.plan_hashes,
            base_sha=report.base_sha,
            diff_sha=report.diff_sha,
            target_sha=report.target_sha,
            requirement_ids=report.requirement_ids,
            rows=report.rows,
            verdict=report.verdict,
            report_hash="sha256:" + "0" * 64,
            remediation_path=report.remediation_path,
            generated_at=report.generated_at,
        )
        report_path = _write_report(tampered, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("report_hash" in e for e in result.errors)

    def test_rejects_missing_requirement_rows(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows, requirement_ids=("REQ-1", "REQ-2"))
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("length mismatch" in e for e in result.errors)

    def test_rejects_extra_requirement_rows(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"), _row("REQ-2"), _row("REQ-3"))
        report = _make_report(authority_hash, rows, requirement_ids=("REQ-1", "REQ-2"))
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("length mismatch" in e for e in result.errors)

    def test_rejects_reordered_rows(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        r1 = _row("REQ-1")
        r2 = _row("REQ-2")
        request_hash = compute_request_hash(authority_hash, [], "main", "diff", "tgt")
        report = ClosureReport(
            schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
            request_hash=request_hash,
            authority_hash=authority_hash,
            plan_hashes=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
            requirement_ids=("REQ-1", "REQ-2"),
            rows=(r2, r1),
            verdict="GO",
            report_hash=compute_report_hash(request_hash, [r2.row_hash, r1.row_hash], "GO"),
            remediation_path=None,
            generated_at="2026-07-13T00:00:00Z",
        )
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("requirement_id" in e for e in result.errors)

    def test_rejects_report_root_escape(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        outer = tmp_path / "elsewhere"
        outer.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows)
        escape_path = outer / "escape.json"
        write_versioned_json(
            escape_path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION
        )
        result = verify_closure_report(
            report_path=escape_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("containment" in e for e in result.errors)

    def test_rejects_no_go_without_remediation(self, tmp_path) -> None:
        out_root = tmp_path / "out"
        out_root.mkdir()
        authority_path = _write_authority(tmp_path)
        authority_hash = compute_file_hash(authority_path)
        rows = (_row("REQ-1", "MISSING"),)
        report = _make_report(authority_hash, rows, verdict="NO GO", remediation_path=None)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=Path(report_path),
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("remediation" in e for e in result.errors)
