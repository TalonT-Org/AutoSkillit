"""Typed, server-owned audit semantic, standalone, and disposition producers."""

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
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleVerifier,
    AuditDispositionCommitRequest,
    AuditOutcomeStatus,
    AuditSemanticResult,
    AuditVerdict,
    PlanDispositionReport,
    PlanDispositionRow,
    RecipeExecutionId,
    StandaloneAuditEvidence,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
    get_logger,
    read_stable_contained_bytes,
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
    if set(value) != {
        "requirement_id",
        "requirement_text",
        "assessment",
        "evidence_summary",
    }:
        raise _SemanticInputError(
            f"assessments[{index}] must contain only requirement_id, requirement_text, "
            "assessment, and evidence_summary"
        )
    try:
        return AuditAssessmentRow.create(
            requirement_id=value["requirement_id"],
            requirement_text=value["requirement_text"],
            assessment=AuditAssessment(value["assessment"]),
            evidence_summary=value["evidence_summary"],
        )
    except (KeyError, TypeError, ValueError) as exc:
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


def _write_or_verify_semantic_result(
    path: Path,
    allowed_root: Path,
    result: AuditSemanticResult,
) -> tuple[Path, str]:
    canonical = canonical_json_bytes(result.to_dict())
    digest = compute_bytes_hash(canonical)
    try:
        _write_semantic_result(path, result)
    except FileExistsError:
        resolved, existing = read_stable_contained_bytes(
            path,
            allowed_root,
            max_size_bytes=max(1, len(canonical)),
        )
        if resolved != path or existing != canonical:
            raise _SemanticInputError(
                "reserved semantic result path contains different bytes"
            ) from None
    return path, digest


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


def _disposition_rows(values: list[dict[str, Any]]) -> tuple[PlanDispositionRow, ...]:
    rows: list[PlanDispositionRow] = []
    for index, value in enumerate(values):
        if set(value) != {"requirement_id", "disposition", "implementation_step"}:
            raise _SemanticInputError(
                f"dispositions[{index}] must contain only requirement_id, "
                "disposition, and implementation_step"
            )
        try:
            rows.append(
                PlanDispositionRow.create(
                    requirement_id=value["requirement_id"],
                    disposition=value["disposition"],
                    implementation_step=value["implementation_step"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _SemanticInputError(f"dispositions[{index}] is invalid: {exc}") from exc
    return tuple(rows)


def _write_or_verify_disposition_report(
    path: Path,
    allowed_root: Path,
    report: PlanDispositionReport,
) -> bytes:
    canonical = canonical_json_bytes(report.to_dict())
    try:
        _write_disposition_report(path, report)
    except FileExistsError:
        resolved, existing = read_stable_contained_bytes(
            path,
            allowed_root,
            max_size_bytes=max(1, len(canonical)),
        )
        if resolved != path or existing != canonical:
            raise _SemanticInputError(
                "prepared disposition report contains different bytes"
            ) from None
    return canonical


def _write_or_verify_plan_association(
    path: Path,
    allowed_root: Path,
    association: dict[str, Any],
) -> bytes:
    canonical = canonical_json_bytes(association)
    try:
        _write_plan_association(path, association)
    except FileExistsError:
        resolved, existing = read_stable_contained_bytes(
            path,
            allowed_root,
            max_size_bytes=max(1, len(canonical)),
        )
        if resolved != path or existing != canonical:
            raise _SemanticInputError(
                "prepared plan association contains different bytes"
            ) from None
    return canonical


def _write_audit_disposition_bundle_sync(
    *,
    tool_ctx: Any,
    authority_path: str,
    new_plan_path: str,
    new_plan_media_type: str,
    new_plan_schema_version: int,
    dispositions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prepare, verify, and CAS-publish one disposition/association bundle."""
    from autoskillit.server._recipe_execution import get_recipe_execution  # circular-break

    installed = get_recipe_execution(tool_ctx)
    if installed is None:
        raise _SemanticInputError("a trusted recipe execution is not active")
    allowed_root = tool_ctx.project_dir.resolve()
    verifier = AuditCycleVerifier(allowed_root)
    authority = verifier.load_authority(authority_path)
    if authority.execution_generation != installed.snapshot.execution_id:
        raise _SemanticInputError("authority belongs to another recipe execution")
    recipe_execution_id = RecipeExecutionId(authority.execution_generation)
    head = tool_ctx.audit_admission_ledger.current_head(
        recipe_execution_id=recipe_execution_id,
        cycle_id=authority.cycle_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
    )
    if head is None or head.current_authority_digest != authority.authority_digest:
        raise _SemanticInputError("authority is not the trusted current head")

    resolved_plan, plan_bytes = read_stable_contained_bytes(
        new_plan_path,
        allowed_root,
    )
    plan_ref = ArtifactRef(
        locator=str(resolved_plan),
        media_type=new_plan_media_type,
        schema_version=new_plan_schema_version,
        byte_size=len(plan_bytes),
        content_digest=compute_bytes_hash(plan_bytes),
    )
    rows = _disposition_rows(dispositions)
    output_root = (
        tool_ctx.temp_dir.resolve()
        / "audit-disposition"
        / authority.authority_digest.removeprefix("sha256:")
        / plan_ref.content_digest.removeprefix("sha256:")
    )
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "disposition-report.json"
    association_path = output_root / "plan-association.json"
    if report_path.exists():
        generated_at = verifier.load_report(report_path).generated_at
    else:
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = PlanDispositionReport.create(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        audit_round=authority.audit_round,
        parent_authority_digest=authority.authority_digest,
        inventory_digest=authority.inventory_ref.content_digest,
        findings_digest=authority.findings_digest,
        current_plan_ref=plan_ref,
        dispositions=rows,
        generated_at=generated_at,
    )
    report_bytes = _write_or_verify_disposition_report(
        report_path,
        allowed_root,
        report,
    )
    report_ref = ArtifactRef(
        locator=str(report_path),
        media_type="application/json",
        schema_version=AUDIT_CYCLE_SCHEMA_VERSION,
        byte_size=len(report_bytes),
        content_digest=compute_bytes_hash(report_bytes),
    )
    association: dict[str, Any] = {
        "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
        "plan_ref": plan_ref.to_dict(),
        "disposition_ref": report_ref.to_dict(),
        "parent_authority_digest": authority.authority_digest,
    }
    association["association_digest"] = compute_canonical_hash(
        association,
        domain="autoskillit:audit-cycle:plan-association:v1:sha256",
    )
    _write_or_verify_plan_association(
        association_path,
        allowed_root,
        association,
    )
    with tool_ctx.recipe_execution_lock:
        if get_recipe_execution(tool_ctx) is not installed:
            raise _SemanticInputError(
                "active recipe execution changed before disposition publication"
            )
        committed = tool_ctx.audit_admission_ledger.commit_disposition(
            AuditDispositionCommitRequest(
                recipe_execution_id=recipe_execution_id,
                installation_version=installed.installation_version,
                cycle_id=authority.cycle_id,
                scope_id=authority.scope_id,
                part_id=authority.part_id,
                authority_digest=authority.authority_digest,
                plan_digest=plan_ref.content_digest,
                report_digest=report.report_digest,
                report_path=report_path,
                association_digest=association["association_digest"],
                association_path=association_path,
                generated_at=generated_at,
            )
        )
    if not committed.committed:
        raise _SemanticInputError(
            committed.conflict_detail or "disposition publication was rejected"
        )
    return {
        "success": True,
        "plan_disposition_path": str(report_path),
        "plan_association_path": str(association_path),
        "report_digest": report.report_digest,
        "association_digest": association["association_digest"],
        "generated_at": committed.generated_at,
    }


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
        semantic = _build_semantic_result(
            audited_plan_refs=audited_plan_refs,
            assessments=assessments,
            verdict=verdict,
            remediation_ref=remediation_ref,
        )
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        started = time.monotonic()
        try:
            reservation = tool_ctx.audit_admission_ledger.resolve_reservation_handle(
                reservation_handle
            )
            if reservation is None:
                raise _SemanticInputError("reservation handle is stale or invalid")
            path, digest = _write_or_verify_semantic_result(
                reservation.semantic_result_path,
                reservation.allowed_root,
                semantic,
            )
            result = {
                "success": True,
                "audit_semantic_result_path": str(path),
                "semantic_digest": digest,
            }
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
            result = _write_audit_disposition_bundle_sync(
                tool_ctx=tool_ctx,
                authority_path=authority_path,
                new_plan_path=new_plan_path,
                new_plan_media_type=new_plan_media_type,
                new_plan_schema_version=new_plan_schema_version,
                dispositions=dispositions,
            )
        finally:
            if step_name:
                tool_ctx.timing_log.record(step_name, time.monotonic() - started)
        return json.dumps(result)
    except Exception as exc:
        logger.error("write_audit_disposition_bundle failed", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
