"""MCP tool handler: write_audit_cycle_artifact.

Server-side construction, digest computation, dataclass validation, and canonical
serialization for the four hash-bound audit-cycle artifact kinds (``authority``,
``inventory``, ``disposition_report``, ``plan_association``) consumed with
``require_canonical=True``. Removes both JSON-canonicalization and digest-arithmetic
from the LLM producer's token-generation path, the way ``commit_files``
(``tools_git.py``) removes git staging/committing from the same sessions.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    _MAX_REFERENCED_ARTIFACTS_PER_CALL,
    _PLAN_ASSOCIATION_DOMAIN,
    _PLAN_ASSOCIATION_KEYS,
    AUDIT_CYCLE_SCHEMA_VERSION,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditVerdict,
    ContainmentError,
    PlanDispositionReport,
    PlanDispositionRow,
    compute_bytes_hash,
    compute_canonical_hash,
    get_logger,
    resolve_contained_path,
    write_canonical_versioned_json,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


class _FieldError(ValueError):
    """Raised when the semantic ``fields`` payload fails structural validation."""


def _require_str(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value:
        raise _FieldError(f"fields[{key!r}] must be a non-empty string")
    return value


def _require_optional_str(fields: dict[str, Any], key: str) -> str | None:
    value = fields.get(key)
    if value is not None and not isinstance(value, str):
        raise _FieldError(f"fields[{key!r}] must be a string or null")
    return value


def _require_int(fields: dict[str, Any], key: str) -> int:
    value = fields.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _FieldError(f"fields[{key!r}] must be an integer")
    return value


def _artifact_ref_spec(entry: object, label: str) -> tuple[str, str, int]:
    """Validate a caller-declared ``{locator, media_type, schema_version}`` shape.

    Cheap, no file I/O — the referenced file is read later, only after every
    structural check in the request has passed.
    """
    if not isinstance(entry, dict):
        raise _FieldError(f"{label} must be an object")
    locator = entry.get("locator")
    media_type = entry.get("media_type")
    schema_version = entry.get("schema_version")
    if not isinstance(locator, str) or not locator:
        raise _FieldError(f"{label}.locator must be a non-empty string")
    if not isinstance(media_type, str) or not media_type:
        raise _FieldError(f"{label}.media_type must be a non-empty string")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise _FieldError(f"{label}.schema_version must be an integer")
    return locator, media_type, schema_version


def _bound_referenced_artifacts(count: int) -> None:
    if count > _MAX_REFERENCED_ARTIFACTS_PER_CALL:
        raise _FieldError(
            f"referenced-artifact count {count} exceeds "
            f"_MAX_REFERENCED_ARTIFACTS_PER_CALL={_MAX_REFERENCED_ARTIFACTS_PER_CALL}"
        )


def _resolve_ref(spec: tuple[str, str, int], cwd: str) -> ArtifactRef:
    """Containment-checked read of an already-existing referenced artifact.

    The server computes ``byte_size``/``content_digest`` from the actual bytes —
    the caller never hand-computes a hash.
    """
    locator, media_type, schema_version = spec
    resolved = resolve_contained_path(locator, cwd)
    data = resolved.read_bytes()
    return ArtifactRef(
        locator=locator,
        media_type=media_type,
        schema_version=schema_version,
        byte_size=len(data),
        content_digest=compute_bytes_hash(data),
    )


def _build_authority(fields: dict[str, Any], cwd: str) -> dict[str, Any]:
    execution_generation = _require_str(fields, "execution_generation")
    cycle_id = _require_str(fields, "cycle_id")
    plan_set_id = _require_str(fields, "plan_set_id")
    scope_id = _require_str(fields, "scope_id")
    part_id = _require_str(fields, "part_id")
    audit_round = _require_int(fields, "audit_round")
    parent_authority_digest = _require_optional_str(fields, "parent_authority_digest")
    generated_at = _require_str(fields, "generated_at")

    audited_plan_refs_raw = fields.get("audited_plan_refs")
    if not isinstance(audited_plan_refs_raw, list) or not audited_plan_refs_raw:
        raise _FieldError("fields['audited_plan_refs'] must be a non-empty list")
    audited_plan_specs = [
        _artifact_ref_spec(entry, f"fields['audited_plan_refs'][{index}]")
        for index, entry in enumerate(audited_plan_refs_raw)
    ]
    inventory_spec = _artifact_ref_spec(fields.get("inventory_ref"), "fields['inventory_ref']")
    remediation_raw = fields.get("remediation_ref")
    remediation_spec = (
        _artifact_ref_spec(remediation_raw, "fields['remediation_ref']")
        if remediation_raw is not None
        else None
    )

    referenced_count = len(audited_plan_specs) + 1 + (1 if remediation_spec is not None else 0)
    _bound_referenced_artifacts(referenced_count)

    assessments_raw = fields.get("assessments")
    if not isinstance(assessments_raw, list) or not assessments_raw:
        raise _FieldError("fields['assessments'] must be a non-empty list")
    assessment_rows: list[AuditAssessmentRow] = []
    for index, row in enumerate(assessments_raw):
        if not isinstance(row, dict):
            raise _FieldError(f"fields['assessments'][{index}] must be an object")
        assessment_raw = row.get("assessment")
        requirement_id = row.get("requirement_id")
        requirement_text = row.get("requirement_text")
        evidence_summary = row.get("evidence_summary")
        if (
            not isinstance(assessment_raw, str)
            or not isinstance(requirement_id, str)
            or not isinstance(requirement_text, str)
            or not isinstance(evidence_summary, str)
        ):
            raise _FieldError(
                f"fields['assessments'][{index}] requires string assessment, requirement_id, "
                "requirement_text, and evidence_summary"
            )
        try:
            assessment_value = AuditAssessment(assessment_raw)
        except (TypeError, ValueError) as exc:
            raise _FieldError(f"fields['assessments'][{index}].assessment invalid: {exc}") from exc
        try:
            assessment_rows.append(
                AuditAssessmentRow.create(
                    requirement_id=requirement_id,
                    requirement_text=requirement_text,
                    assessment=assessment_value,
                    evidence_summary=evidence_summary,
                )
            )
        except (TypeError, ValueError) as exc:
            raise _FieldError(f"fields['assessments'][{index}] invalid: {exc}") from exc

    verdict_raw = fields.get("verdict")
    if not isinstance(verdict_raw, str):
        raise _FieldError("fields['verdict'] must be a string")
    try:
        verdict = AuditVerdict(verdict_raw)
    except ValueError as exc:
        raise _FieldError(f"fields['verdict'] invalid: {exc}") from exc

    # Every structural check above (assessment values, verdict, required keys,
    # referenced-artifact count bound) has passed — only now do we touch disk.
    audited_plan_refs = tuple(_resolve_ref(spec, cwd) for spec in audited_plan_specs)
    inventory_ref = _resolve_ref(inventory_spec, cwd)
    remediation_ref = _resolve_ref(remediation_spec, cwd) if remediation_spec is not None else None

    authority = AuditCycleAuthority.create(
        execution_generation=execution_generation,
        cycle_id=cycle_id,
        plan_set_id=plan_set_id,
        scope_id=scope_id,
        part_id=part_id,
        audit_round=audit_round,
        parent_authority_digest=parent_authority_digest,
        audited_plan_refs=audited_plan_refs,
        inventory_ref=inventory_ref,
        assessments=tuple(assessment_rows),
        verdict=verdict,
        remediation_ref=remediation_ref,
        generated_at=generated_at,
    )
    return authority.to_dict()


def _build_disposition_report(fields: dict[str, Any], cwd: str) -> dict[str, Any]:
    execution_generation = _require_str(fields, "execution_generation")
    cycle_id = _require_str(fields, "cycle_id")
    plan_set_id = _require_str(fields, "plan_set_id")
    scope_id = _require_str(fields, "scope_id")
    part_id = _require_str(fields, "part_id")
    audit_round = _require_int(fields, "audit_round")
    parent_authority_digest = _require_str(fields, "parent_authority_digest")
    inventory_digest = _require_str(fields, "inventory_digest")
    findings_digest = _require_str(fields, "findings_digest")
    generated_at = _require_str(fields, "generated_at")

    current_plan_spec = _artifact_ref_spec(
        fields.get("current_plan_ref"), "fields['current_plan_ref']"
    )
    _bound_referenced_artifacts(1)

    dispositions_raw = fields.get("dispositions")
    if not isinstance(dispositions_raw, list) or not dispositions_raw:
        raise _FieldError("fields['dispositions'] must be a non-empty list")
    disposition_rows: list[PlanDispositionRow] = []
    for index, row in enumerate(dispositions_raw):
        if not isinstance(row, dict):
            raise _FieldError(f"fields['dispositions'][{index}] must be an object")
        requirement_id = row.get("requirement_id")
        disposition = row.get("disposition")
        implementation_step = row.get("implementation_step")
        if not isinstance(requirement_id, str) or not isinstance(disposition, str):
            raise _FieldError(
                f"fields['dispositions'][{index}] requires string requirement_id and disposition"
            )
        if implementation_step is not None and not isinstance(implementation_step, str):
            raise _FieldError(
                f"fields['dispositions'][{index}].implementation_step must be a string or null"
            )
        try:
            disposition_rows.append(
                PlanDispositionRow.create(
                    requirement_id=requirement_id,
                    disposition=disposition,
                    implementation_step=implementation_step,
                )
            )
        except (TypeError, ValueError) as exc:
            raise _FieldError(f"fields['dispositions'][{index}] invalid: {exc}") from exc

    current_plan_ref = _resolve_ref(current_plan_spec, cwd)

    report = PlanDispositionReport.create(
        execution_generation=execution_generation,
        cycle_id=cycle_id,
        plan_set_id=plan_set_id,
        scope_id=scope_id,
        part_id=part_id,
        audit_round=audit_round,
        parent_authority_digest=parent_authority_digest,
        inventory_digest=inventory_digest,
        findings_digest=findings_digest,
        current_plan_ref=current_plan_ref,
        dispositions=tuple(disposition_rows),
        generated_at=generated_at,
    )
    return report.to_dict()


def _build_plan_association(fields: dict[str, Any], cwd: str) -> dict[str, Any]:
    parent_authority_digest = _require_str(fields, "parent_authority_digest")
    plan_spec = _artifact_ref_spec(fields.get("plan_ref"), "fields['plan_ref']")
    disposition_spec = _artifact_ref_spec(
        fields.get("disposition_ref"), "fields['disposition_ref']"
    )
    _bound_referenced_artifacts(2)

    plan_ref = _resolve_ref(plan_spec, cwd)
    disposition_ref = _resolve_ref(disposition_spec, cwd)

    payload: dict[str, Any] = {
        "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
        "plan_ref": plan_ref.to_dict(),
        "disposition_ref": disposition_ref.to_dict(),
        "parent_authority_digest": parent_authority_digest,
    }
    payload["association_digest"] = compute_canonical_hash(
        payload, domain=_PLAN_ASSOCIATION_DOMAIN
    )
    if frozenset(payload) != _PLAN_ASSOCIATION_KEYS:
        raise _FieldError("plan_association payload key set does not match _PLAN_ASSOCIATION_KEYS")
    return payload


def _build_inventory(fields: dict[str, Any], cwd: str) -> dict[str, Any]:
    del cwd  # inventory has no referenced-artifact fields
    requirement_ids = fields.get("requirement_ids")
    requirements = fields.get("requirements")
    if not isinstance(requirement_ids, list) or not isinstance(requirements, list):
        raise _FieldError("fields['requirement_ids'] and fields['requirements'] must be arrays")
    if not all(isinstance(item, dict) for item in requirements):
        raise _FieldError("fields['requirements'] entries must be objects")
    row_ids = [item.get("id") for item in requirements]
    if list(requirement_ids) != row_ids:
        raise _FieldError("requirement_ids and requirements order/content differ")
    if any(not isinstance(item, str) or not item for item in requirement_ids):
        raise _FieldError("requirement IDs must be non-empty strings")
    return dict(fields)


_KIND_BUILDERS: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {
    "authority": _build_authority,
    "inventory": _build_inventory,
    "disposition_report": _build_disposition_report,
    "plan_association": _build_plan_association,
}


def write_audit_cycle_artifact_sync(
    *,
    kind: str,
    path: str,
    fields: dict[str, Any],
    cwd: str,
) -> dict[str, Any]:
    """Validate, construct, digest, and canonically write one audit-cycle artifact.

    Pure synchronous implementation — no MCP context required. Never raises;
    every failure mode returns a structured ``{"success": False, "error": ...}``
    envelope, and no bytes are written until every structural and containment
    check has passed.
    """
    try:
        builder = _KIND_BUILDERS.get(kind)
        if builder is None:
            return {"success": False, "error": f"unknown kind: {kind!r}"}

        if not cwd or not os.path.isdir(cwd):
            return {"success": False, "error": f"cwd does not exist or is not a directory: {cwd}"}

        from autoskillit.server.git import validate_commit_paths  # circular-break

        if (containment_error := validate_commit_paths(cwd, [path])) is not None:
            return {"success": False, "error": containment_error}

        if not isinstance(fields, dict):
            return {"success": False, "error": "fields must be an object"}

        try:
            final_dict = builder(fields, cwd)
        except _FieldError as exc:
            return {"success": False, "error": str(exc)}
        except (ContainmentError, OSError) as exc:
            return {"success": False, "error": f"referenced artifact read failed: {exc}"}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        try:
            write_canonical_versioned_json(
                Path(path), final_dict, AUDIT_CYCLE_SCHEMA_VERSION, exclusive=True
            )
        except FileExistsError:
            return {"success": False, "error": f"artifact already exists at {path}"}
        except OSError as exc:
            return {"success": False, "error": f"write failed: {type(exc).__name__}: {exc}"}

        content_digest = compute_bytes_hash(Path(path).read_bytes())
        return {"success": True, "path": path, "content_digest": content_digest}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(
    tags={"autoskillit", "kitchen", "kitchen-core", "headless"},
    annotations={"readOnlyHint": True},
)
@_cancellation_shield()
@track_response_size("write_audit_cycle_artifact")
async def write_audit_cycle_artifact(
    kind: str,
    path: str,
    fields: dict[str, Any],
    cwd: str,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Server-side construct, digest, validate, and canonically write one audit-cycle artifact.

    Removes both JSON-canonicalization and digest-computation from the calling
    session's token-generation path for the four hash-bound audit-cycle artifact
    kinds consumed with ``require_canonical=True`` by the audit-cycle verifier:
    ``authority``, ``inventory``, ``disposition_report``, ``plan_association``.

    Args:
        kind: One of "authority", "inventory", "disposition_report", "plan_association".
        path: Absolute destination path for the new artifact — must not already exist.
        fields: Kind-specific semantic fields only — no pre-computed digests, no
            pre-serialized bytes. Every digest is computed server-side.
        cwd: Absolute containment root for both the destination and every
            referenced artifact path inside ``fields``.
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    try:
        with structlog.contextvars.bound_contextvars(tool="write_audit_cycle_artifact", cwd=cwd):
            logger.info("write_audit_cycle_artifact", kind=kind, cwd=cwd)

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            _start = time.monotonic()
            try:
                result = write_audit_cycle_artifact_sync(
                    kind=kind, path=path, fields=fields, cwd=cwd
                )
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
            return json.dumps(result)
    except Exception as exc:
        logger.error("write_audit_cycle_artifact unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
