"""Typed, server-owned audit artifact producers.

The semantic and disposition handlers are registered before the atomic
admission-ledger cutover, but fail closed until that parent-owned dependency is
installed. Standalone evidence is independent of admission state and is fully
writable through this module.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    STANDALONE_AUDIT_EVIDENCE_KIND,
    STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    ArtifactRef,
    AuditAssessmentRow,
    AuditOutcomeStatus,
    AuditSemanticResult,
    AuditVerdict,
    PlanDispositionReport,
    StandaloneAuditEvidence,
    canonical_json_bytes,
    compute_bytes_hash,
    get_logger,
    write_canonical_versioned_json,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


class _SemanticInputError(ValueError):
    """Raised when a typed child-owned semantic argument is invalid."""


def _artifact_ref(value: object, *, field_name: str) -> ArtifactRef:
    if not isinstance(value, dict):
        raise _SemanticInputError(f"{field_name} must be an object")
    try:
        return ArtifactRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _SemanticInputError(f"{field_name} is invalid: {exc}") from exc


def _assessment(value: object, *, index: int) -> AuditAssessmentRow:
    if not isinstance(value, dict):
        raise _SemanticInputError(f"assessments[{index}] must be an object")
    try:
        return AuditAssessmentRow.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _SemanticInputError(f"assessments[{index}] is invalid: {exc}") from exc


def _build_semantic_result(
    *,
    audited_plan_refs: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    verdict: str,
    remediation_ref: dict[str, Any] | None,
) -> AuditSemanticResult:
    try:
        return AuditSemanticResult(
            schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
            audited_plan_refs=tuple(
                _artifact_ref(value, field_name=f"audited_plan_refs[{index}]")
                for index, value in enumerate(audited_plan_refs)
            ),
            assessments=tuple(
                _assessment(value, index=index) for index, value in enumerate(assessments)
            ),
            verdict=AuditVerdict(verdict),
            remediation_ref=(
                _artifact_ref(remediation_ref, field_name="remediation_ref")
                if remediation_ref is not None
                else None
            ),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, _SemanticInputError):
            raise
        raise _SemanticInputError(f"invalid audit semantics: {exc}") from exc


def _write_semantic_result(path: Path, result: AuditSemanticResult) -> None:
    payload = result.to_dict()
    payload.pop("schema_version")
    write_canonical_versioned_json(
        path,
        payload,
        AUDIT_SEMANTIC_SCHEMA_VERSION,
        exclusive=True,
    )


def _write_standalone_evidence(path: Path, evidence: StandaloneAuditEvidence) -> None:
    payload = evidence.to_dict()
    payload.pop("schema_version")
    write_canonical_versioned_json(
        path,
        payload,
        STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
        exclusive=True,
    )


def _write_disposition_report(path: Path, report: PlanDispositionReport) -> None:
    payload = report.to_dict()
    payload.pop("schema_version")
    write_canonical_versioned_json(
        path,
        payload,
        AUDIT_CYCLE_SCHEMA_VERSION,
        exclusive=True,
    )


def _write_plan_association(path: Path, association: dict[str, Any]) -> None:
    payload = dict(association)
    schema_version = payload.pop("schema_version")
    if schema_version != AUDIT_CYCLE_SCHEMA_VERSION:
        raise _SemanticInputError("plan association has an invalid schema version")
    write_canonical_versioned_json(
        path,
        payload,
        AUDIT_CYCLE_SCHEMA_VERSION,
        exclusive=True,
    )


def _write_or_verify_standalone_evidence(
    root: Path,
    evidence: StandaloneAuditEvidence,
) -> tuple[Path, str]:
    canonical = canonical_json_bytes(evidence.to_dict())
    digest = compute_bytes_hash(canonical)
    path = root / "standalone-audit-evidence" / f"{digest.removeprefix('sha256:')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_standalone_evidence(path, evidence)
    except FileExistsError:
        if path.read_bytes() != canonical:
            raise _SemanticInputError(
                "standalone audit evidence path contains different bytes"
            ) from None
    return path, digest


def write_standalone_audit_evidence_sync(
    *,
    temp_root: Path,
    audited_plan_refs: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    verdict: str,
    remediation_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    """Construct and canonically persist standalone evidence. Never raises."""
    try:
        semantic = _build_semantic_result(
            audited_plan_refs=audited_plan_refs,
            assessments=assessments,
            verdict=verdict,
            remediation_ref=remediation_ref,
        )
        evidence = StandaloneAuditEvidence(
            schema_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
            kind=STANDALONE_AUDIT_EVIDENCE_KIND,
            audited_plan_refs=semantic.audited_plan_refs,
            assessments=semantic.assessments,
            verdict=semantic.verdict,
            remediation_ref=semantic.remediation_ref,
        )
        path, digest = _write_or_verify_standalone_evidence(temp_root, evidence)
        return {
            "success": True,
            "audit_status": AuditOutcomeStatus.NON_PUBLISHED_STANDALONE.value,
            "standalone_evidence_path": str(path),
            "content_digest": digest,
        }
    except Exception as exc:
        logger.error("write_standalone_audit_evidence_sync failed", exc_info=True)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _dormant_admission_failure(operation: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            f"{operation} is unavailable until the parent-owned audit admission "
            "ledger is installed"
        ),
    }


def _validate_disposition_inputs(
    *,
    authority_path: str,
    new_plan_path: str,
    new_plan_media_type: str,
    new_plan_schema_version: int,
    dispositions: list[dict[str, Any]],
) -> None:
    if not authority_path:
        raise _SemanticInputError("authority_path must be non-empty")
    if not new_plan_path or not new_plan_media_type:
        raise _SemanticInputError("new_plan_path and new_plan_media_type must be non-empty")
    if isinstance(new_plan_schema_version, bool) or not isinstance(new_plan_schema_version, int):
        raise _SemanticInputError("new_plan_schema_version must be an integer")
    if not dispositions:
        raise _SemanticInputError("dispositions must be a non-empty object array")
    for row in dispositions:
        if not isinstance(row, dict):
            raise _SemanticInputError("dispositions must be a non-empty object array")


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("write_audit_semantic_result")
async def write_audit_semantic_result(
    reservation_handle: str,
    audited_plan_refs: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    verdict: str,
    remediation_ref: dict[str, Any] | None = None,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Submit child-owned semantics through an opaque parent reservation handle.

    The caller supplies no execution identity, lifecycle value, output path, or
    containment root. Never raises.
    """
    try:
        del ctx
        if not reservation_handle:
            raise _SemanticInputError("reservation_handle must be non-empty")
        _build_semantic_result(
            audited_plan_refs=audited_plan_refs,
            assessments=assessments,
            verdict=verdict,
            remediation_ref=remediation_ref,
        )
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        started = time.monotonic()
        try:
            result = _dormant_admission_failure("write_audit_semantic_result")
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - started)
        return json.dumps(result)
    except Exception as exc:
        logger.error("write_audit_semantic_result failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("write_standalone_audit_evidence")
async def write_standalone_audit_evidence(
    audited_plan_refs: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    verdict: str,
    remediation_ref: dict[str, Any] | None = None,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Write standalone evidence beneath the server-owned temporary root. Never raises."""
    try:
        del ctx
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        started = time.monotonic()
        try:
            result = write_standalone_audit_evidence_sync(
                temp_root=tool_ctx.temp_dir,
                audited_plan_refs=audited_plan_refs,
                assessments=assessments,
                verdict=verdict,
                remediation_ref=remediation_ref,
            )
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - started)
        return json.dumps(result)
    except Exception as exc:
        logger.error("write_standalone_audit_evidence failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("write_audit_disposition_bundle")
async def write_audit_disposition_bundle(
    authority_path: str,
    new_plan_path: str,
    new_plan_media_type: str,
    new_plan_schema_version: int,
    dispositions: list[dict[str, Any]],
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Submit child-owned plan dispositions against a trusted authority. Never raises."""
    try:
        del ctx
        _validate_disposition_inputs(
            authority_path=authority_path,
            new_plan_path=new_plan_path,
            new_plan_media_type=new_plan_media_type,
            new_plan_schema_version=new_plan_schema_version,
            dispositions=dispositions,
        )
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        started = time.monotonic()
        try:
            result = _dormant_admission_failure("write_audit_disposition_bundle")
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - started)
        return json.dumps(result)
    except Exception as exc:
        logger.error("write_audit_disposition_bundle failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
