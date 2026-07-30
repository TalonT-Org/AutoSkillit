"""Typed audit artifact producer tests."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import (
    AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY,
    ArtifactRef,
    AuditArtifactFieldOwnership,
    AuditAssessment,
    AuditVerdict,
    compute_bytes_hash,
    load_audit_semantic_result,
    load_standalone_audit_evidence,
)
from autoskillit.server.tools.tools_audit_artifacts import (
    _build_semantic_result,
    _write_semantic_result,
    write_audit_disposition_bundle,
    write_audit_semantic_result,
    write_standalone_audit_evidence,
    write_standalone_audit_evidence_sync,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _semantic_args(tmp_path: Path) -> dict[str, Any]:
    plan_path = tmp_path / "plan.md"
    remediation_path = tmp_path / "remediation.md"
    plan_bytes = b"plan"
    remediation_bytes = b"remediation"
    plan_path.write_bytes(plan_bytes)
    remediation_path.write_bytes(remediation_bytes)
    return {
        "audited_plan_refs": [
            ArtifactRef(
                locator=str(plan_path),
                media_type="text/markdown",
                schema_version=1,
                byte_size=len(plan_bytes),
                content_digest=compute_bytes_hash(plan_bytes),
            ).to_dict()
        ],
        "assessments": [
            {
                "requirement_id": "REQ-001",
                "requirement_text": "Preserve diagnostics",
                "assessment": AuditAssessment.COVERED.value,
                "evidence_summary": "The plan preserves the error boundary.",
            }
        ],
        "verdict": AuditVerdict.NO_GO.value,
        "remediation_ref": ArtifactRef(
            locator=str(remediation_path),
            media_type="text/markdown",
            schema_version=1,
            byte_size=len(remediation_bytes),
            content_digest=compute_bytes_hash(remediation_bytes),
        ).to_dict(),
    }


def test_private_semantic_writer_round_trips_through_strict_loader(
    tmp_path: Path,
) -> None:
    semantic = _build_semantic_result(**_semantic_args(tmp_path))
    path = tmp_path / "semantic.json"

    _write_semantic_result(path, semantic)

    assert load_audit_semantic_result(path, tmp_path) == semantic


def test_standalone_writer_is_deterministic_and_non_authoritative(
    tmp_path: Path,
) -> None:
    args = _semantic_args(tmp_path)

    first = write_standalone_audit_evidence_sync(temp_root=tmp_path, **args)
    second = write_standalone_audit_evidence_sync(temp_root=tmp_path, **args)

    assert first == second
    assert first["success"] is True
    assert first["audit_status"] == "NON_PUBLISHED_STANDALONE"
    assert "audit_cycle_path" not in first
    path = Path(first["standalone_evidence_path"])
    evidence = load_standalone_audit_evidence(path, tmp_path)
    assert evidence.verdict is AuditVerdict.NO_GO


def test_standalone_writer_never_raises_for_malformed_semantics(
    tmp_path: Path,
) -> None:
    result = write_standalone_audit_evidence_sync(
        temp_root=tmp_path,
        audited_plan_refs=[],
        assessments=[],
        verdict="attacker-selected",
        remediation_ref=None,
    )

    assert result["success"] is False
    assert "error" in result


def test_typed_handler_signatures_exclude_identity_path_and_cwd() -> None:
    semantic_params = set(inspect.signature(write_audit_semantic_result).parameters)
    standalone_params = set(inspect.signature(write_standalone_audit_evidence).parameters)
    disposition_params = set(inspect.signature(write_audit_disposition_bundle).parameters)
    child_semantic_fields = {
        definition.field_name
        for (kind, _), definition in AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY.items()
        if kind == "semantic_result"
        and definition.ownership is AuditArtifactFieldOwnership.CHILD_SEMANTIC
    }
    framework_params = {"ctx", "step_name"}

    assert {"execution_generation", "cycle_id", "generated_at", "cwd", "path"}.isdisjoint(
        semantic_params
    )
    assert {"execution_generation", "generated_at", "cwd", "path"}.isdisjoint(disposition_params)
    assert semantic_params - framework_params - {"reservation_handle"} == child_semantic_fields
    assert standalone_params - framework_params == child_semantic_fields
    assert disposition_params - framework_params == {
        "authority_path",
        "new_plan_path",
        "new_plan_media_type",
        "new_plan_schema_version",
        "dispositions",
    }


@pytest.mark.anyio
async def test_admission_handlers_fail_closed_for_invalid_requests_without_raising() -> None:
    semantic = json.loads(
        await write_audit_semantic_result(
            reservation_handle="",
            audited_plan_refs=[],
            assessments=[],
            verdict="GO",
        )
    )
    disposition = json.loads(
        await write_audit_disposition_bundle(
            authority_path="",
            new_plan_path="",
            new_plan_media_type="",
            new_plan_schema_version=1,
            dispositions=[],
        )
    )

    assert semantic["success"] is False
    assert disposition["success"] is False
