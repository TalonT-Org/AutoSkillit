"""Independent verifier for closure-mode reports (IL-0, stdlib-only).

Reconstructs every hash from raw inputs and compares against the report.
Accumulates errors (does not short-circuit) so a single report can be
diagnosed in one pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .closure_hashing import (
    compute_file_hash,
    compute_report_hash,
    compute_request_hash,
    compute_row_hash,
)
from .io import read_versioned_json
from .path_containment import ContainmentError, resolve_contained_path
from .types._type_closure_report import ClosureReport

__all__ = ["VerificationResult", "verify_closure_report"]


@dataclass(frozen=True, slots=True)
class VerificationResult:
    success: bool
    verdict: str | None
    errors: tuple[str, ...]
    report_path: str | None


def verify_closure_report(
    report_path: Path,
    authority_path: Path,
    authority_hash: str,
    output_root: Path,
    plan_paths: tuple[Path, ...],
    base_sha: str,
    diff_sha: str,
    target_sha: str,
) -> VerificationResult:
    errors: list[str] = []

    try:
        resolve_contained_path(report_path, output_root)
    except ContainmentError as exc:
        errors.append(f"report containment failed: {exc}")
        return VerificationResult(
            success=False, verdict=None, errors=tuple(errors), report_path=str(report_path)
        )
    except OSError as exc:
        errors.append(f"report path inaccessible: {exc}")
        return VerificationResult(
            success=False, verdict=None, errors=tuple(errors), report_path=str(report_path)
        )

    raw = read_versioned_json(Path(report_path), expected_version=1)
    if raw is None:
        errors.append("report unreadable or schema_version mismatch")
        return VerificationResult(
            success=False, verdict=None, errors=tuple(errors), report_path=str(report_path)
        )

    try:
        report = ClosureReport.from_dict(raw)
    except ValueError as exc:
        errors.append(f"report parse failed: {exc}")
        return VerificationResult(
            success=False, verdict=None, errors=tuple(errors), report_path=str(report_path)
        )

    try:
        computed_authority_hash = compute_file_hash(authority_path)
    except OSError as exc:
        errors.append(f"authority_path unreadable: {exc}")
        computed_authority_hash = None
    if computed_authority_hash is not None and computed_authority_hash != authority_hash:
        errors.append(
            f"authority_hash argument mismatch: expected {authority_hash}, got "
            f"{computed_authority_hash}"
        )
    if report.authority_hash != authority_hash:
        errors.append(
            f"report.authority_hash mismatch: report says {report.authority_hash}, expected "
            f"{authority_hash}"
        )

    plan_file_hashes: list[str | None] = []
    can_compute_request = True
    if len(report.plan_hashes) != len(plan_paths):
        errors.append(
            f"plan_hashes count mismatch: report has {len(report.plan_hashes)}, "
            f"spec has {len(plan_paths)}"
        )
    else:
        for idx, p in enumerate(plan_paths):
            try:
                computed = compute_file_hash(p)
            except OSError as exc:
                errors.append(f"plan_paths[{idx}] unreadable: {exc}")
                computed = None
                can_compute_request = False
            plan_file_hashes.append(computed)
            if computed is not None and report.plan_hashes[idx] != computed:
                errors.append(
                    f"plan_hashes[{idx}] mismatch: report says {report.plan_hashes[idx]}, "
                    f"computed {computed}"
                )

    if can_compute_request:
        valid_hashes = [h for h in plan_file_hashes if h is not None]
        computed_request_hash: str | None = compute_request_hash(
            authority_hash,
            valid_hashes,
            base_sha,
            diff_sha,
            target_sha,
        )
    else:
        computed_request_hash = None
        errors.append("cannot compute request_hash: plan file(s) unreadable")
    if computed_request_hash is not None and report.request_hash != computed_request_hash:
        errors.append(
            f"request_hash mismatch: report says {report.request_hash}, "
            f"computed {computed_request_hash}"
        )

    for idx, row in enumerate(report.rows):
        expected = compute_row_hash(
            row.requirement_id,
            row.requirement_text,
            row.assessment,
            row.evidence_summary,
            row.source_file,
            row.source_line,
            row.source_section,
        )
        if row.row_hash != expected:
            errors.append(f"row[{idx}].row_hash mismatch (content tampered)")

    expected_report_hash = compute_report_hash(
        report.request_hash, [r.row_hash for r in report.rows], report.verdict
    )
    if report.report_hash != expected_report_hash:
        errors.append(
            f"report_hash mismatch: report says {report.report_hash}, "
            f"computed {expected_report_hash}"
        )

    errors.extend(report.validate())

    success = len(errors) == 0
    return VerificationResult(
        success=success,
        verdict=report.verdict if success else None,
        errors=tuple(errors),
        report_path=str(report_path),
    )
