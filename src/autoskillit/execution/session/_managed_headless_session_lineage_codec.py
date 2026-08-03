"""Canonical serialization for managed headless session lineage records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    NativeShellCaptureObservation,
)

_LEGACY_RECORD_FIELDS = {
    "schema_version",
    "generation",
    "launch_id",
    "decision",
    "backend",
    "session_kind",
    "lineage_anchor",
    "anchor_device",
    "anchor_inode",
    "lineage_digest",
    "record_digest",
    "attempt_ids",
    "candidate_native_session_ids",
    "final_native_session_id",
    "dispatch_id",
    "terminal_state",
    "observations",
    "dropped_observation_count",
}
_RECORD_FIELDS = _LEGACY_RECORD_FIELDS | {"launch_contract_digest"}


def canonical_json(value: object) -> str:
    """Return the canonical JSON representation used by lineage artifacts."""
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest(value: Mapping[str, object]) -> str:
    """Return the canonical SHA-256 digest for a lineage mapping."""
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def record_payload(lineage: ManagedHeadlessSessionLineage) -> dict[str, object]:
    """Return the digest-bearing portion of a lineage record."""
    return {
        "schema_version": lineage.schema_version,
        "generation": lineage.generation,
        "launch_id": lineage.launch_id,
        "decision": lineage.decision.to_dict(),
        "backend": lineage.backend,
        "session_kind": lineage.session_kind.value,
        "lineage_anchor": lineage.lineage_anchor,
        "anchor_device": lineage.anchor_device,
        "anchor_inode": lineage.anchor_inode,
        "lineage_digest": lineage.lineage_digest,
        "launch_contract_digest": lineage.launch_contract_digest,
        "attempt_ids": list(lineage.attempt_ids),
        "candidate_native_session_ids": list(lineage.candidate_native_session_ids),
        "final_native_session_id": lineage.final_native_session_id,
        "dispatch_id": lineage.dispatch_id,
        "terminal_state": lineage.terminal_state.value,
        "observations": [item.to_dict() for item in lineage.observations],
        "dropped_observation_count": lineage.dropped_observation_count,
    }


def record_to_dict(lineage: ManagedHeadlessSessionLineage) -> dict[str, object]:
    """Return the complete persisted representation of a lineage record."""
    return {**record_payload(lineage), "record_digest": lineage.record_digest}


def lineage_from_dict(value: object) -> ManagedHeadlessSessionLineage:
    """Validate and deserialize one lineage record."""
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(_LEGACY_RECORD_FIELDS),
        frozenset(_RECORD_FIELDS),
    }:
        raise ValueError("Invalid managed lineage record shape")
    legacy_record = "launch_contract_digest" not in value
    try:
        lineage = ManagedHeadlessSessionLineage(
            schema_version=_strict_int(value["schema_version"], "schema_version"),
            generation=_strict_int(value["generation"], "generation"),
            launch_id=_strict_str(value["launch_id"], "launch_id"),
            decision=NativeShellCaptureDecision.from_dict(value["decision"]),
            backend=_strict_str(value["backend"], "backend"),
            session_kind=ManagedHeadlessSessionKind(
                _strict_str(value["session_kind"], "session_kind")
            ),
            lineage_anchor=_strict_str(value["lineage_anchor"], "lineage_anchor"),
            anchor_device=_strict_int(value["anchor_device"], "anchor_device"),
            anchor_inode=_strict_int(value["anchor_inode"], "anchor_inode"),
            lineage_digest=_strict_str(value["lineage_digest"], "lineage_digest"),
            record_digest=_strict_str(value["record_digest"], "record_digest"),
            launch_contract_digest=(
                ""
                if legacy_record
                else _strict_str(value["launch_contract_digest"], "launch_contract_digest")
            ),
            attempt_ids=_strict_str_tuple(value["attempt_ids"], "attempt_ids"),
            candidate_native_session_ids=_strict_str_tuple(
                value["candidate_native_session_ids"],
                "candidate_native_session_ids",
            ),
            final_native_session_id=_optional_str(
                value["final_native_session_id"],
                "final_native_session_id",
            ),
            dispatch_id=_optional_str(value["dispatch_id"], "dispatch_id"),
            terminal_state=ManagedHeadlessSessionTerminalState(
                _strict_str(value["terminal_state"], "terminal_state")
            ),
            observations=_observation_tuple(value["observations"]),
            dropped_observation_count=_strict_int(
                value["dropped_observation_count"],
                "dropped_observation_count",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid managed lineage record") from exc
    expected_identity_digest = digest(
        {
            "schema_version": lineage.schema_version,
            "launch_id": lineage.launch_id,
            "decision": lineage.decision.to_dict(),
            "backend": lineage.backend,
            "session_kind": lineage.session_kind.value,
            "lineage_anchor": lineage.lineage_anchor,
            "anchor_device": lineage.anchor_device,
            "anchor_inode": lineage.anchor_inode,
        }
    )
    if lineage.lineage_digest != expected_identity_digest:
        raise ValueError("Managed lineage identity digest mismatch")
    persisted_payload = record_payload(lineage)
    if legacy_record:
        persisted_payload.pop("launch_contract_digest")
    if lineage.record_digest != digest(persisted_payload):
        raise ValueError("Managed lineage record digest mismatch")
    return lineage


def strict_json_load(raw: bytes) -> object:
    """Decode JSON while rejecting malformed UTF-8 and duplicate keys."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate managed lineage JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid managed lineage JSON") from exc


def _strict_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    return value


def _strict_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be int")
    return value


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _strict_str(value, field_name)


def _strict_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    return tuple(_strict_str(item, field_name) for item in value)


def _observation_tuple(value: object) -> tuple[NativeShellCaptureObservation, ...]:
    if not isinstance(value, list):
        raise TypeError("observations must be a list")
    return tuple(NativeShellCaptureObservation.from_dict(item) for item in value)
