"""Tests for the execution-layer closure verification gate in _build_skill_result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    CLOSURE_REPORT_SCHEMA_VERSION,
    ClosureAuthoritySpec,
    ClosureReport,
    ClosureRow,
)
from autoskillit.core.closure_hashing import (
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)
from autoskillit.core.io import write_versioned_json
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import _build_skill_result
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_closure_spec(
    authority_path: str,
    authority_hash: str,
    plan_paths: tuple[str, ...] = (),
    base_sha: str = "main",
    diff_sha: str = "diff",
    target_sha: str = "tgt",
) -> ClosureAuthoritySpec:
    return ClosureAuthoritySpec(
        authority_path=authority_path,
        authority_hash=authority_hash,
        plan_paths=plan_paths,
        base_sha=base_sha,
        diff_sha=diff_sha,
        target_sha=target_sha,
    )


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


def _write_valid_report(
    tmp_path: Path,
    authority_path: str,
    authority_hash: str,
    plan_paths: tuple[str, ...] = (),
    base_sha: str = "main",
    diff_sha: str = "diff",
    target_sha: str = "tgt",
    rows: tuple[ClosureRow, ...] = (_row("REQ-1"),),
    verdict: str = "GO",
) -> Path:
    out_root = tmp_path / "out"
    out_root.mkdir(exist_ok=True)
    plan_hashes = tuple(compute_file_hash(p) for p in plan_paths)
    request_hash = compute_request_hash(
        authority_hash, list(plan_hashes), base_sha, diff_sha, target_sha
    )
    requirement_ids = tuple(r.requirement_id for r in rows)
    report = ClosureReport(
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
        remediation_path=None,
        generated_at="2026-07-13T00:00:00Z",
    )
    report_path = out_root / "closure_report.json"
    write_versioned_json(
        report_path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION
    )
    return out_root


def _make_authority(tmp_path: Path) -> tuple[str, str]:
    p = tmp_path / "authority.bin"
    p.write_bytes(b"authority-bytes")
    return str(p), compute_file_hash(str(p))


def _make_plan(tmp_path: Path) -> tuple[str, str]:
    p = tmp_path / "plan.md"
    p.write_bytes(b"# plan content")
    return str(p), compute_file_hash(str(p))


class TestClosureGate:
    """Execution-layer closure verification gate: demote unverifiable results."""

    def test_closure_gate_demotes_on_missing_report(self, tmp_path: Path) -> None:
        """ClosureAuthoritySpec active + closure_report_root set, no report file."""
        authority_path, authority_hash = _make_authority(tmp_path)
        out_root = tmp_path / "out"
        out_root.mkdir()
        closure_spec = _make_closure_spec(authority_path, authority_hash)
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=""),
            skill_command="/autoskillit:audit-impl",
            closure_spec=closure_spec,
            closure_report_root=out_root,
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is False
        assert sr.is_error is True
        assert sr.subtype == "closure_verification_failed"

    def test_closure_gate_demotes_on_invalid_report(self, tmp_path: Path) -> None:
        """Report exists but fails verification (tampered hash)."""
        authority_path, authority_hash = _make_authority(tmp_path)
        plan_path, _plan_hash = _make_plan(tmp_path)
        out_root = _write_valid_report(
            tmp_path, authority_path, authority_hash, plan_paths=(plan_path,)
        )
        # Tamper with the report file
        bad_report = out_root / "closure_report.json"
        data = json.loads(bad_report.read_text())
        data["report_hash"] = "sha256:" + "0" * 64
        bad_report.write_text(json.dumps(data))

        closure_spec = _make_closure_spec(authority_path, authority_hash, plan_paths=(plan_path,))
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=""),
            skill_command="/autoskillit:audit-impl",
            closure_spec=closure_spec,
            closure_report_root=out_root,
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is False
        assert sr.is_error is True
        assert sr.subtype == "closure_verification_failed"

    def test_closure_gate_passes_on_valid_report(self, tmp_path: Path) -> None:
        """Valid report with correct hashes → result unchanged."""
        authority_path, authority_hash = _make_authority(tmp_path)
        plan_path, _plan_hash = _make_plan(tmp_path)
        out_root = _write_valid_report(
            tmp_path, authority_path, authority_hash, plan_paths=(plan_path,)
        )

        closure_spec = _make_closure_spec(authority_path, authority_hash, plan_paths=(plan_path,))
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=""),
            skill_command="/autoskillit:audit-impl",
            closure_spec=closure_spec,
            closure_report_root=out_root,
            backend=ClaudeCodeBackend(),
        )
        assert sr.is_error is False
        assert sr.subtype != "closure_verification_failed"

    def test_closure_gate_noop_without_spec(self, tmp_path: Path) -> None:
        """closure_spec=None → gate skipped entirely, result unchanged."""
        out_root = tmp_path / "out"
        out_root.mkdir()
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=""),
            skill_command="/autoskillit:audit-impl",
            closure_spec=None,
            closure_report_root=out_root,
            backend=ClaudeCodeBackend(),
        )
        assert sr.subtype != "closure_verification_failed"

    def test_closure_gate_demotes_forged_go(self, tmp_path: Path) -> None:
        """Report claims GO but has MISSING findings — verifier rejects."""
        authority_path, authority_hash = _make_authority(tmp_path)
        plan_path, _plan_hash = _make_plan(tmp_path)
        # Build a report with MISSING row but verdict=GO (forged)
        out_root = tmp_path / "out"
        out_root.mkdir()
        plan_hashes = (compute_file_hash(plan_path),)
        rows = (_row("REQ-1", "MISSING"),)
        request_hash = compute_request_hash(
            authority_hash, list(plan_hashes), "main", "diff", "tgt"
        )
        report = ClosureReport(
            schema_version=CLOSURE_REPORT_SCHEMA_VERSION,
            request_hash=request_hash,
            authority_hash=authority_hash,
            plan_hashes=plan_hashes,
            base_sha="main",
            diff_sha="diff",
            target_sha="tgt",
            requirement_ids=("REQ-1",),
            rows=rows,
            verdict="GO",
            report_hash=compute_report_hash(request_hash, [r.row_hash for r in rows], "GO"),
            remediation_path=None,
            generated_at="2026-07-13T00:00:00Z",
        )
        report_path = out_root / "closure_report.json"
        write_versioned_json(
            report_path, report.to_dict(), schema_version=CLOSURE_REPORT_SCHEMA_VERSION
        )

        closure_spec = _make_closure_spec(authority_path, authority_hash, plan_paths=(plan_path,))
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=""),
            skill_command="/autoskillit:audit-impl",
            closure_spec=closure_spec,
            closure_report_root=out_root,
            backend=ClaudeCodeBackend(),
        )
        assert sr.is_error is True
        assert sr.subtype == "closure_verification_failed"
