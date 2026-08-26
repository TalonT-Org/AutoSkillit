"""Pure JSON record encoding helpers and encoding-domain constants for the ledger.

This module centralizes:
- ``_now_iso`` — the wall-clock helper used by every durable timestamp.
- ``_head_key`` — the canonical hash for the (cycle, scope, part) head slot.
- ``_slot_key_to_dict`` / ``_slot_key_from_dict`` — round-trip serializers for
  ``AuditSlotKey``.
- ``_head_to_dict`` / ``_head_from_dict`` — round-trip serializers for
  ``AuditCycleHead``.
- ``_reservation_to_dict`` / ``_reservation_from_dict`` — round-trip serializers
  for ``AuditIdentityReservation``.
- ``_outcome_to_dict`` / ``_outcome_from_dict`` — round-trip serializers for
  ``AuditOutcome``.
- ``_normalize_required_effect_names`` / ``_required_effect_names_to_json`` /
  ``_required_effect_names_from_json`` — tuple canonicalization and JSON
  round-trip for the ordered effect-name set.
- ``_validate_replay_projection`` — defensive validation that the
  ``outcome.replay_response_json`` payload is a JSON object.
- ``_json_dumps`` / ``_json_loads`` — typed wrappers around the canonical
  encoding/decoding helpers used throughout the ledger.

The encoding-domain constants (``_HEAD_KEY_DOMAIN``, ``_HANDLE_DIGEST_DOMAIN``,
``_HANDLE_PREFIX``) live here so ``_reservations`` can import the handle
constants from one place — the single-source-of-truth invariant verified by
``test_handle_constants_single_source_in_encoders``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ArtifactRef,
    AuditAttemptId,
    AuditCycleHead,
    AuditIdentityReservation,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditRound,
    AuditSlotId,
    AuditSlotKey,
    AuditVerdict,
    InstallationVersion,
    KillReason,
    RecipeExecutionId,
    canonical_json_bytes,
    compute_canonical_hash,
)

__all__ = [
    # Constants
    "_HEAD_KEY_DOMAIN",
    "_HANDLE_DIGEST_DOMAIN",
    "_HANDLE_PREFIX",
    # Time helper
    "_now_iso",
    # Head slot helpers
    "_head_key",
    # Slot key encoders
    "_slot_key_to_dict",
    "_slot_key_from_dict",
    # Head record encoders
    "_head_to_dict",
    "_head_from_dict",
    # Reservation record encoders
    "_reservation_to_dict",
    "_reservation_from_dict",
    # Outcome record encoders
    "_outcome_to_dict",
    "_outcome_from_dict",
    # Required-effect-name helpers
    "_normalize_required_effect_names",
    "_required_effect_names_to_json",
    "_required_effect_names_from_json",
    # Replay projection validation
    "_validate_replay_projection",
    # Generic JSON helpers
    "_json_dumps",
    "_json_loads",
]


_HEAD_KEY_DOMAIN = "autoskillit:audit-admission:head-key:v1:sha256"
_HANDLE_DIGEST_DOMAIN = "autoskillit:audit-admission:reservation-handle:v1:sha256"
_HANDLE_PREFIX = "adr1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _head_key(
    recipe_execution_id: RecipeExecutionId,
    cycle_id: str,
    scope_id: str,
    part_id: str,
) -> str:
    return compute_canonical_hash(
        {
            "recipe_execution_id": recipe_execution_id.value,
            "cycle_id": cycle_id,
            "scope_id": scope_id,
            "part_id": part_id,
        },
        domain=_HEAD_KEY_DOMAIN,
    )


def _slot_key_to_dict(key: AuditSlotKey) -> dict[str, Any]:
    return {
        "recipe_execution_id": key.recipe_execution_id.value,
        "installation_version": key.installation_version.value,
        "step_name": key.step_name,
        "invocation_template_digest": key.invocation_template_digest,
        "slot_intent_digest": key.slot_intent_digest,
        "ordered_reference_identity": key.ordered_reference_identity,
        "prior_authority_digest": key.prior_authority_digest,
    }


def _slot_key_from_dict(data: dict[str, Any]) -> AuditSlotKey:
    return AuditSlotKey(
        recipe_execution_id=RecipeExecutionId(data["recipe_execution_id"]),
        installation_version=InstallationVersion(data["installation_version"]),
        step_name=data["step_name"],
        invocation_template_digest=data["invocation_template_digest"],
        slot_intent_digest=data["slot_intent_digest"],
        ordered_reference_identity=data["ordered_reference_identity"],
        prior_authority_digest=data["prior_authority_digest"],
    )


def _head_to_dict(head: AuditCycleHead) -> dict[str, Any]:
    return {
        "execution_generation": head.execution_generation,
        "cycle_id": head.cycle_id,
        "plan_set_id": head.plan_set_id,
        "scope_id": head.scope_id,
        "part_id": head.part_id,
        "current_authority_digest": head.current_authority_digest,
        "audit_round": head.audit_round,
        "audited_plan_refs": [ref.to_dict() for ref in head.audited_plan_refs],
        "inventory_ref": head.inventory_ref.to_dict(),
        "verdict": head.verdict.value,
        "authorized_successor_part_id": head.authorized_successor_part_id,
    }


def _head_from_dict(data: dict[str, Any]) -> AuditCycleHead:
    return AuditCycleHead(
        execution_generation=data["execution_generation"],
        cycle_id=data["cycle_id"],
        plan_set_id=data["plan_set_id"],
        scope_id=data["scope_id"],
        part_id=data["part_id"],
        current_authority_digest=data["current_authority_digest"],
        audit_round=data["audit_round"],
        audited_plan_refs=tuple(ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]),
        inventory_ref=ArtifactRef.from_dict(data["inventory_ref"]),
        verdict=AuditVerdict(data["verdict"]),
        authorized_successor_part_id=data["authorized_successor_part_id"],
    )


def _reservation_to_dict(reservation: AuditIdentityReservation) -> dict[str, Any]:
    return {
        "slot_id": reservation.slot_id.value,
        "slot_key": _slot_key_to_dict(reservation.slot_key),
        "current_attempt_id": reservation.current_attempt_id.value,
        "runtime_binding_digest": reservation.runtime_binding_digest,
        "reference_identity_profile_id": reservation.reference_identity_profile_id,
        "audited_plan_refs": [ref.to_dict() for ref in reservation.audited_plan_refs],
        "plan_set_id": reservation.plan_set_id,
        "cycle_id": reservation.cycle_id,
        "scope_id": reservation.scope_id,
        "part_id": reservation.part_id,
        "audit_round": reservation.audit_round.value,
        "parent_authority_digest": reservation.parent_authority_digest,
        "generated_at": reservation.generated_at,
        "allowed_root": str(reservation.allowed_root),
        "semantic_result_path": str(reservation.semantic_result_path),
        "inventory_path": str(reservation.inventory_path),
        "authority_path": str(reservation.authority_path),
        "expected_head": (
            _head_to_dict(reservation.expected_head) if reservation.expected_head else None
        ),
        "tracker_target_order_id": reservation.tracker_target_order_id,
        "tracker_expected": reservation.tracker_expected,
    }


def _reservation_from_dict(data: dict[str, Any]) -> AuditIdentityReservation:
    expected_head = data["expected_head"]
    return AuditIdentityReservation(
        slot_id=AuditSlotId(data["slot_id"]),
        slot_key=_slot_key_from_dict(data["slot_key"]),
        current_attempt_id=AuditAttemptId(data["current_attempt_id"]),
        runtime_binding_digest=data["runtime_binding_digest"],
        reference_identity_profile_id=data["reference_identity_profile_id"],
        audited_plan_refs=tuple(ArtifactRef.from_dict(item) for item in data["audited_plan_refs"]),
        plan_set_id=data["plan_set_id"],
        cycle_id=data["cycle_id"],
        scope_id=data["scope_id"],
        part_id=data["part_id"],
        audit_round=AuditRound(data["audit_round"]),
        parent_authority_digest=data["parent_authority_digest"],
        generated_at=data["generated_at"],
        allowed_root=Path(data["allowed_root"]),
        semantic_result_path=Path(data["semantic_result_path"]),
        inventory_path=Path(data["inventory_path"]),
        authority_path=Path(data["authority_path"]),
        expected_head=(_head_from_dict(expected_head) if expected_head else None),
        tracker_target_order_id=data.get("tracker_target_order_id"),
        tracker_expected=data.get("tracker_expected", False),
    )


def _outcome_to_dict(outcome: AuditOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status.value,
        "attempt_id": outcome.attempt_id.value,
        "verdict": outcome.verdict.value if outcome.verdict is not None else None,
        "path": str(outcome.path) if outcome.path is not None else None,
        "error": outcome.error,
        "kill_reason": outcome.kill_reason.value,
        "replay_response_json": outcome.replay_response_json,
        "tracker_target_order_id": outcome.tracker_target_order_id,
        "tracker_expected": outcome.tracker_expected,
    }


def _outcome_from_dict(data: dict[str, Any]) -> AuditOutcome:
    return AuditOutcome(
        status=AuditOutcomeStatus(data["status"]),
        attempt_id=AuditAttemptId(data["attempt_id"]),
        verdict=AuditVerdict(data["verdict"]) if data["verdict"] is not None else None,
        path=Path(data["path"]) if data["path"] is not None else None,
        error=data["error"],
        kill_reason=KillReason(data.get("kill_reason", KillReason.NATURAL_EXIT.value)),
        replay_response_json=data.get("replay_response_json"),
        tracker_target_order_id=data.get("tracker_target_order_id"),
        tracker_expected=data.get("tracker_expected", False),
    )


def _normalize_required_effect_names(
    required_effect_names: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(required_effect_names, tuple) or not required_effect_names:
        raise ValueError("required_effect_names must be a non-empty tuple")
    normalized: list[str] = []
    for effect_name in required_effect_names:
        if (
            not isinstance(effect_name, str)
            or not effect_name
            or effect_name != effect_name.strip()
        ):
            raise ValueError("required_effect_names must contain normalized non-empty strings")
        normalized.append(effect_name)
    if len(set(normalized)) != len(normalized):
        raise ValueError("required_effect_names cannot contain duplicates")
    return tuple(sorted(normalized))


def _required_effect_names_to_json(required_effect_names: tuple[str, ...]) -> str:
    return _json_dumps({"effect_names": list(required_effect_names)})


def _required_effect_names_from_json(payload_json: str) -> tuple[str, ...]:
    payload = _json_loads(payload_json)
    if set(payload) != {"effect_names"} or not isinstance(payload["effect_names"], list):
        raise ValueError("invalid durable required_effect_names projection")
    return _normalize_required_effect_names(tuple(payload["effect_names"]))


def _validate_replay_projection(outcome: AuditOutcome) -> str:
    replay_projection = outcome.replay_response_json
    if replay_projection is None:
        raise ValueError("finalize_response requires replay_response_json")
    try:
        projection = json.loads(replay_projection)
    except json.JSONDecodeError as exc:
        raise ValueError("replay_response_json must be a JSON object") from exc
    if not isinstance(projection, dict):
        raise ValueError("replay_response_json must be a JSON object")
    return replay_projection


def _json_dumps(payload: dict[str, Any]) -> str:
    return canonical_json_bytes(payload).decode("utf-8")


def _json_loads(text: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(text)
    return result
