"""Parent-owned audit reservation and authority materialization.

Children submit only :class:`AuditSemanticResult` bytes.  This module derives
the inventory and authority identities from a server reservation, persists the
prepared effects, verifies referenced bytes immediately before publication,
and commits the trusted head through the durable admission ledger.
"""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    AUDIT_CYCLE_SCHEMA_VERSION,
    AUDIT_REFERENCE_IDENTITY_PROFILE_V1,
    ArtifactRef,
    AuditAdmissionLedger,
    AuditAdmissionStorageError,
    AuditAssessmentRow,
    AuditAttemptId,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerificationError,
    AuditCycleVerifier,
    AuditFinalCommitRequest,
    AuditIdentityReservation,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditPreparedEffect,
    AuditPreparedEffectDeliveryStatus,
    AuditPrepareRequest,
    AuditSemanticCodecError,
    InstallationVersion,
    RecipeExecutionId,
    atomic_write,
    canonical_full_reference_records_match,
    canonical_json_bytes,
    compute_bytes_hash,
    compute_canonical_hash,
    load_audit_semantic_result,
    parse_plan_paths,
    read_stable_contained_bytes,
)

_CANONICALIZATION_PROFILE = "autoskillit-audit-artifact-v1"
_LIFECYCLE_ID_DOMAIN = "autoskillit:audit-admission:lifecycle:v1:sha256"
_MEDIA_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def normalize_audited_plan_refs(
    raw_paths: str,
    *,
    allowed_root: Path,
) -> tuple[ArtifactRef, ...]:
    """Resolve and hash the exact ordered audited plan sequence."""

    paths = parse_plan_paths(raw_paths)
    if not paths:
        raise ValueError("attested audit requires at least one all_plan_paths entry")
    references: list[ArtifactRef] = []
    for raw_path in paths:
        resolved, data = read_stable_contained_bytes(
            raw_path,
            allowed_root,
            max_size_bytes=AUDIT_REFERENCE_IDENTITY_PROFILE_V1.verifier_byte_limit,
        )
        references.append(
            ArtifactRef(
                locator=str(resolved),
                media_type=_MEDIA_TYPES.get(resolved.suffix.lower(), "application/octet-stream"),
                schema_version=1,
                byte_size=len(data),
                content_digest=compute_bytes_hash(data),
            )
        )
    return tuple(references)


def derive_initial_lifecycle_ids(
    *,
    recipe_execution_id: RecipeExecutionId,
    step_name: str,
    slot_intent_digest: str,
) -> tuple[str, str, str]:
    """Derive stable server lifecycle IDs without exposing execution identity."""

    digest = compute_canonical_hash(
        {
            "recipe_execution_id": recipe_execution_id.value,
            "slot_intent_digest": slot_intent_digest,
            "step_name": step_name,
        },
        domain=_LIFECYCLE_ID_DOMAIN,
    ).removeprefix("sha256:")
    token = digest[:24]
    return f"cycle-{token}", f"scope-{token}", f"part-{token}"


def load_current_prior_authority(
    authority_path: str,
    *,
    allowed_root: Path,
    ledger: AuditAdmissionLedger,
    recipe_execution_id: RecipeExecutionId,
) -> AuditCycleAuthority:
    """Load an explicit prior only when it is the ledger's trusted head."""

    authority = AuditCycleVerifier(allowed_root).load_authority(authority_path)
    if authority.execution_generation != recipe_execution_id.value:
        raise ValueError("prior authority belongs to another recipe execution")
    head = ledger.current_head(
        recipe_execution_id=recipe_execution_id,
        cycle_id=authority.cycle_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
    )
    if head is None or head.current_authority_digest != authority.authority_digest:
        raise ValueError("prior authority is not the trusted current head")
    return authority


def _inventory_payload(
    reservation: AuditIdentityReservation,
    *,
    assessments: tuple[AuditAssessmentRow, ...],
) -> dict[str, object]:
    requirements = [
        {
            "id": row.requirement_id,
            "text": row.requirement_text,
        }
        for row in assessments
    ]
    return {
        "audited_plan_refs": [ref.to_dict() for ref in reservation.audited_plan_refs],
        "plan_set_id": reservation.plan_set_id,
        "requirement_ids": [row["id"] for row in requirements],
        "requirements": requirements,
        "schema_version": AUDIT_CYCLE_SCHEMA_VERSION,
    }


def _artifact_ref(path: Path, canonical_bytes: bytes) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=AUDIT_CYCLE_SCHEMA_VERSION,
        byte_size=len(canonical_bytes),
        content_digest=compute_bytes_hash(canonical_bytes),
    )


def _prepared_effect(
    *,
    artifact_kind: str,
    canonical_bytes: bytes,
    path: Path,
    semantic_fingerprint: str,
    delivered: bool = False,
) -> AuditPreparedEffect:
    return AuditPreparedEffect(
        artifact_kind=artifact_kind,
        canonical_bytes=canonical_bytes,
        content_digest=compute_bytes_hash(canonical_bytes),
        path=path,
        delivery_status=(
            AuditPreparedEffectDeliveryStatus.DELIVERED
            if delivered
            else AuditPreparedEffectDeliveryStatus.PENDING
        ),
        canonicalization_profile=_CANONICALIZATION_PROFILE,
        semantic_fingerprint=semantic_fingerprint,
    )


def _write_or_verify(effect: AuditPreparedEffect, allowed_root: Path) -> None:
    try:
        atomic_write(
            effect.path,
            effect.canonical_bytes.decode("utf-8"),
            strict_durability=True,
            exclusive=True,
        )
    except FileExistsError:
        _, existing = read_stable_contained_bytes(
            effect.path,
            allowed_root,
            max_size_bytes=max(1, len(effect.canonical_bytes)),
        )
        if existing != effect.canonical_bytes:
            raise AuditSemanticCodecError(
                "prepared_effect_mismatch",
                f"{effect.artifact_kind} prepared path contains different bytes",
            ) from None


class DefaultAuditAuthorityMaterializer:
    """Materialize one reserved semantic result into the trusted v1 authority."""

    def __init__(self, ledger: AuditAdmissionLedger) -> None:
        self._ledger = ledger

    def _semantic_rejection(
        self,
        *,
        attempt_id: AuditAttemptId,
        installation_version: InstallationVersion,
        semantic_digest: str,
        error: str,
    ) -> AuditMaterializationResult:
        prepared = self._ledger.prepare(
            AuditPrepareRequest(
                attempt_id=attempt_id,
                installation_version=installation_version,
                semantic_digest=semantic_digest,
                accepted=False,
            )
        )
        if prepared.conflict_detail is not None:
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.CONFLICT,
                attempt_id=attempt_id,
                verdict=None,
                path=None,
                error=prepared.conflict_detail,
            )
        return AuditMaterializationResult(
            status=AuditMaterializationStatus.SEMANTIC_REJECTED,
            attempt_id=attempt_id,
            verdict=None,
            path=None,
            error=error,
        )

    def materialize(
        self,
        *,
        reservation: AuditIdentityReservation,
        semantic_result_path: Path,
        preflight_step_names: tuple[str, ...],
    ) -> AuditMaterializationResult:
        attempt_id = reservation.current_attempt_id
        installation_version = reservation.slot_key.installation_version
        try:
            resolved_semantic_path, semantic_bytes = read_stable_contained_bytes(
                semantic_result_path,
                reservation.allowed_root,
                max_size_bytes=AUDIT_REFERENCE_IDENTITY_PROFILE_V1.verifier_byte_limit,
            )
            if resolved_semantic_path != reservation.semantic_result_path:
                semantic_digest = compute_bytes_hash(semantic_bytes)
                return self._semantic_rejection(
                    attempt_id=attempt_id,
                    installation_version=installation_version,
                    semantic_digest=semantic_digest,
                    error="semantic_result_path does not match the reserved path",
                )
            semantic_digest = compute_bytes_hash(semantic_bytes)
            try:
                semantic = load_audit_semantic_result(
                    semantic_result_path,
                    reservation.allowed_root,
                )
            except AuditSemanticCodecError as exc:
                return self._semantic_rejection(
                    attempt_id=attempt_id,
                    installation_version=installation_version,
                    semantic_digest=semantic_digest,
                    error=str(exc),
                )
            if not canonical_full_reference_records_match(
                reservation.audited_plan_refs,
                semantic.audited_plan_refs,
            ):
                return self._semantic_rejection(
                    attempt_id=attempt_id,
                    installation_version=installation_version,
                    semantic_digest=semantic_digest,
                    error="semantic audited references differ from the reservation",
                )

            verifier = AuditCycleVerifier(reservation.allowed_root)
            for reference in reservation.audited_plan_refs:
                verifier.verify_artifact_ref(reference)
            if semantic.remediation_ref is not None:
                verifier.verify_artifact_ref(semantic.remediation_ref)

            inventory_bytes = canonical_json_bytes(
                _inventory_payload(
                    reservation,
                    assessments=semantic.assessments,
                )
            )
            inventory_ref = _artifact_ref(reservation.inventory_path, inventory_bytes)
            authority = AuditCycleAuthority.create(
                execution_generation=reservation.slot_key.recipe_execution_id.value,
                cycle_id=reservation.cycle_id,
                plan_set_id=reservation.plan_set_id,
                scope_id=reservation.scope_id,
                part_id=reservation.part_id,
                audit_round=reservation.audit_round.value,
                parent_authority_digest=reservation.parent_authority_digest,
                audited_plan_refs=reservation.audited_plan_refs,
                inventory_ref=inventory_ref,
                assessments=semantic.assessments,
                verdict=semantic.verdict,
                remediation_ref=semantic.remediation_ref,
                generated_at=reservation.generated_at,
            )
            authority_bytes = authority.canonical_bytes
            effects = (
                _prepared_effect(
                    artifact_kind="semantic_result",
                    canonical_bytes=semantic_bytes,
                    path=reservation.semantic_result_path,
                    semantic_fingerprint=semantic_digest,
                    delivered=True,
                ),
                _prepared_effect(
                    artifact_kind="inventory",
                    canonical_bytes=inventory_bytes,
                    path=reservation.inventory_path,
                    semantic_fingerprint=semantic_digest,
                ),
                _prepared_effect(
                    artifact_kind="authority",
                    canonical_bytes=authority_bytes,
                    path=reservation.authority_path,
                    semantic_fingerprint=semantic_digest,
                ),
            )
            prepared = self._ledger.prepare(
                AuditPrepareRequest(
                    attempt_id=attempt_id,
                    installation_version=installation_version,
                    semantic_digest=semantic_digest,
                    accepted=True,
                    effects=effects,
                )
            )
            if not prepared.accepted:
                return AuditMaterializationResult(
                    status=AuditMaterializationStatus.CONFLICT,
                    attempt_id=attempt_id,
                    verdict=None,
                    path=None,
                    error=prepared.conflict_detail or "semantic preparation rejected",
                )
            for effect in effects[1:]:
                _write_or_verify(effect, reservation.allowed_root)

            for reference in reservation.audited_plan_refs:
                verifier.verify_artifact_ref(reference)
            if semantic.remediation_ref is not None:
                verifier.verify_artifact_ref(semantic.remediation_ref)
            verifier.verify_artifact_ref(inventory_ref)

            head = AuditCycleHead(
                execution_generation=authority.execution_generation,
                cycle_id=authority.cycle_id,
                plan_set_id=authority.plan_set_id,
                scope_id=authority.scope_id,
                part_id=authority.part_id,
                current_authority_digest=authority.authority_digest,
                audit_round=authority.audit_round,
                audited_plan_refs=authority.audited_plan_refs,
                inventory_ref=authority.inventory_ref,
                verdict=authority.verdict,
                authorized_successor_part_id=None,
            )
            committed = self._ledger.commit_authority(
                AuditFinalCommitRequest(
                    attempt_id=attempt_id,
                    installation_version=installation_version,
                    expected_head_digest=reservation.parent_authority_digest,
                    new_head=head,
                    preflight_step_names=preflight_step_names,
                )
            )
            if not committed.committed:
                return AuditMaterializationResult(
                    status=AuditMaterializationStatus.CONFLICT,
                    attempt_id=attempt_id,
                    verdict=None,
                    path=None,
                    error=committed.conflict_detail or "authority commit rejected",
                )
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION,
                attempt_id=attempt_id,
                verdict=authority.verdict,
                path=reservation.authority_path,
                error=None,
            )
        except AuditSemanticCodecError as exc:
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.QUARANTINED,
                attempt_id=attempt_id,
                verdict=None,
                path=None,
                error=str(exc),
            )
        except AuditCycleVerificationError as exc:
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.SEMANTIC_REJECTED,
                attempt_id=attempt_id,
                verdict=None,
                path=None,
                error=str(exc),
            )
        except (AuditAdmissionStorageError, OSError, UnicodeError, ValueError) as exc:
            return AuditMaterializationResult(
                status=AuditMaterializationStatus.STORAGE_FAILURE,
                attempt_id=attempt_id,
                verdict=None,
                path=None,
                error=f"{type(exc).__name__}: {exc}",
            )


class DefaultCommittedDispositionResolver:
    """Expose only disposition paths committed by the admission ledger."""

    def __init__(self, ledger: AuditAdmissionLedger) -> None:
        self._ledger = ledger

    def resolve(
        self,
        *,
        authority_digest: str,
        plan_digest: str,
    ) -> Path | None:
        return self._ledger.resolve_disposition(
            authority_digest=authority_digest,
            plan_digest=plan_digest,
        )
