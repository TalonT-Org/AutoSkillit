"""Adversarial containment, integrity, replay, and lineage tests."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    AdmissionReason,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerificationError,
    AuditCycleVerifier,
    AuditVerdict,
)
from autoskillit.core.closure_hashing import compute_bytes_hash
from autoskillit.core.path_containment import (
    ContainmentError,
    read_stable_contained_bytes,
    resolve_contained_path,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_HASH = "sha256:" + "a" * 64


def _ref(path: Path, content: bytes, *, schema_version: int = 1) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=schema_version,
        byte_size=len(content),
        content_digest=compute_bytes_hash(content),
    )


def _authority(
    root: Path,
    *,
    parent: str | None = None,
    round_: int = 1,
    plan_content: bytes = b"plan",
    inventory_content: bytes = b"inventory",
) -> AuditCycleAuthority:
    plan = _ref(root / "plan.md", plan_content)
    inventory = _ref(root / "inventory.json", inventory_content)
    remediation = _ref(root / "remediation.md", b"remediation")
    row = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="requirement",
        assessment=AuditAssessment.MISSING,
        evidence_summary="missing",
    )
    return AuditCycleAuthority.create(
        execution_generation="generation-1",
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=round_,
        parent_authority_digest=parent,
        audited_plan_refs=(plan,),
        inventory_ref=inventory,
        assessments=(row,),
        verdict=AuditVerdict.NO_GO,
        remediation_ref=remediation,
        generated_at=f"2026-07-23T00:0{round_}:00Z",
    )


@pytest.mark.parametrize("attack", ["relative", "symlink", "hardlink", "world", "directory"])
def test_containment_rejects_hostile_artifacts(tmp_path: Path, attack: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.json"
    target.write_bytes(b"{}")
    candidate: Path
    if attack == "relative":
        candidate = Path("target.json")
    elif attack == "symlink":
        candidate = root / "link.json"
        candidate.symlink_to(target)
    elif attack == "hardlink":
        candidate = root / "hard.json"
        os.link(target, candidate)
    elif attack == "world":
        candidate = target
        candidate.chmod(0o666)
    else:
        candidate = root
    with pytest.raises((ContainmentError, ValueError, FileNotFoundError)):
        resolve_contained_path(candidate, root)


def test_containment_rejects_oversized_buffer(tmp_path: Path) -> None:
    artifact = tmp_path / "large.bin"
    artifact.write_bytes(b"x" * 5)
    with pytest.raises(ContainmentError, match="large"):
        read_stable_contained_bytes(artifact, tmp_path, max_size_bytes=4)


def test_artifact_reference_rejects_size_digest_and_post_reference_mutation(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b'{"schema_version":1}')
    ref = _ref(artifact, artifact.read_bytes())
    verifier = AuditCycleVerifier(tmp_path)
    assert verifier.verify_artifact_ref(ref) == b'{"schema_version":1}'
    with pytest.raises(AuditCycleVerificationError, match="size"):
        verifier.verify_artifact_ref(replace(ref, byte_size=ref.byte_size + 1))
    artifact.write_bytes(b'{"schema_version":2}')
    with pytest.raises(AuditCycleVerificationError, match="digest"):
        verifier.verify_artifact_ref(ref)


def test_authority_rejects_forged_digest_and_noncanonical_bytes(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    path = tmp_path / "authority.json"
    path.write_bytes(authority.canonical_bytes)
    verifier = AuditCycleVerifier(tmp_path)
    assert verifier.load_authority(path) == authority
    forged = authority.to_dict()
    forged["cycle_id"] = "forged"
    import json

    path.write_text(json.dumps(forged, indent=2), encoding="utf-8")
    with pytest.raises(AuditCycleVerificationError, match="canonical"):
        verifier.load_authority(path)


def test_duplicate_keys_float_and_nan_are_rejected_before_authority_parsing(
    tmp_path: Path,
) -> None:
    verifier = AuditCycleVerifier(tmp_path)
    path = tmp_path / "authority.json"
    for payload in (
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"value":1.5}',
        b'{"schema_version":1,"value":NaN}',
    ):
        path.write_bytes(payload)
        with pytest.raises(AuditCycleVerificationError, match="canonical"):
            verifier.load_authority(path)


def test_successor_must_descend_from_trusted_current_head(tmp_path: Path) -> None:
    first = _authority(tmp_path)
    head = AuditCycleHead(
        execution_generation=first.execution_generation,
        cycle_id=first.cycle_id,
        plan_set_id=first.plan_set_id,
        scope_id=first.scope_id,
        part_id=first.part_id,
        current_authority_digest=first.authority_digest,
        audit_round=first.audit_round,
        audited_plan_refs=first.audited_plan_refs,
        inventory_ref=first.inventory_ref,
        verdict=first.verdict,
    )
    successor = _authority(tmp_path, parent=first.authority_digest, round_=2)
    AuditCycleVerifier.verify_successor(successor, head)
    forged_parent = _authority(tmp_path, parent=_HASH, round_=2)
    with pytest.raises(AuditCycleVerificationError) as caught:
        AuditCycleVerifier.verify_successor(forged_parent, head)
    assert caught.value.reason is AdmissionReason.PARENT_MISMATCH


@pytest.mark.parametrize(
    "replacement,reason",
    [
        ({"inventory_content": b"replacement-inventory"}, AdmissionReason.INVENTORY_MISMATCH),
        ({"plan_content": b"replacement-plan"}, AdmissionReason.PLAN_MISMATCH),
    ],
)
def test_successor_preserves_trusted_artifact_lineage(
    tmp_path: Path,
    replacement: dict[str, bytes],
    reason: AdmissionReason,
) -> None:
    first = _authority(tmp_path)
    head = AuditCycleHead(
        execution_generation=first.execution_generation,
        cycle_id=first.cycle_id,
        plan_set_id=first.plan_set_id,
        scope_id=first.scope_id,
        part_id=first.part_id,
        current_authority_digest=first.authority_digest,
        audit_round=first.audit_round,
        audited_plan_refs=first.audited_plan_refs,
        inventory_ref=first.inventory_ref,
        verdict=first.verdict,
    )
    successor = _authority(
        tmp_path,
        parent=first.authority_digest,
        round_=2,
        **replacement,
    )

    with pytest.raises(AuditCycleVerificationError) as caught:
        AuditCycleVerifier.verify_successor(successor, head)

    assert caught.value.reason is reason


def test_stale_authority_replay_is_rejected_before_inventory_read(tmp_path: Path) -> None:
    stale = _authority(tmp_path)
    current = _authority(tmp_path, parent=stale.authority_digest, round_=2)
    stale_path = tmp_path / "stale.json"
    stale_path.write_bytes(stale.canonical_bytes)
    reads: list[Path] = []

    def recording_reader(
        path: str | Path,
        root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]:
        reads.append(Path(path))
        return read_stable_contained_bytes(path, root, max_size_bytes=max_size_bytes)

    verifier = AuditCycleVerifier(tmp_path, reader=recording_reader)
    head = AuditCycleHead(
        execution_generation=current.execution_generation,
        cycle_id=current.cycle_id,
        plan_set_id=current.plan_set_id,
        scope_id=current.scope_id,
        part_id=current.part_id,
        current_authority_digest=current.authority_digest,
        audit_round=current.audit_round,
        audited_plan_refs=current.audited_plan_refs,
        inventory_ref=current.inventory_ref,
        verdict=current.verdict,
    )
    decision = verifier.evaluate_paths(
        authority_path=stale_path,
        report_path=None,
        trusted_head=head,
        current_plan_path=tmp_path / "plan.md",
        expected_generation=current.execution_generation,
        expected_plan_set_id=current.plan_set_id,
        expected_scope_id=current.scope_id,
        expected_part_id=current.part_id,
    )
    assert decision.reason is AdmissionReason.AUTHORITY_NOT_CURRENT
    assert reads == [stale_path]
