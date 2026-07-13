"""Adversarial attack tests for closure-mode verification.

Covers: forged GO verdicts, report root escape, ref-diff drift, unauthorized rows,
symlink authority, metadata-stability, and validation-level rejections.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CLOSURE_REPORT_SCHEMA_VERSION,
    ClosureReport,
    ClosureRow,
)
from autoskillit.core.closure_hashing import (
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)
from autoskillit.core.closure_verifier import verify_closure_report
from autoskillit.core.io import write_versioned_json
from autoskillit.core.path_containment import ContainmentError, resolve_contained_path

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


def _write_authority(tmp_path: Path) -> tuple[str, str]:
    p = tmp_path / "authority.bin"
    p.write_bytes(b"authority-bytes")
    return str(p), compute_file_hash(str(p))


def _make_report(
    authority_hash: str,
    rows: tuple[ClosureRow, ...],
    *,
    base_sha: str = "main",
    diff_sha: str = "diff",
    target_sha: str = "tgt",
    verdict: str = "GO",
    remediation_path: str | None = None,
    requirement_ids: tuple[str, ...] | None = None,
    plan_hashes: tuple[str, ...] = (),
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
        generated_at="2026-07-13T00:00:00Z",
    )


def _write_report(report: ClosureReport, out_root: Path) -> Path:
    out_root.mkdir(exist_ok=True)
    report_path = out_root / "closure_report.json"
    write_versioned_json(
        report_path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION
    )
    return report_path


class TestAdversarialAttacks:
    def test_attack_forged_go_with_missing_findings(self, tmp_path: Path) -> None:
        """Report with verdict=GO but one row has assessment=MISSING — verifier rejects."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        rows = (_row("REQ-1", "MISSING"),)
        report = _make_report(authority_hash, rows, verdict="GO")
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False

    def test_attack_report_root_escape(self, tmp_path: Path) -> None:
        """Report path outside output_root → ContainmentError."""
        out_root = tmp_path / "out"
        out_root.mkdir()
        # Write report OUTSIDE out_root
        escape_dir = tmp_path / "escape"
        escape_dir.mkdir()
        authority_path, authority_hash = _write_authority(tmp_path)
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows)
        report_path = escape_dir / "closure_report.json"
        write_versioned_json(
            report_path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION
        )
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("containment" in e.lower() for e in result.errors)

    def test_attack_ref_diff_drift(self, tmp_path: Path) -> None:
        """Report base_sha mismatch: verifier reconstructs with different SHA."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        # Report built with base_sha="main" but verifier reconstructs with base_sha="drifted"
        rows = (_row("REQ-1"),)
        report = _make_report(authority_hash, rows, base_sha="main")
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="drifted",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("request_hash" in e for e in result.errors)

    def test_attack_symlink_authority_file(self, tmp_path: Path) -> None:
        """Authority path is symlink to file outside root → ContainmentError."""
        out_root = tmp_path / "out"
        out_root.mkdir()
        # Create real authority file outside root
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        real_authority = real_dir / "real_authority.bin"
        real_authority.write_bytes(b"real-authority")
        # Create symlink inside root pointing to real file
        link_dir = tmp_path / "links"
        link_dir.mkdir()
        link_authority = link_dir / "authority_link.bin"
        link_authority.symlink_to(real_authority)
        # Verify containment rejects the symlink
        with pytest.raises(ContainmentError):
            resolve_contained_path(link_authority, link_dir)

    def test_attack_authority_modified_between_reads(self, tmp_path: Path) -> None:
        """Authority content changes: check_metadata_stable detects TOCTOU drift."""
        from autoskillit.core.path_containment import check_metadata_stable

        p = tmp_path / "authority.bin"
        p.write_bytes(b"original")
        pre_stat = p.stat()
        p.write_bytes(b"tampered")
        post_stat = p.stat()
        with pytest.raises(ContainmentError):
            check_metadata_stable(p, pre_stat, post_stat)

    def test_attack_extra_unauthorized_rows(self, tmp_path: Path) -> None:
        """4-plan authority has 72 rows, report includes 83 → row count mismatch."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        # 72 requirement_ids but 83 rows → mismatch
        rows = tuple(_row(f"REQ-{i:03d}") for i in range(83))
        requirement_ids = tuple(f"REQ-{i:03d}" for i in range(72))
        report = _make_report(authority_hash, rows, requirement_ids=requirement_ids)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("length" in e.lower() for e in result.errors)

    def test_attack_missing_rows_valid_hash(self, tmp_path: Path) -> None:
        """Report omits one row with adjusted report_hash → requirement_ids mismatch."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        # 2 rows but only 1 in requirement_ids
        rows = (_row("REQ-1"), _row("REQ-2"))
        requirement_ids = ("REQ-1",)
        report = _make_report(authority_hash, rows, requirement_ids=requirement_ids)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False

    def test_attack_duplicate_requirement_ids(self, tmp_path: Path) -> None:
        """Two rows with same requirement_id → validate() error."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        rows = (_row("REQ-1"), _row("REQ-1"))
        report = _make_report(authority_hash, rows)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False

    def test_attack_reordered_rows(self, tmp_path: Path) -> None:
        """Rows in different order than authority's requirement_ids → row_hash mismatch."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        # Rows are REQ-2 first, then REQ-1, but requirement_ids = REQ-1, REQ-2
        row1 = _row("REQ-1")
        row2 = _row("REQ-2")
        rows = (row2, row1)  # swapped
        requirement_ids = ("REQ-1", "REQ-2")
        # Build report with original order for correct request/report hashes
        ordered_rows = (row1, row2)
        report = _make_report(authority_hash, ordered_rows, requirement_ids=requirement_ids)
        # Now tamper: swap rows in the report
        tampered = ClosureReport(
            schema_version=report.schema_version,
            request_hash=report.request_hash,
            authority_hash=report.authority_hash,
            plan_hashes=report.plan_hashes,
            base_sha=report.base_sha,
            diff_sha=report.diff_sha,
            target_sha=report.target_sha,
            requirement_ids=report.requirement_ids,
            rows=rows,
            verdict=report.verdict,
            report_hash=report.report_hash,
            remediation_path=report.remediation_path,
            generated_at=report.generated_at,
        )
        report_path = _write_report(tampered, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False

    def test_attack_no_go_missing_remediation(self, tmp_path: Path) -> None:
        """verdict="NO GO" with remediation_path=None → validate() error."""
        authority_path, authority_hash = _write_authority(tmp_path)
        out_root = tmp_path / "out"
        rows = (_row("REQ-1", "MISSING"),)
        report = _make_report(authority_hash, rows, verdict="NO GO", remediation_path=None)
        report_path = _write_report(report, out_root)
        result = verify_closure_report(
            report_path=report_path,
            authority_path=Path(authority_path),
            authority_hash=authority_hash,
            output_root=out_root,
            plan_paths=(),
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
        )
        assert result.success is False
        assert any("remediation" in e.lower() for e in result.errors)
