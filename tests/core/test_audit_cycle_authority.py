"""Typed and canonical contracts for audit-cycle authority artifacts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from autoskillit.core import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditVerdict,
    PlanDispositionReport,
    PlanDispositionRow,
)
from autoskillit.core.closure_hashing import (
    canonical_json_bytes,
    parse_canonical_json_bytes,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64
_HASH_C = "sha256:" + "c" * 64


def _ref(path: Path, digest: str = _HASH_A) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=1,
        byte_size=17,
        content_digest=digest,
    )


def _row(
    requirement_id: str = "REQ-001",
    assessment: AuditAssessment = AuditAssessment.COVERED,
) -> AuditAssessmentRow:
    return AuditAssessmentRow.create(
        requirement_id=requirement_id,
        requirement_text=f"text for {requirement_id}",
        assessment=assessment,
        evidence_summary=f"evidence for {requirement_id}",
    )


def _authority(
    tmp_path: Path,
    *,
    verdict: AuditVerdict = AuditVerdict.GO,
    assessment: AuditAssessment = AuditAssessment.COVERED,
) -> AuditCycleAuthority:
    remediation = (
        _ref(tmp_path / "remediation.md", _HASH_C) if verdict is AuditVerdict.NO_GO else None
    )
    return AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=2,
        parent_authority_digest=_HASH_B,
        audited_plan_refs=(_ref(tmp_path / "plan.md"),),
        inventory_ref=_ref(tmp_path / "inventory.json", _HASH_B),
        assessments=(_row(assessment=assessment),),
        verdict=verdict,
        remediation_ref=remediation,
        generated_at="2026-07-23T00:00:00Z",
    )


def test_artifact_ref_uses_content_digest_as_identity(tmp_path: Path) -> None:
    first = _ref(tmp_path / "first.json")
    second = ArtifactRef(
        locator=str(tmp_path / "second.json"),
        media_type="text/plain",
        schema_version=9,
        byte_size=999,
        content_digest=first.content_digest,
    )
    assert first == second
    assert hash(first) == hash(second)
    assert first.metadata_digest != second.metadata_digest
    with pytest.raises(ValueError, match="absolute"):
        replace(first, locator="../inventory.json")


def test_canonical_json_profile_is_compact_sorted_and_order_sensitive() -> None:
    first = canonical_json_bytes({"z": ["b", "a"], "a": {"b": 2, "a": 1}})
    second = canonical_json_bytes({"a": {"a": 1, "b": 2}, "z": ["b", "a"]})
    reordered = canonical_json_bytes({"z": ["a", "b"], "a": {"b": 2, "a": 1}})
    assert first == b'{"a":{"a":1,"b":2},"z":["b","a"]}'
    assert first == second
    assert first != reordered
    assert parse_canonical_json_bytes(first) == {
        "a": {"a": 1, "b": 2},
        "z": ["b", "a"],
    }


@pytest.mark.parametrize(
    "payload",
    [
        b'{"a":1,"a":2}',
        b'{"a":1.5}',
        b'{"a":NaN}',
        b'{ "a":1}',
        b'{"b":2,"a":1}',
        b'{"a":"\\ud800"}',
    ],
)
def test_canonical_json_profile_rejects_noncanonical_payloads(payload: bytes) -> None:
    with pytest.raises(ValueError):
        parse_canonical_json_bytes(payload)


def test_authority_and_rows_are_frozen_and_digest_bound(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    with pytest.raises(FrozenInstanceError):
        authority.cycle_id = "forged"  # type: ignore[misc]
    with pytest.raises(ValueError, match="row content"):
        replace(authority.assessments[0], evidence_summary="forged")
    with pytest.raises(ValueError, match="event content"):
        replace(authority, cycle_id="forged")


def test_authority_normalizes_mutable_typed_collections(tmp_path: Path) -> None:
    audited_plan_refs = [_ref(tmp_path / "plan.md")]
    assessments = [_row()]
    authority = AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=2,
        parent_authority_digest=_HASH_B,
        audited_plan_refs=audited_plan_refs,  # type: ignore[arg-type]
        inventory_ref=_ref(tmp_path / "inventory.json", _HASH_B),
        assessments=assessments,  # type: ignore[arg-type]
        verdict=AuditVerdict.GO,
        remediation_ref=None,
        generated_at="2026-07-23T00:00:00Z",
    )

    audited_plan_refs.append(_ref(tmp_path / "other-plan.md", _HASH_C))
    assessments.append(_row("REQ-002"))

    assert isinstance(authority.audited_plan_refs, tuple)
    assert isinstance(authority.assessments, tuple)
    assert len(authority.audited_plan_refs) == 1
    assert len(authority.assessments) == 1
    assert authority.authority_digest == authority.compute_digest()


@pytest.mark.parametrize(
    ("field", "value", "expected_type"),
    [
        ("audited_plan_refs", [object()], "ArtifactRef"),
        ("assessments", [object()], "AuditAssessmentRow"),
    ],
)
def test_authority_rejects_invalid_collection_elements(
    tmp_path: Path,
    field: str,
    value: list[object],
    expected_type: str,
) -> None:
    with pytest.raises(ValueError, match=expected_type):
        replace(_authority(tmp_path), **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_generation", "generation-2"),
        ("cycle_id", "cycle-2"),
        ("plan_set_id", "plans-2"),
        ("scope_id", "scope-2"),
        ("part_id", "part-b"),
        ("audit_round", 3),
        ("parent_authority_digest", _HASH_C),
    ],
)
def test_authority_digest_covers_cycle_lineage(tmp_path: Path, field: str, value: object) -> None:
    base = _authority(tmp_path)
    kwargs = {
        "execution_generation": base.execution_generation,
        "cycle_id": base.cycle_id,
        "plan_set_id": base.plan_set_id,
        "scope_id": base.scope_id,
        "part_id": base.part_id,
        "audit_round": base.audit_round,
        "parent_authority_digest": base.parent_authority_digest,
        "audited_plan_refs": base.audited_plan_refs,
        "inventory_ref": base.inventory_ref,
        "assessments": base.assessments,
        "verdict": base.verdict,
        "remediation_ref": base.remediation_ref,
        "generated_at": base.generated_at,
    }
    kwargs[field] = value
    changed = AuditCycleAuthority.create(**kwargs)  # type: ignore[arg-type]
    assert changed.authority_digest != base.authority_digest


def test_authority_digest_covers_plan_inventory_and_findings(tmp_path: Path) -> None:
    base = _authority(tmp_path)
    changed_plan = AuditCycleAuthority.create(
        execution_generation=base.execution_generation,
        cycle_id=base.cycle_id,
        plan_set_id=base.plan_set_id,
        scope_id=base.scope_id,
        part_id=base.part_id,
        audit_round=base.audit_round,
        parent_authority_digest=base.parent_authority_digest,
        audited_plan_refs=(_ref(tmp_path / "other-plan.md", _HASH_C),),
        inventory_ref=base.inventory_ref,
        assessments=base.assessments,
        verdict=base.verdict,
        remediation_ref=base.remediation_ref,
        generated_at=base.generated_at,
    )
    changed_inventory = replace(
        base.inventory_ref,
        content_digest=_HASH_C,
    )
    changed_inventory_authority = AuditCycleAuthority.create(
        execution_generation=base.execution_generation,
        cycle_id=base.cycle_id,
        plan_set_id=base.plan_set_id,
        scope_id=base.scope_id,
        part_id=base.part_id,
        audit_round=base.audit_round,
        parent_authority_digest=base.parent_authority_digest,
        audited_plan_refs=base.audited_plan_refs,
        inventory_ref=changed_inventory,
        assessments=base.assessments,
        verdict=base.verdict,
        remediation_ref=base.remediation_ref,
        generated_at=base.generated_at,
    )
    changed_findings = _authority(
        tmp_path,
        verdict=AuditVerdict.NO_GO,
        assessment=AuditAssessment.MISSING,
    )
    assert changed_plan.authority_digest != base.authority_digest
    assert changed_inventory_authority.authority_digest != base.authority_digest
    assert changed_findings.findings_digest != base.findings_digest
    assert changed_findings.authority_digest != base.authority_digest


def test_go_and_no_go_remediation_invariants(tmp_path: Path) -> None:
    go = _authority(tmp_path)
    no_go = _authority(
        tmp_path,
        verdict=AuditVerdict.NO_GO,
        assessment=AuditAssessment.MISSING,
    )
    with pytest.raises(ValueError, match="GO authority cannot"):
        replace(go, remediation_ref=_ref(tmp_path / "active.md"))
    with pytest.raises(ValueError, match="requires remediation"):
        replace(no_go, remediation_ref=None)
    with pytest.raises(ValueError, match="blocking"):
        replace(no_go, verdict=AuditVerdict.GO, remediation_ref=None)


def test_plan_disposition_report_is_bound_to_full_identity(tmp_path: Path) -> None:
    authority = _authority(
        tmp_path,
        verdict=AuditVerdict.NO_GO,
        assessment=AuditAssessment.MISSING,
    )
    disposition = PlanDispositionRow.create(
        requirement_id="REQ-001",
        disposition="carried@step",
        implementation_step="Step 2.1",
    )
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
        current_plan_ref=_ref(tmp_path / "current-plan.md", _HASH_C),
        dispositions=(disposition,),
        generated_at="2026-07-23T00:01:00Z",
    )
    assert PlanDispositionReport.from_dict(report.to_dict()) == report
    with pytest.raises(ValueError, match="report content"):
        replace(report, cycle_id="forged-cycle")
    mutable_dispositions = [disposition]
    normalized = PlanDispositionReport.create(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        audit_round=authority.audit_round,
        parent_authority_digest=authority.authority_digest,
        inventory_digest=authority.inventory_ref.content_digest,
        findings_digest=authority.findings_digest,
        current_plan_ref=_ref(tmp_path / "other-current-plan.md", _HASH_C),
        dispositions=mutable_dispositions,  # type: ignore[arg-type]
        generated_at="2026-07-23T00:01:00Z",
    )
    mutable_dispositions.append(
        PlanDispositionRow.create(
            requirement_id="REQ-002",
            disposition="satisfied-by-round-1",
        )
    )
    assert isinstance(normalized.dispositions, tuple)
    assert len(normalized.dispositions) == 1
    assert normalized.report_digest == normalized.compute_digest()
    with pytest.raises(ValueError, match="PlanDispositionRow"):
        replace(normalized, dispositions=[object()])


def test_head_allows_successor_only_for_go(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only a GO"):
        AuditCycleHead(
            execution_generation="generation-1",
            cycle_id="cycle-1",
            plan_set_id="plans-1",
            scope_id="scope-1",
            part_id="part-a",
            current_authority_digest=_HASH_A,
            audit_round=1,
            audited_plan_refs=(_ref(tmp_path / "plan.md"),),
            inventory_ref=_ref(tmp_path / "inventory.json", _HASH_B),
            verdict=AuditVerdict.NO_GO,
            authorized_successor_part_id="part-b",
        )


def test_schema_version_is_public() -> None:
    assert AUDIT_CYCLE_SCHEMA_VERSION == 1
