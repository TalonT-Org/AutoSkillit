"""Declaration normalization for the stdlib-only join ledger."""

from __future__ import annotations

import hashlib
import json
import secrets
import string
from collections.abc import Mapping
from typing import Any

OUTCOME_PENDING = "pending"

WAVE_PENDING = "pending"

_BATCH_ID_ALPHABET = string.ascii_lowercase + string.digits


class JoinLedgerError(Exception):
    """A fail-closed ledger contract or persistence failure."""


def _new_batch_id() -> str:
    return "".join(secrets.choice(_BATCH_ID_ALPHABET) for _ in range(24))


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise JoinLedgerError(f"join declaration must be JSON-serializable: {exc}") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _string(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise JoinLedgerError(f"{field} must be a non-empty string")
    return value


def _scope_record(
    payload: dict[str, Any], request_session_id: str, managed_parent_id: str
) -> dict[str, Any]:
    sessions = payload["sessions"]
    session = sessions.setdefault(request_session_id, {"managed_parents": {}})
    if not isinstance(session, dict):
        raise JoinLedgerError("ledger session scope is malformed")
    parents = session.setdefault("managed_parents", {})
    if not isinstance(parents, dict):
        raise JoinLedgerError("ledger parent scope is malformed")
    record = parents.setdefault(managed_parent_id, {"active_batch_id": None})
    if not isinstance(record, dict):
        raise JoinLedgerError("ledger parent record is malformed")
    return record


def _active_from_payload(
    payload: dict[str, Any], request_session_id: str, managed_parent_id: str
) -> dict[str, Any] | None:
    sessions = payload.get("sessions")
    session = sessions.get(request_session_id) if isinstance(sessions, dict) else None
    parents = session.get("managed_parents") if isinstance(session, dict) else None
    scope = parents.get(managed_parent_id) if isinstance(parents, dict) else None
    batch_id = scope.get("active_batch_id") if isinstance(scope, dict) else None
    batches = payload.get("batches")
    batch = (
        batches.get(batch_id) if isinstance(batch_id, str) and isinstance(batches, dict) else None
    )
    return batch if isinstance(batch, dict) else None


def _normalize_scope(
    parent: Mapping[str, object], selected_source: Mapping[str, object]
) -> tuple[dict[str, str], dict[str, str]]:
    normalized_parent = {
        "request_session_id": _string(parent, "request_session_id"),
        "managed_parent_id": _string(parent, "managed_parent_id"),
        "managed_leaf_id": str(parent.get("managed_leaf_id", "")),
    }
    normalized_source = {
        "skill_name": _string(selected_source, "skill_name"),
        "source_artifact_digest": _string(selected_source, "source_artifact_digest"),
        "source_artifact_incarnation_id": _string(
            selected_source, "source_artifact_incarnation_id"
        ),
    }
    return normalized_parent, normalized_source


def _normalize_assignments(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list) or not raw:
        raise JoinLedgerError("declaration assignments must be a non-empty array")
    result: list[dict[str, object]] = []
    labels: set[str] = set()
    for ordinal, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise JoinLedgerError("declaration assignments must contain objects")
        label = _string(item, "label")
        if label in labels:
            raise JoinLedgerError("declaration assignment labels must be unique")
        labels.add(label)
        role = item.get("role", "")
        runtime_key = item.get("runtime_key", "")
        prompt_digest = item.get("prompt_digest", "")
        if not all(isinstance(value, str) for value in (role, runtime_key, prompt_digest)):
            raise JoinLedgerError(
                "assignment role, runtime_key, and prompt_digest must be strings"
            )
        result.append(
            {
                "ordinal": ordinal,
                "label": label,
                "role": role,
                "runtime_key": runtime_key,
                "prompt_digest": prompt_digest,
            }
        )
    return result


def _make_batch(
    *,
    parent: dict[str, str],
    source: dict[str, str],
    caller_key: str,
    declaration: dict[str, object],
    ts: float,
) -> dict[str, Any]:
    assignments = _normalize_assignments(declaration.get("assignments"))
    join_batch_id = _new_batch_id()
    canonical_declaration = _canonical(
        {"parent": parent, "selected_source": source, "declaration": declaration}
    )
    records: list[dict[str, Any]] = []
    for assignment in assignments:
        records.append(
            {
                "assignment_id": f"{join_batch_id}:{assignment['ordinal']}",
                **assignment,
                "tool_use_id": None,
                "attempts": [],
                "current_attempt_id": None,
                "current_run_id": None,
                "lifecycle_state": "queued",
                "outcome": OUTCOME_PENDING,
                "terminal_event_id": None,
                "terminal_payload_digest": None,
                "result_reference": None,
                "result_digest": None,
                "cleanup_outcome": None,
                "created_at": ts,
                "updated_at": ts,
            }
        )
    return {
        "join_batch_id": join_batch_id,
        "request_session_id": parent["request_session_id"],
        "managed_parent_id": parent["managed_parent_id"],
        "managed_leaf_id": parent["managed_leaf_id"],
        "skill_name": source["skill_name"],
        "source_artifact_digest": source["source_artifact_digest"],
        "source_artifact_incarnation_id": source["source_artifact_incarnation_id"],
        "artifact_digest": source["source_artifact_digest"],
        "caller_key": caller_key,
        "canonical_declaration": canonical_declaration,
        "declaration_digest": _digest(canonical_declaration),
        "membership_digest": _digest(assignments),
        "assignments": records,
        "lifecycle_state": "queued",
        "opened_at": ts,
        "settled_at": None,
        "wave_outcome": WAVE_PENDING,
    }
