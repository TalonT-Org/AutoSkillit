"""Adversarial tests for the strict audit-semantic artifact codec."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import autoskillit.core.audit_semantic_codec as audit_semantic_codec
from autoskillit.core.audit_semantic_codec import (
    AuditSemanticCodecError,
    canonical_full_reference_records_match,
    load_audit_semantic_result,
    load_standalone_audit_evidence,
)
from autoskillit.core.closure_hashing import canonical_json_bytes, compute_bytes_hash
from autoskillit.core.path_containment import read_stable_contained_bytes
from autoskillit.core.types._type_audit_admission import (
    STANDALONE_AUDIT_EVIDENCE_KIND,
    AuditSemanticResult,
    StandaloneAuditEvidence,
)
from autoskillit.core.types._type_audit_cycle import (
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditVerdict,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _ref(path: Path, content: bytes, *, media_type: str = "text/markdown") -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type=media_type,
        schema_version=1,
        byte_size=len(content),
        content_digest=compute_bytes_hash(content),
    )


def _semantic_result(tmp_path: Path) -> AuditSemanticResult:
    plan_ref = _ref(tmp_path / "plan.md", b"plan")
    remediation_ref = _ref(tmp_path / "remediation.md", b"remediation")
    assessment = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="Preserve stderr diagnostics",
        assessment=AuditAssessment.MISSING,
        evidence_summary="The planned error path is absent.",
    )
    return AuditSemanticResult(
        schema_version=1,
        audited_plan_refs=(plan_ref,),
        assessments=(assessment,),
        verdict=AuditVerdict.NO_GO,
        remediation_ref=remediation_ref,
    )


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def test_loader_reads_strict_canonical_semantics_with_bounded_reader(
    tmp_path: Path,
) -> None:
    semantic = _semantic_result(tmp_path)
    path = tmp_path / "semantic.json"
    _write_payload(path, semantic.to_dict())
    calls: list[tuple[Path, Path, int]] = []

    def recording_reader(
        candidate: str | Path,
        allowed_root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]:
        calls.append((Path(candidate), Path(allowed_root), max_size_bytes))
        return read_stable_contained_bytes(
            candidate,
            allowed_root,
            max_size_bytes=max_size_bytes,
        )

    loaded = load_audit_semantic_result(
        path,
        tmp_path,
        max_size_bytes=4_096,
        reader=recording_reader,
    )

    assert loaded.to_dict() == semantic.to_dict()
    assert calls == [(path, tmp_path, 4_096)]


def test_loader_default_matches_existing_audit_verifier_byte_limit(tmp_path: Path) -> None:
    semantic = _semantic_result(tmp_path)
    path = tmp_path / "semantic.json"
    _write_payload(path, semantic.to_dict())
    observed_limits: list[int] = []

    def recording_reader(
        candidate: str | Path,
        allowed_root: str | Path,
        *,
        max_size_bytes: int,
    ) -> tuple[Path, bytes]:
        observed_limits.append(max_size_bytes)
        return read_stable_contained_bytes(
            candidate,
            allowed_root,
            max_size_bytes=max_size_bytes,
        )

    load_audit_semantic_result(path, tmp_path, reader=recording_reader)

    assert observed_limits == [10_000_000]


def test_loader_rejects_containment_escape_and_byte_limit(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.json"
    _write_payload(outside, _semantic_result(tmp_path).to_dict())

    with pytest.raises(AuditSemanticCodecError) as escaped:
        load_audit_semantic_result(outside, allowed_root)
    assert escaped.value.reason == "artifact_read_failed"

    inside = allowed_root / "semantic.json"
    _write_payload(inside, _semantic_result(tmp_path).to_dict())
    with pytest.raises(AuditSemanticCodecError) as oversized:
        load_audit_semantic_result(
            inside,
            allowed_root,
            max_size_bytes=inside.stat().st_size - 1,
        )
    assert oversized.value.reason == "artifact_read_failed"


@pytest.mark.parametrize(
    "payload_bytes",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{ "schema_version": 1 }',
        b'{"schema_version":2}',
    ],
)
def test_loader_rejects_noncanonical_or_wrong_version_bytes(
    tmp_path: Path,
    payload_bytes: bytes,
) -> None:
    path = tmp_path / "semantic.json"
    path.write_bytes(payload_bytes)

    with pytest.raises(AuditSemanticCodecError) as caught:
        load_audit_semantic_result(path, tmp_path)

    assert caught.value.reason == "invalid_canonical_json"


@pytest.mark.parametrize(
    ("depth", "field"),
    [
        ("top", "execution_generation"),
        ("top", "gen1"),
        ("plan_ref", "inventory_ref"),
        ("assessment", "cycle_id"),
        ("remediation_ref", "generated_at"),
        ("assessment", "findings_digest"),
        ("plan_ref", "cwd"),
        ("remediation_ref", "handle"),
        ("top", "output_path"),
        ("top", "parent_authority_digest"),
    ],
)
def test_loader_rejects_unknown_authority_fields_at_every_object_depth(
    tmp_path: Path,
    depth: str,
    field: str,
) -> None:
    payload = _semantic_result(tmp_path).to_dict()
    targets = {
        "top": payload,
        "plan_ref": payload["audited_plan_refs"][0],
        "assessment": payload["assessments"][0],
        "remediation_ref": payload["remediation_ref"],
    }
    targets[depth][field] = "attacker-selected"
    path = tmp_path / "semantic.json"
    _write_payload(path, payload)

    with pytest.raises(AuditSemanticCodecError) as caught:
        load_audit_semantic_result(path, tmp_path)

    assert caught.value.reason == "forbidden_identity_field"
    assert field in str(caught.value)


def test_loader_rejects_invalid_exact_schema_with_stable_reason(tmp_path: Path) -> None:
    payload = _semantic_result(tmp_path).to_dict()
    del payload["verdict"]
    path = tmp_path / "semantic.json"
    _write_payload(path, payload)

    with pytest.raises(AuditSemanticCodecError) as caught:
        load_audit_semantic_result(path, tmp_path)

    assert caught.value.reason == "invalid_semantic_schema"


@pytest.mark.parametrize(
    ("container", "scalar_field", "identity_field"),
    [
        ("assessments", "evidence_summary", "execution_generation"),
        ("audited_plan_refs", "locator", "gen1"),
    ],
)
def test_loader_rejects_identity_maps_nested_where_scalars_belong(
    tmp_path: Path,
    container: str,
    scalar_field: str,
    identity_field: str,
) -> None:
    payload = _semantic_result(tmp_path).to_dict()
    payload[container][0][scalar_field] = {identity_field: "attacker-selected"}
    path = tmp_path / "semantic.json"
    _write_payload(path, payload)

    with pytest.raises(AuditSemanticCodecError) as caught:
        load_audit_semantic_result(path, tmp_path)

    assert caught.value.reason == "forbidden_identity_field"
    assert identity_field in str(caught.value)


def test_full_reference_comparison_checks_order_and_non_digest_fields(
    tmp_path: Path,
) -> None:
    first = _ref(tmp_path / "first.md", b"first")
    second = _ref(tmp_path / "second.md", b"second")
    relocated = replace(first, locator=str(tmp_path / "relocated.md"))

    assert first == relocated  # ArtifactRef equality is intentionally digest-only.
    assert canonical_full_reference_records_match((first, second), (first, second))
    assert not canonical_full_reference_records_match((first,), (relocated,))
    assert not canonical_full_reference_records_match((first, second), (second, first))


def test_standalone_loader_requires_exact_kind_and_canonical_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _semantic_result(tmp_path)
    evidence = StandaloneAuditEvidence(
        schema_version=1,
        kind=STANDALONE_AUDIT_EVIDENCE_KIND,
        audited_plan_refs=semantic.audited_plan_refs,
        assessments=semantic.assessments,
        verdict=semantic.verdict,
        remediation_ref=semantic.remediation_ref,
    )
    path = tmp_path / "standalone.json"
    _write_payload(path, evidence.to_dict())
    monkeypatch.setattr(audit_semantic_codec, "AUDIT_SEMANTIC_SCHEMA_VERSION", 2)

    loaded = load_standalone_audit_evidence(path, tmp_path)

    assert loaded == evidence

    tampered = evidence.to_dict()
    tampered["kind"] = "audit_cycle_authority"
    _write_payload(path, tampered)
    with pytest.raises(AuditSemanticCodecError, match="invalid kind"):
        load_standalone_audit_evidence(path, tmp_path)
