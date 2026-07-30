"""Audit-admission ownership and value-contract tests."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from autoskillit.core.types._type_audit_admission import (
    AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY,
    AUDIT_REFERENCE_IDENTITY_PROFILE_V1,
    AUDIT_SEMANTIC_SCHEMA_VERSION,
    STANDALONE_AUDIT_EVIDENCE_KIND,
    STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
    AuditArtifactFieldOwnership,
    AuditAttemptId,
    AuditAttemptLifecycle,
    AuditAttemptRecord,
    AuditIdentityReservation,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPreparedEffect,
    AuditPreparedEffectDeliveryStatus,
    AuditRound,
    AuditSemanticResult,
    AuditSlotId,
    AuditSlotKey,
    InstallationVersion,
    RecipeExecutionId,
    ReservationDecision,
    StandaloneAuditEvidence,
    compute_audit_reference_identity,
)
from autoskillit.core.types._type_audit_cycle import (
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditVerdict,
    PlanDispositionReport,
)
from autoskillit.core.types._type_audit_protocols import (
    AuditAuthorityMaterializer,
    CommittedDispositionResolver,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _bytes_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _reference(
    name: str,
    *,
    media_type: str = "text/markdown",
    schema_version: int = 1,
    byte_size: int = 10,
    content_digest: str = _digest("a"),
) -> ArtifactRef:
    return ArtifactRef(
        locator=f"/tmp/audit/{name}",
        media_type=media_type,
        schema_version=schema_version,
        byte_size=byte_size,
        content_digest=content_digest,
    )


def _covered_row(requirement_id: str = "REQ-001") -> AuditAssessmentRow:
    return AuditAssessmentRow.create(
        requirement_id=requirement_id,
        requirement_text="The implementation preserves the authority boundary.",
        assessment=AuditAssessment.COVERED,
        evidence_summary="Covered by focused contract tests.",
    )


def _semantic_result(
    refs: tuple[ArtifactRef, ...] | None = None,
) -> AuditSemanticResult:
    return AuditSemanticResult(
        schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
        audited_plan_refs=refs or (_reference("plan.md"),),
        assessments=(_covered_row(),),
        verdict=AuditVerdict.GO,
        remediation_ref=None,
    )


def _slot_key(refs: tuple[ArtifactRef, ...]) -> AuditSlotKey:
    return AuditSlotKey(
        recipe_execution_id=RecipeExecutionId("execution-1"),
        installation_version=InstallationVersion("install-1"),
        step_name="audit_impl",
        invocation_template_digest=_digest("b"),
        slot_intent_digest=_digest("c"),
        ordered_reference_identity=compute_audit_reference_identity(refs),
        prior_authority_digest=None,
    )


def _reservation() -> AuditIdentityReservation:
    refs = (_reference("plan.md"),)
    reference_identity = compute_audit_reference_identity(refs)
    return AuditIdentityReservation(
        slot_id=AuditSlotId("slot-1"),
        slot_key=_slot_key(refs),
        current_attempt_id=AuditAttemptId("attempt-1"),
        runtime_binding_digest=_digest("d"),
        reference_identity_profile_id=(AUDIT_REFERENCE_IDENTITY_PROFILE_V1.profile_id),
        audited_plan_refs=refs,
        plan_set_id=reference_identity,
        cycle_id="cycle-1",
        scope_id="scope-1",
        part_id="part-1",
        audit_round=AuditRound(1),
        parent_authority_digest=None,
        generated_at="2026-07-29T20:00:00Z",
        allowed_root=Path("/tmp/audit"),
        semantic_result_path=Path("/tmp/audit/semantic.json"),
        inventory_path=Path("/tmp/audit/inventory.json"),
        authority_path=Path("/tmp/audit/authority.json"),
        expected_head=None,
    )


def _prepared_effect(
    status: AuditPreparedEffectDeliveryStatus = (AuditPreparedEffectDeliveryStatus.PENDING),
) -> AuditPreparedEffect:
    payload = b'{"schema_version":1}'
    return AuditPreparedEffect(
        artifact_kind="authority",
        canonical_bytes=payload,
        content_digest=_bytes_digest(payload),
        path=Path("/tmp/audit/authority.json"),
        delivery_status=status,
        canonicalization_profile="canonical-json-v1",
        semantic_fingerprint=_digest("e"),
    )


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (
            AuditArtifactFieldOwnership,
            {
                "CHILD_SEMANTIC",
                "SERVER_INJECTED",
                "SERVER_DERIVED",
                "VERIFIED_COPY",
            },
        ),
        (
            AuditMaterializationStatus,
            {
                "PUBLISHED_PENDING_FINALIZATION",
                "EXACT_REPLAY",
                "SEMANTIC_REJECTED",
                "CONFLICT",
                "STORAGE_FAILURE",
                "QUARANTINED",
                "NON_PUBLISHED_STANDALONE",
            },
        ),
        (
            AuditOutcomeStatus,
            {
                "PUBLISHED",
                "EXACT_REPLAY",
                "SEMANTIC_REJECTED",
                "CONFLICT",
                "STORAGE_FAILURE",
                "QUARANTINED",
                "NON_PUBLISHED_STANDALONE",
            },
        ),
        (
            ReservationDecision,
            {
                "DISPATCH_NEW",
                "REDISPATCH_OPEN",
                "RESUME_PREPARED",
                "PUBLISHED_PENDING_FINALIZATION",
                "EXACT_REPLAY",
                "CONFLICT",
            },
        ),
        (
            AuditAttemptLifecycle,
            {
                "OPEN",
                "SEMANTIC_ACCEPTED",
                "SEMANTIC_REJECTED",
                "PREPARED",
                "PUBLISHED_PENDING_FINALIZATION",
                "RESPONSE_COMMITTED",
                "CONFLICT",
                "QUARANTINED",
            },
        ),
        (
            AuditPreparedEffectDeliveryStatus,
            {"PENDING", "DELIVERED", "QUARANTINED"},
        ),
    ],
)
def test_status_enums_are_closed(
    enum_type: type[
        AuditArtifactFieldOwnership
        | AuditMaterializationStatus
        | AuditOutcomeStatus
        | ReservationDecision
        | AuditAttemptLifecycle
        | AuditPreparedEffectDeliveryStatus
    ],
    expected: set[str],
) -> None:
    assert {member.name for member in enum_type} == expected
    assert {member.value for member in enum_type} == expected


def test_authority_field_ownership_registry_is_exact_and_immutable() -> None:
    expected = {
        AuditArtifactFieldOwnership.CHILD_SEMANTIC: {
            "assessments",
            "verdict",
            "remediation_ref",
        },
        AuditArtifactFieldOwnership.SERVER_INJECTED: {
            "execution_generation",
            "cycle_id",
            "scope_id",
            "part_id",
            "audit_round",
            "generated_at",
        },
        AuditArtifactFieldOwnership.SERVER_DERIVED: {
            "schema_version",
            "plan_set_id",
            "inventory_ref",
            "findings_digest",
            "authority_digest",
        },
        AuditArtifactFieldOwnership.VERIFIED_COPY: {
            "parent_authority_digest",
            "audited_plan_refs",
        },
    }
    actual = {
        ownership: {
            definition.field_name
            for (kind, _), definition in AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY.items()
            if kind == "authority" and definition.ownership is ownership
        }
        for ownership in AuditArtifactFieldOwnership
    }
    assert actual == expected
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("semantic_result", "audited_plan_refs")].ownership
        is AuditArtifactFieldOwnership.CHILD_SEMANTIC
    )
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("authority", "audited_plan_refs")].ownership
        is AuditArtifactFieldOwnership.VERIFIED_COPY
    )
    with pytest.raises(TypeError):
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("authority", "new")] = (  # type: ignore[index,assignment]
            AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("authority", "schema_version")]
        )


def _assert_ownership_registry_covers_wire_schemas(registry) -> None:
    artifact_fields = {
        "semantic_result": {field.name for field in dataclasses.fields(AuditSemanticResult)},
        "standalone_evidence": {
            field.name for field in dataclasses.fields(StandaloneAuditEvidence)
        },
        "authority": {field.name for field in dataclasses.fields(AuditCycleAuthority)},
        "disposition_report": {field.name for field in dataclasses.fields(PlanDispositionReport)},
        "plan_association": {
            "schema_version",
            "plan_ref",
            "disposition_ref",
            "parent_authority_digest",
            "association_digest",
        },
    }
    expected_keys = {
        (artifact_kind, field_name)
        for artifact_kind, fields in artifact_fields.items()
        for field_name in fields
    }
    assert set(registry) == expected_keys
    for key, definition in registry.items():
        assert key == (definition.artifact_kind, definition.field_name)


def test_field_ownership_registry_covers_every_wire_field_exactly() -> None:
    _assert_ownership_registry_covers_wire_schemas(AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY)
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("semantic_result", "schema_version")].ownership
        is AuditArtifactFieldOwnership.SERVER_DERIVED
    )
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[("disposition_report", "generated_at")].ownership
        is AuditArtifactFieldOwnership.SERVER_INJECTED
    )
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[
            ("disposition_report", "execution_generation")
        ].ownership
        is AuditArtifactFieldOwnership.VERIFIED_COPY
    )
    assert (
        AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[
            ("plan_association", "association_digest")
        ].ownership
        is AuditArtifactFieldOwnership.SERVER_DERIVED
    )


def test_field_ownership_registry_meta_guard_rejects_synthetic_forbidden_field() -> None:
    mutated = dict(AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY)
    mutated[("semantic_result", "execution_generation")] = AUDIT_ARTIFACT_FIELD_OWNERSHIP_REGISTRY[
        ("authority", "execution_generation")
    ]

    with pytest.raises(AssertionError):
        _assert_ownership_registry_covers_wire_schemas(mutated)


def test_ordered_full_reference_identity_covers_all_metadata() -> None:
    first = _reference("one.md", content_digest=_digest("1"))
    second = _reference("two.md", content_digest=_digest("2"))
    baseline = compute_audit_reference_identity((first, second))

    assert baseline != compute_audit_reference_identity((second, first))
    for changed in (
        _reference("renamed.md", content_digest=_digest("1")),
        _reference("one.md", media_type="application/json", content_digest=_digest("1")),
        _reference("one.md", schema_version=2, content_digest=_digest("1")),
        _reference("one.md", byte_size=11, content_digest=_digest("1")),
        _reference("one.md", content_digest=_digest("3")),
    ):
        assert baseline != compute_audit_reference_identity((changed, second))


def test_reference_identity_profile_encodes_strict_reader_policy() -> None:
    profile = AUDIT_REFERENCE_IDENTITY_PROFILE_V1

    assert profile.canonical_json_required is True
    assert profile.plan_set_domain == "audit-plan-set-v1"
    assert profile.digest_algorithm_allowlist == ("sha256",)
    assert profile.verifier_byte_limit == 10_000_000
    assert profile.resolved_absolute_contained_paths_required is True
    assert profile.uri_locators_allowed is False
    assert profile.traversal_allowed is False
    assert profile.symlinks_allowed is False
    assert profile.hardlinks_allowed is False
    assert profile.world_writable_allowed is False
    assert profile.reject_duplicate_canonical_records is True
    assert profile.reject_duplicate_resolved_locators is True
    assert profile.ordered_sequence is True


def test_reference_identity_rejects_noncanonical_or_ambiguous_sequences() -> None:
    reference = _reference("one.md")

    with pytest.raises(ValueError, match="non-empty"):
        compute_audit_reference_identity(())
    with pytest.raises(ValueError, match="duplicate canonical records"):
        compute_audit_reference_identity((reference, reference))
    with pytest.raises(ValueError, match="duplicate resolved locators"):
        compute_audit_reference_identity(
            (
                reference,
                _reference("one.md", content_digest=_digest("2")),
            )
        )
    with pytest.raises(ValueError, match="absolute, normalized, and non-URI"):
        compute_audit_reference_identity(
            (
                ArtifactRef(
                    locator="/tmp/audit//one.md",
                    media_type="text/markdown",
                    schema_version=1,
                    byte_size=10,
                    content_digest=_digest("1"),
                ),
            )
        )
    with pytest.raises(ValueError, match="exceeds verifier limit"):
        compute_audit_reference_identity(
            (
                _reference(
                    "large.md",
                    byte_size=(AUDIT_REFERENCE_IDENTITY_PROFILE_V1.verifier_byte_limit + 1),
                ),
            )
        )


def test_semantic_result_is_exact_frozen_slotted_and_round_trips() -> None:
    semantic = _semantic_result()

    assert set(semantic.to_dict()) == {
        "schema_version",
        "audited_plan_refs",
        "assessments",
        "verdict",
        "remediation_ref",
    }
    assert AuditSemanticResult.from_dict(semantic.to_dict()) == semantic
    assert not hasattr(semantic, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        semantic.verdict = AuditVerdict.NO_GO  # type: ignore[misc]
    with pytest.raises(ValueError, match="invalid AuditSemanticResult fields"):
        AuditSemanticResult.from_dict({**semantic.to_dict(), "generated_at": "forged"})


def test_semantic_collections_are_deeply_immutable_and_strict() -> None:
    semantic = _semantic_result()

    with pytest.raises(TypeError):
        semantic.audited_plan_refs[0] = _reference("other.md")  # type: ignore[index]
    with pytest.raises(ValueError, match="tuple of ArtifactRef"):
        AuditSemanticResult(
            schema_version=AUDIT_SEMANTIC_SCHEMA_VERSION,
            audited_plan_refs=[_reference("plan.md")],  # type: ignore[arg-type]
            assessments=(_covered_row(),),
            verdict=AuditVerdict.GO,
            remediation_ref=None,
        )
    with pytest.raises(ValueError, match="duplicate full references"):
        _semantic_result((_reference("plan.md"), _reference("plan.md")))


def test_standalone_evidence_has_no_authority_or_identity_fields() -> None:
    semantic = _semantic_result()
    evidence = StandaloneAuditEvidence(
        schema_version=STANDALONE_AUDIT_EVIDENCE_SCHEMA_VERSION,
        kind=STANDALONE_AUDIT_EVIDENCE_KIND,
        audited_plan_refs=semantic.audited_plan_refs,
        assessments=semantic.assessments,
        verdict=semantic.verdict,
        remediation_ref=semantic.remediation_ref,
    )

    assert StandaloneAuditEvidence.from_dict(evidence.to_dict()) == evidence
    assert set(evidence.to_dict()) == {
        "schema_version",
        "kind",
        "audited_plan_refs",
        "assessments",
        "verdict",
        "remediation_ref",
    }


@pytest.mark.parametrize(
    "value_type",
    [
        RecipeExecutionId,
        InstallationVersion,
        AuditSlotId,
        AuditAttemptId,
    ],
)
def test_opaque_identifiers_are_frozen_slotted_and_nonempty(
    value_type: type[RecipeExecutionId | InstallationVersion | AuditSlotId | AuditAttemptId],
) -> None:
    value = value_type("opaque-value")
    assert value.value == "opaque-value"
    assert not hasattr(value, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.value = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-empty"):
        value_type(" ")


def test_reservation_binds_exact_refs_paths_and_prior_head() -> None:
    reservation = _reservation()

    assert reservation.slot_key.recipe_execution_id == RecipeExecutionId("execution-1")
    assert reservation.audit_round == AuditRound(1)
    assert reservation.authority_path.is_relative_to(reservation.allowed_root)
    assert not hasattr(reservation, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reservation.plan_set_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="ordered reference identity"):
        dataclasses.replace(
            reservation,
            audited_plan_refs=(_reference("different.md"),),
        )
    with pytest.raises(ValueError, match="plan_set_id"):
        dataclasses.replace(reservation, plan_set_id="caller-selected")
    with pytest.raises(ValueError, match="under allowed_root"):
        dataclasses.replace(
            reservation,
            authority_path=Path("/tmp/outside/authority.json"),
        )


def test_prepared_effect_derives_size_and_attempt_collections_are_immutable() -> None:
    effect = _prepared_effect()
    attempt = AuditAttemptRecord(
        slot_id=AuditSlotId("slot-1"),
        attempt_id=AuditAttemptId("attempt-1"),
        lifecycle=AuditAttemptLifecycle.PREPARED,
        semantic_digest=_digest("f"),
        correction_predecessor=None,
        prepared_effects=(effect,),
        committed_outcome=None,
    )

    assert effect.byte_size == len(effect.canonical_bytes)
    assert attempt.prepared_effects == (effect,)
    with pytest.raises(TypeError):
        attempt.prepared_effects[0] = effect  # type: ignore[index]
    with pytest.raises(ValueError, match="does not match canonical_bytes"):
        dataclasses.replace(effect, content_digest=_digest("0"))
    with pytest.raises(ValueError, match="tuple of AuditPreparedEffect"):
        dataclasses.replace(
            attempt,
            prepared_effects=[effect],  # type: ignore[arg-type]
        )


def test_internal_pending_status_cannot_escape_as_public_status() -> None:
    attempt_id = AuditAttemptId("attempt-1")
    materialization = AuditMaterializationResult(
        status=AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION,
        attempt_id=attempt_id,
        verdict=AuditVerdict.GO,
        path=Path("/tmp/audit/authority.json"),
        error=None,
    )
    outcome = AuditOutcome(
        status=AuditOutcomeStatus.PUBLISHED,
        attempt_id=attempt_id,
        verdict=AuditVerdict.GO,
        path=materialization.path,
        error=None,
    )
    committed = AuditAttemptRecord(
        slot_id=AuditSlotId("slot-1"),
        attempt_id=attempt_id,
        lifecycle=AuditAttemptLifecycle.RESPONSE_COMMITTED,
        semantic_digest=_digest("f"),
        correction_predecessor=None,
        prepared_effects=(_prepared_effect(AuditPreparedEffectDeliveryStatus.DELIVERED),),
        committed_outcome=outcome,
    )

    assert committed.committed_outcome is outcome
    assert "PUBLISHED_PENDING_FINALIZATION" not in {status.value for status in AuditOutcomeStatus}
    with pytest.raises(ValueError, match="error"):
        AuditOutcome(
            status=AuditOutcomeStatus.CONFLICT,
            attempt_id=attempt_id,
            verdict=None,
            path=None,
            error=None,
        )
    standalone = AuditOutcome(
        status=AuditOutcomeStatus.NON_PUBLISHED_STANDALONE,
        attempt_id=attempt_id,
        verdict=None,
        path=None,
        error=None,
    )
    assert standalone.path is None
    with pytest.raises(ValueError, match="cannot expose authority payload"):
        dataclasses.replace(standalone, verdict=AuditVerdict.GO)


def test_semantic_terminal_attempts_require_semantic_digest() -> None:
    for lifecycle in (
        AuditAttemptLifecycle.SEMANTIC_ACCEPTED,
        AuditAttemptLifecycle.SEMANTIC_REJECTED,
    ):
        with pytest.raises(ValueError, match="requires semantic_digest"):
            AuditAttemptRecord(
                slot_id=AuditSlotId("slot-1"),
                attempt_id=AuditAttemptId("attempt-1"),
                lifecycle=lifecycle,
                semantic_digest=None,
                correction_predecessor=None,
                prepared_effects=(),
                committed_outcome=None,
            )


def test_runtime_protocols_accept_structural_implementations() -> None:
    class Materializer:
        def materialize(
            self,
            *,
            reservation: AuditIdentityReservation,
            semantic_result_path: Path,
        ) -> AuditMaterializationResult:
            assert semantic_result_path == reservation.semantic_result_path
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION,
                attempt_id=reservation.current_attempt_id,
                verdict=AuditVerdict.GO,
                path=reservation.authority_path,
                error=None,
            )

    class DispositionResolver:
        def resolve(
            self,
            *,
            authority_digest: str,
            plan_digest: str,
        ) -> Path | None:
            assert authority_digest != plan_digest
            return Path("/tmp/audit/disposition.json")

    materializer = Materializer()
    resolver = DispositionResolver()
    assert isinstance(materializer, AuditAuthorityMaterializer)
    assert isinstance(resolver, CommittedDispositionResolver)
    assert (
        materializer.materialize(
            reservation=_reservation(),
            semantic_result_path=Path("/tmp/audit/semantic.json"),
        ).status
        is AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION
    )
    assert resolver.resolve(
        authority_digest=_digest("1"),
        plan_digest=_digest("2"),
    ) == Path("/tmp/audit/disposition.json")
