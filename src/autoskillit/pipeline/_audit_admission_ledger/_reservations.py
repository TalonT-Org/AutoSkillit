"""Reservation & dispatch helpers for the audit admission ledger.

The shard owns the per-transition SQL for the ``reserve`` and
``resolve_reservation_handle`` public methods. ``_resolve_reservation_handle_read``
runs under ``try/finally`` only (no ``BEGIN IMMEDIATE``); the facade has
already parsed the handle and matched the authority id.
"""

from __future__ import annotations

import secrets
import sqlite3

from autoskillit.core import (
    AUDIT_REFERENCE_IDENTITY_PROFILE_V1,
    AuditAttemptId,
    AuditAttemptLifecycle,
    AuditCycleHead,
    AuditIdentityReservation,
    AuditReservationOutcome,
    AuditReservationRequest,
    AuditRound,
    AuditSlotId,
    AuditSlotKey,
    ReservationDecision,
    compute_audit_reference_identity,
    compute_audit_slot_id,
)
from autoskillit.pipeline import audit_admission_ledger as _facade_module
from autoskillit.pipeline._audit_admission_ledger._encoders import (
    _HANDLE_DIGEST_DOMAIN,
    _HANDLE_PREFIX,
    _head_key,
    _json_dumps,
    _json_loads,
    _outcome_from_dict,
    _reservation_from_dict,
    _reservation_to_dict,
    _slot_key_to_dict,
)
from autoskillit.pipeline._audit_admission_ledger._installations import _installation_row_read
from autoskillit.pipeline._audit_admission_ledger._reads import _head_by_key_read

__all__ = [
    "_reserve_locked",
    "_conflict_outcome",
    "_dispatch_new_slot",
    "_dispatch_correction",
    "_redispatch_open",
    "_issue_handle",
    "_build_reservation",
    "_resolve_reservation_handle_read",
]


def _reserve_locked(
    connection: sqlite3.Connection,
    request: AuditReservationRequest,
    *,
    authority_id: str,
) -> AuditReservationOutcome:
    installation_row = _installation_row_read(connection, request.recipe_execution_id)
    if (
        installation_row is None
        or installation_row.installation_version != request.installation_version
    ):
        raise ValueError(
            "reserve() requires a matching installation created via create_or_get_installation()"
        )
    if installation_row.retired:
        return _conflict_outcome(request, "installation_retired")

    head_key = _head_key(
        request.recipe_execution_id,
        request.cycle_id,
        request.scope_id,
        request.part_id,
    )
    live_head = _head_by_key_read(connection, head_key)
    # Slot identity is derived from the caller's EXPLICIT attested prior-authority
    # reference (request.parent_authority_digest), never from the ledger's current
    # live head: an exact redelivery of the same runtime binding must resolve to the
    # same slot even after the head has advanced past what this attempt targets.
    # Liveness against the current head is enforced later, at commit_authority()'s
    # CAS, not at slot-identity derivation time.
    current_head = (
        live_head
        if live_head is not None
        and live_head.current_authority_digest == request.parent_authority_digest
        else None
    )

    ordered_reference_identity = compute_audit_reference_identity(request.audited_plan_refs)
    slot_key = AuditSlotKey(
        recipe_execution_id=request.recipe_execution_id,
        installation_version=request.installation_version,
        step_name=request.step_name,
        invocation_template_digest=request.invocation_template_digest,
        slot_intent_digest=request.slot_intent_digest,
        ordered_reference_identity=ordered_reference_identity,
        prior_authority_digest=request.parent_authority_digest,
    )
    slot_id = compute_audit_slot_id(slot_key)

    slot_row = connection.execute(
        "SELECT current_attempt_id FROM slots WHERE slot_id = ?",
        (slot_id.value,),
    ).fetchone()

    if slot_row is None:
        if request.retry_after_audit_attempt_id is not None:
            return _conflict_outcome(request, "retry_token_unknown_slot", slot_key=slot_key)
        return _dispatch_new_slot(
            connection,
            request=request,
            slot_key=slot_key,
            slot_id=slot_id,
            current_head=current_head,
            authority_id=authority_id,
        )

    attempt_id = AuditAttemptId(slot_row[0])
    attempt_row = connection.execute(
        "SELECT lifecycle, semantic_digest, committed_outcome_json, reservation_json "
        "FROM attempts WHERE attempt_id = ?",
        (attempt_id.value,),
    ).fetchone()
    assert attempt_row is not None
    lifecycle = AuditAttemptLifecycle(attempt_row[0])
    reservation = _reservation_from_dict(_json_loads(attempt_row[3]))

    if request.retry_after_audit_attempt_id is not None:
        if (
            lifecycle is not AuditAttemptLifecycle.SEMANTIC_REJECTED
            or request.retry_after_audit_attempt_id != attempt_id
        ):
            return _conflict_outcome(
                request, "retry_token_not_terminal_rejection", slot_key=slot_key
            )
        return _dispatch_correction(
            connection,
            request=request,
            slot_id=slot_id,
            predecessor_attempt_id=attempt_id,
            reservation=reservation,
            authority_id=authority_id,
        )

    if lifecycle is AuditAttemptLifecycle.OPEN:
        return _redispatch_open(
            connection,
            attempt_id=attempt_id,
            reservation=reservation,
            authority_id=authority_id,
        )
    if lifecycle is AuditAttemptLifecycle.PREPARED:
        return AuditReservationOutcome(
            decision=ReservationDecision.RESUME_PREPARED,
            slot_key=slot_key,
            attempt_id=attempt_id,
            reservation=reservation,
        )
    if lifecycle is AuditAttemptLifecycle.PUBLISHED_PENDING_FINALIZATION:
        return AuditReservationOutcome(
            decision=ReservationDecision.PUBLISHED_PENDING_FINALIZATION,
            slot_key=slot_key,
            attempt_id=attempt_id,
            reservation=reservation,
        )
    if lifecycle is AuditAttemptLifecycle.RESPONSE_COMMITTED:
        outcome = _outcome_from_dict(_json_loads(attempt_row[2]))
        return AuditReservationOutcome(
            decision=ReservationDecision.EXACT_REPLAY,
            slot_key=slot_key,
            attempt_id=attempt_id,
            replay_outcome=outcome,
        )
    if lifecycle is AuditAttemptLifecycle.SEMANTIC_REJECTED:
        return _conflict_outcome(request, "correction_token_required", slot_key=slot_key)
    return _conflict_outcome(request, f"attempt_{lifecycle.value.lower()}", slot_key=slot_key)


def _conflict_outcome(
    request: AuditReservationRequest,
    detail: str,
    *,
    slot_key: AuditSlotKey | None = None,
) -> AuditReservationOutcome:
    resolved_slot_key = slot_key or AuditSlotKey(
        recipe_execution_id=request.recipe_execution_id,
        installation_version=request.installation_version,
        step_name=request.step_name,
        invocation_template_digest=request.invocation_template_digest,
        slot_intent_digest=request.slot_intent_digest,
        ordered_reference_identity=compute_audit_reference_identity(request.audited_plan_refs),
        prior_authority_digest=None,
    )
    return AuditReservationOutcome(
        decision=ReservationDecision.CONFLICT,
        slot_key=resolved_slot_key,
        attempt_id=AuditAttemptId(secrets.token_hex(16)),
        conflict_detail=detail,
    )


def _build_reservation(
    *,
    request: AuditReservationRequest,
    slot_id: AuditSlotId,
    slot_key: AuditSlotKey,
    attempt_id: AuditAttemptId,
    audit_round: AuditRound,
    current_head: AuditCycleHead | None,
) -> AuditIdentityReservation:
    ordered_reference_identity = slot_key.ordered_reference_identity
    root = request.allowed_root / "audit-admission" / slot_id.value / attempt_id.value
    return AuditIdentityReservation(
        slot_id=slot_id,
        slot_key=slot_key,
        current_attempt_id=attempt_id,
        runtime_binding_digest=request.runtime_binding_digest,
        reference_identity_profile_id=AUDIT_REFERENCE_IDENTITY_PROFILE_V1.profile_id,
        audited_plan_refs=request.audited_plan_refs,
        plan_set_id=ordered_reference_identity,
        cycle_id=request.cycle_id,
        scope_id=request.scope_id,
        part_id=request.part_id,
        audit_round=audit_round,
        parent_authority_digest=slot_key.prior_authority_digest,
        generated_at=_facade_module._now_iso(),
        allowed_root=request.allowed_root,
        semantic_result_path=root / "semantic.json",
        inventory_path=root / "inventory.json",
        authority_path=root / "authority.json",
        expected_head=current_head,
        tracker_target_order_id=request.tracker_target_order_id,
        tracker_expected=request.tracker_expected,
    )


def _issue_handle(
    connection: sqlite3.Connection,
    attempt_id: AuditAttemptId,
    authority_id: str,
) -> str:
    secret = secrets.token_hex(32)
    handle_digest = _facade_module.compute_bytes_hash(secret.encode("utf-8"))
    connection.execute(
        "UPDATE attempts SET handle_digest = ? WHERE attempt_id = ?",
        (f"{_HANDLE_DIGEST_DOMAIN}:{handle_digest}", attempt_id.value),
    )
    return f"{_HANDLE_PREFIX}.{authority_id}.{secret}"


def _dispatch_new_slot(
    connection: sqlite3.Connection,
    *,
    request: AuditReservationRequest,
    slot_key: AuditSlotKey,
    slot_id: AuditSlotId,
    current_head: AuditCycleHead | None,
    authority_id: str,
) -> AuditReservationOutcome:
    attempt_id = AuditAttemptId(secrets.token_hex(16))
    audit_round = AuditRound(1 if current_head is None else current_head.audit_round + 1)
    head_key = _head_key(
        request.recipe_execution_id,
        request.cycle_id,
        request.scope_id,
        request.part_id,
    )
    reservation = _build_reservation(
        request=request,
        slot_id=slot_id,
        slot_key=slot_key,
        attempt_id=attempt_id,
        audit_round=audit_round,
        current_head=current_head,
    )
    now = _facade_module._now_iso()
    connection.execute(
        "INSERT INTO slots(slot_id, recipe_execution_id, installation_version, "
        "step_name, head_key, slot_key_json, current_attempt_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            slot_id.value,
            request.recipe_execution_id.value,
            request.installation_version.value,
            request.step_name,
            head_key,
            _json_dumps(_slot_key_to_dict(slot_key)),
            attempt_id.value,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO attempts(attempt_id, slot_id, lifecycle, semantic_digest, "
        "correction_predecessor, handle_digest, reservation_json, "
        "committed_outcome_json, created_at) "
        "VALUES (?, ?, ?, NULL, NULL, NULL, ?, NULL, ?)",
        (
            attempt_id.value,
            slot_id.value,
            AuditAttemptLifecycle.OPEN.value,
            _json_dumps(_reservation_to_dict(reservation)),
            now,
        ),
    )
    handle = _issue_handle(connection, attempt_id, authority_id)
    return AuditReservationOutcome(
        decision=ReservationDecision.DISPATCH_NEW,
        slot_key=slot_key,
        attempt_id=attempt_id,
        reservation=reservation,
        reservation_handle=handle,
    )


def _dispatch_correction(
    connection: sqlite3.Connection,
    *,
    request: AuditReservationRequest,
    slot_id: AuditSlotId,
    predecessor_attempt_id: AuditAttemptId,
    reservation: AuditIdentityReservation,
    authority_id: str,
) -> AuditReservationOutcome:
    attempt_id = AuditAttemptId(secrets.token_hex(16))
    next_reservation = _build_reservation(
        request=request,
        slot_id=slot_id,
        slot_key=reservation.slot_key,
        attempt_id=attempt_id,
        audit_round=reservation.audit_round,
        current_head=reservation.expected_head,
    )
    now = _facade_module._now_iso()
    connection.execute(
        "UPDATE slots SET current_attempt_id = ? WHERE slot_id = ?",
        (attempt_id.value, slot_id.value),
    )
    connection.execute(
        "INSERT INTO attempts(attempt_id, slot_id, lifecycle, semantic_digest, "
        "correction_predecessor, handle_digest, reservation_json, "
        "committed_outcome_json, created_at) "
        "VALUES (?, ?, ?, NULL, ?, NULL, ?, NULL, ?)",
        (
            attempt_id.value,
            slot_id.value,
            AuditAttemptLifecycle.OPEN.value,
            predecessor_attempt_id.value,
            _json_dumps(_reservation_to_dict(next_reservation)),
            now,
        ),
    )
    handle = _issue_handle(connection, attempt_id, authority_id)
    return AuditReservationOutcome(
        decision=ReservationDecision.DISPATCH_NEW,
        slot_key=reservation.slot_key,
        attempt_id=attempt_id,
        reservation=next_reservation,
        reservation_handle=handle,
    )


def _redispatch_open(
    connection: sqlite3.Connection,
    *,
    attempt_id: AuditAttemptId,
    reservation: AuditIdentityReservation,
    authority_id: str,
) -> AuditReservationOutcome:
    handle = _issue_handle(connection, attempt_id, authority_id)
    return AuditReservationOutcome(
        decision=ReservationDecision.REDISPATCH_OPEN,
        slot_key=reservation.slot_key,
        attempt_id=attempt_id,
        reservation=reservation,
        reservation_handle=handle,
    )


def _resolve_reservation_handle_read(
    connection: sqlite3.Connection,
    *,
    handle_digest: str,
) -> AuditIdentityReservation | None:
    """Read-only handle verification keyed by ``handle_digest``.

    Returns ``None`` when the handle is unknown or the attempt is no
    longer ``OPEN``.
    """
    row = connection.execute(
        "SELECT reservation_json, lifecycle FROM attempts WHERE handle_digest = ?",
        (handle_digest,),
    ).fetchone()
    if row is None or row[1] != AuditAttemptLifecycle.OPEN.value:
        return None
    return _reservation_from_dict(_json_loads(row[0]))
