"""Stdlib-only durable authority for declared fixed-set join batches."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import string
import tempfile
import time
from collections.abc import Generator, Iterable, Mapping
from pathlib import Path
from typing import Any

if __package__:
    from ._session_binding import resolve_channel_dir as _resolve_channel_dir
else:
    from _session_binding import (  # type: ignore[import-not-found,no-redef]
        resolve_channel_dir as _resolve_channel_dir,
    )

LEDGER_FILENAME = "join_ledger.json"
LOCK_FILENAME = "join_ledger.lock"
JOIN_LEDGER_SCHEMA_VERSION = 2

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILURE = "failure"
OUTCOME_FAILED = "failed"
OUTCOME_LAUNCH_FAILED = "launch-failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_TIMED_OUT = "timed-out"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_INTERRUPTION = "interruption"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_MISSING = "missing"
OUTCOME_REAPED = "reaped"

WAVE_PENDING = "pending"
WAVE_COMPLETE = "complete"
WAVE_PARTIAL_TIMEOUT = "partial_timeout"
WAVE_FAILURE = "failure"
WAVE_LAUNCH_FAILED = "launch_failed"
WAVE_CANCELLED = "cancelled"
WAVE_INTERRUPTION = "interruption"
WAVE_MISSING_CHILD = "missing_child"
WAVE_REAPED = "reaped"
WAVE_PARTIAL = "partial"

_NON_SUCCESS_WAVE_OUTCOMES: frozenset[str] = frozenset(
    {
        WAVE_PARTIAL_TIMEOUT,
        WAVE_FAILURE,
        WAVE_LAUNCH_FAILED,
        WAVE_CANCELLED,
        WAVE_INTERRUPTION,
        WAVE_MISSING_CHILD,
        WAVE_REAPED,
        WAVE_PARTIAL,
    }
)
_TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_SUCCESS,
        OUTCOME_COMPLETED,
        OUTCOME_FAILURE,
        OUTCOME_FAILED,
        OUTCOME_LAUNCH_FAILED,
        OUTCOME_TIMEOUT,
        OUTCOME_TIMED_OUT,
        OUTCOME_CANCELLED,
        OUTCOME_INTERRUPTION,
        OUTCOME_INTERRUPTED,
        OUTCOME_MISSING,
        OUTCOME_REAPED,
    }
)
_COMPLETED_OUTCOMES: frozenset[str] = frozenset({OUTCOME_SUCCESS, OUTCOME_COMPLETED})
_BATCH_ID_ALPHABET = string.ascii_lowercase + string.digits


class _CorruptedLedger(Exception):
    """Raised when the on-disk ledger cannot be parsed safely."""


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


def ledger_paths(flag_dir: Path) -> tuple[Path, Path]:
    return (flag_dir / LEDGER_FILENAME, flag_dir / LOCK_FILENAME)


def resolve_flag_dir(project_root: Path) -> Path:
    return _resolve_channel_dir(project_root)


@contextlib.contextmanager
def _flock(lock_path: Path) -> Generator[int, None, None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _empty_payload() -> dict[str, Any]:
    return {
        "schema_version": JOIN_LEDGER_SCHEMA_VERSION,
        "sessions": {},
        "batches": {},
        "declaration_index": {},
    }


def _read_locked(ledger_path: Path) -> dict[str, Any]:
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_payload()
    except OSError:
        raise
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _CorruptedLedger(f"join ledger is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _CorruptedLedger("join ledger top level must be an object")
    if payload.get("schema_version") != JOIN_LEDGER_SCHEMA_VERSION:
        raise _CorruptedLedger(
            "unsupported join ledger schema_version: "
            f"{payload.get('schema_version')!r}; expected {JOIN_LEDGER_SCHEMA_VERSION}"
        )
    fields = ("sessions", "batches", "declaration_index")
    if not all(isinstance(payload.get(field), dict) for field in fields):
        raise _CorruptedLedger("join ledger v2 indexes must be objects")
    return payload


def write_join_ledger(ledger_path: Path, payload: dict[str, Any]) -> None:
    """Persist one locked ledger snapshot through atomic replacement."""
    encoded = _canonical(payload).encode("utf-8")
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".join_ledger.", suffix=".tmp", dir=str(ledger_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, ledger_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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


def open_or_replay(
    flag_dir: Path,
    *,
    parent: Mapping[str, object],
    selected_source: Mapping[str, object],
    key: str,
    declaration: Mapping[str, object],
    now: float | None = None,
) -> dict[str, Any]:
    """Open one immutable batch or replay its exact prior declaration."""
    if not isinstance(key, str) or not key:
        raise JoinLedgerError("declaration key must be a non-empty string")
    normalized_parent, normalized_source = _normalize_scope(parent, selected_source)
    normalized_declaration = json.loads(_canonical(dict(declaration)))
    if not isinstance(normalized_declaration, dict):
        raise JoinLedgerError("declaration must be an object")
    expected = _canonical(
        {
            "parent": normalized_parent,
            "selected_source": normalized_source,
            "declaration": normalized_declaration,
        }
    )
    index_key = _canonical(
        {
            "request_session_id": normalized_parent["request_session_id"],
            "managed_parent_id": normalized_parent["managed_parent_id"],
            "skill_name": normalized_source["skill_name"],
            "source_artifact_digest": normalized_source["source_artifact_digest"],
            "source_artifact_incarnation_id": normalized_source["source_artifact_incarnation_id"],
            "caller_key": key,
        }
    )
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            indexed = payload["declaration_index"].get(index_key)
            if isinstance(indexed, dict):
                if indexed.get("canonical_declaration") != expected:
                    raise JoinLedgerError("declaration key conflicts with a changed declaration")
                batch_id = indexed.get("join_batch_id")
                batch = payload["batches"].get(batch_id)
                if not isinstance(batch, dict):
                    raise JoinLedgerError("declaration index references a missing batch")
                _scope_record(
                    payload,
                    normalized_parent["request_session_id"],
                    normalized_parent["managed_parent_id"],
                )["active_batch_id"] = batch_id
                write_join_ledger(ledger_path, payload)
                return batch

            active = _active_from_payload(
                payload,
                normalized_parent["request_session_id"],
                normalized_parent["managed_parent_id"],
            )
            if active is not None and active.get("wave_outcome") == WAVE_PENDING:
                raise JoinLedgerError(
                    "another wave is already open for "
                    f"{normalized_parent['request_session_id']!r}/"
                    f"{normalized_parent['managed_parent_id']!r}: "
                    f"join_batch_id={active.get('join_batch_id')!r}"
                )
            batch = _make_batch(
                parent=normalized_parent,
                source=normalized_source,
                caller_key=key,
                declaration=normalized_declaration,
                ts=ts,
            )
            payload["batches"][batch["join_batch_id"]] = batch
            payload["declaration_index"][index_key] = {
                "join_batch_id": batch["join_batch_id"],
                "canonical_declaration": expected,
            }
            _scope_record(
                payload,
                normalized_parent["request_session_id"],
                normalized_parent["managed_parent_id"],
            )["active_batch_id"] = batch["join_batch_id"]
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during open_or_replay: {exc}") from exc


def declare_batch(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    skill_name: str,
    artifact_digest: str,
    assignments: Iterable[str],
    now: float | None = None,
) -> dict[str, Any]:
    labels = list(assignments)
    if not session_id or not top_level_parent:
        raise JoinLedgerError("declare_batch requires session_id and top_level_parent")
    if not skill_name:
        raise JoinLedgerError("declare_batch requires skill_name")
    if not artifact_digest:
        raise JoinLedgerError("declare_batch requires artifact_digest")
    return open_or_replay(
        flag_dir,
        parent={
            "request_session_id": session_id,
            "managed_parent_id": top_level_parent,
            "managed_leaf_id": "",
        },
        selected_source={
            "skill_name": skill_name,
            "source_artifact_digest": artifact_digest,
            "source_artifact_incarnation_id": "native",
        },
        key=f"native:{_new_batch_id()}",
        declaration={"assignments": [{"label": label} for label in labels]},
        now=now,
    )


def _assignment(batch: dict[str, Any], assignment_id: str) -> dict[str, Any]:
    assignments = batch.get("assignments")
    if not isinstance(assignments, list):
        raise JoinLedgerError("batch assignments are malformed")
    for assignment in assignments:
        if isinstance(assignment, dict) and assignment.get("assignment_id") == assignment_id:
            return assignment
    raise JoinLedgerError(f"assignment {assignment_id!r} is not part of this batch")


def _append_attempt(
    assignment: dict[str, Any],
    *,
    attempt_id: str,
    run_id: str,
    evidence: Mapping[str, object],
    ts: float,
) -> dict[str, Any]:
    if assignment.get("current_attempt_id") is not None:
        raise JoinLedgerError("assignment already has a current attempt")
    if not attempt_id or not run_id:
        raise JoinLedgerError("attempt_id and run_id must be non-empty strings")
    attempts = assignment.get("attempts")
    if not isinstance(attempts, list):
        raise JoinLedgerError("assignment attempts are malformed")
    record = {
        "attempt_id": attempt_id,
        "run_id": run_id,
        "generated_home_id": evidence.get("generated_home_id"),
        "leaf_projection_artifact_digest": evidence.get("leaf_projection_artifact_digest"),
        "backend_session_id": evidence.get("backend_session_id"),
        "process_id": evidence.get("process_id"),
        "permit_id": evidence.get("permit_id"),
        "admitted_at": ts,
        "running_at": None,
        "terminal_at": None,
        "terminal_event_id": None,
        "terminal_payload_digest": None,
        "outcome": OUTCOME_PENDING,
        "result_reference": None,
        "result_digest": None,
    }
    attempts.append(record)
    assignment["current_attempt_id"] = attempt_id
    assignment["current_run_id"] = run_id
    assignment["lifecycle_state"] = "admitted"
    assignment["updated_at"] = ts
    return record


def _batch_and_assignment(
    payload: dict[str, Any], *, batch_id: str, assignment_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = payload["batches"].get(batch_id)
    if not isinstance(batch, dict):
        raise JoinLedgerError(f"unknown join batch {batch_id!r}")
    return batch, _assignment(batch, assignment_id)


def _mutate_attempt(
    flag_dir: Path,
    *,
    batch_id: str,
    assignment_id: str,
    attempt_id: str,
    run_id: str,
    evidence: Mapping[str, object],
    retry: bool,
    prior_attempt_id: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch, assignment = _batch_and_assignment(
                payload, batch_id=batch_id, assignment_id=assignment_id
            )
            if retry:
                if assignment.get("current_attempt_id") != prior_attempt_id:
                    raise JoinLedgerError("retry does not name the current prior attempt")
                if assignment.get("outcome") not in _TERMINAL_OUTCOMES:
                    raise JoinLedgerError("retry requires a terminal prior attempt")
                assignment["current_attempt_id"] = None
                assignment["current_run_id"] = None
                assignment["outcome"] = OUTCOME_PENDING
                assignment["terminal_event_id"] = None
                assignment["terminal_payload_digest"] = None
            record = _append_attempt(
                assignment,
                attempt_id=attempt_id,
                run_id=run_id,
                evidence=evidence,
                ts=ts,
            )
            batch["lifecycle_state"] = "admitted"
            write_join_ledger(ledger_path, payload)
            return record
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during admission: {exc}") from exc


def admit_assignment(
    flag_dir: Path,
    *,
    batch_id: str,
    assignment_id: str,
    attempt_id: str,
    run_id: str,
    evidence: Mapping[str, object] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    return _mutate_attempt(
        flag_dir,
        batch_id=batch_id,
        assignment_id=assignment_id,
        attempt_id=attempt_id,
        run_id=run_id,
        evidence=evidence or {},
        retry=False,
        now=now,
    )


def append_retry_attempt(
    flag_dir: Path,
    *,
    batch_id: str,
    assignment_id: str,
    prior_attempt_id: str,
    attempt_id: str,
    run_id: str,
    evidence: Mapping[str, object] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Append a new attempt only after the recorded prior attempt is terminal."""
    return _mutate_attempt(
        flag_dir,
        batch_id=batch_id,
        assignment_id=assignment_id,
        attempt_id=attempt_id,
        run_id=run_id,
        evidence=evidence or {},
        retry=True,
        prior_attempt_id=prior_attempt_id,
        now=now,
    )


def mark_assignment_running(
    flag_dir: Path,
    *,
    batch_id: str,
    assignment_id: str,
    attempt_id: str,
    run_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch, assignment = _batch_and_assignment(
                payload, batch_id=batch_id, assignment_id=assignment_id
            )
            if (
                assignment.get("current_attempt_id") != attempt_id
                or assignment.get("current_run_id") != run_id
            ):
                raise JoinLedgerError("running event names a stale attempt or run")
            attempts = assignment.get("attempts")
            if not isinstance(attempts, list) or not attempts:
                raise JoinLedgerError("running event has no admitted attempt")
            attempts[-1]["running_at"] = ts
            assignment["lifecycle_state"] = "running"
            assignment["updated_at"] = ts
            batch["lifecycle_state"] = "running"
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during running transition: {exc}") from exc


def claim_assignment(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    tool_use_id: str,
    agent_id: str | None = None,
) -> dict[str, Any] | None:
    if not session_id or not top_level_parent or not tool_use_id:
        raise JoinLedgerError(
            "claim_assignment requires session_id, top_level_parent, tool_use_id"
        )
    if agent_id:
        return None
    ledger_path, lock_path = ledger_paths(flag_dir)
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch = _active_from_payload(payload, session_id, top_level_parent)
            if batch is None or batch.get("wave_outcome") != WAVE_PENDING:
                return None
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("batch assignments are malformed")
            for entry in assignments:
                if isinstance(entry, dict) and entry.get("tool_use_id") == tool_use_id:
                    raise JoinLedgerError(
                        f"tool_use_id {tool_use_id!r} already claimed for this wave"
                    )
            for entry in assignments:
                if not isinstance(entry, dict) or entry.get("tool_use_id") is not None:
                    continue
                entry["tool_use_id"] = tool_use_id
                _append_attempt(
                    entry,
                    attempt_id=tool_use_id,
                    run_id=tool_use_id,
                    evidence={},
                    ts=time.time(),
                )
                entry["lifecycle_state"] = "running"
                batch["lifecycle_state"] = "running"
                write_join_ledger(ledger_path, payload)
                return {**entry, "join_batch_id": batch["join_batch_id"]}
            raise JoinLedgerError(
                f"no unclaimed assignment available for tool_use_id {tool_use_id!r}"
            )
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during claim: {exc}") from exc


def _aggregate_wave_outcome(assignments: list[object]) -> str:
    if not assignments:
        return WAVE_MISSING_CHILD
    entries = [entry for entry in assignments if isinstance(entry, dict)]
    outcomes = [str(entry.get("outcome", OUTCOME_PENDING)) for entry in entries]
    if len(entries) != len(assignments) or any(outcome == OUTCOME_PENDING for outcome in outcomes):
        return WAVE_PENDING
    if any(entry.get("cleanup_outcome") == OUTCOME_REAPED for entry in entries):
        return WAVE_REAPED
    if all(outcome in _COMPLETED_OUTCOMES for outcome in outcomes):
        return WAVE_COMPLETE
    if any(outcome == OUTCOME_LAUNCH_FAILED for outcome in outcomes):
        return WAVE_LAUNCH_FAILED
    if any(outcome in {OUTCOME_INTERRUPTION, OUTCOME_INTERRUPTED} for outcome in outcomes):
        return WAVE_INTERRUPTION
    if any(outcome == OUTCOME_CANCELLED for outcome in outcomes):
        return WAVE_CANCELLED
    if any(outcome in {OUTCOME_TIMEOUT, OUTCOME_TIMED_OUT} for outcome in outcomes):
        return WAVE_PARTIAL_TIMEOUT
    if any(outcome in {OUTCOME_FAILURE, OUTCOME_FAILED} for outcome in outcomes):
        return WAVE_FAILURE
    if all(outcome == OUTCOME_MISSING for outcome in outcomes):
        return WAVE_MISSING_CHILD
    if any(outcome == OUTCOME_REAPED for outcome in outcomes):
        return WAVE_REAPED
    return WAVE_PARTIAL


def settle_assignment(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    tool_use_id: str,
    outcome: str,
    batch_id: str | None = None,
    assignment_id: str | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    terminal_event_id: str | None = None,
    terminal_payload_digest: str | None = None,
    result_reference: str | None = None,
    result_digest: str | None = None,
    cleanup_outcome: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if outcome not in _TERMINAL_OUTCOMES:
        raise JoinLedgerError(f"invalid outcome {outcome!r}")
    if cleanup_outcome is not None and cleanup_outcome != OUTCOME_REAPED:
        raise JoinLedgerError(f"invalid cleanup outcome {cleanup_outcome!r}")
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch = _active_from_payload(payload, session_id, top_level_parent)
            if batch_id is not None:
                candidate = payload["batches"].get(batch_id)
                if not isinstance(candidate, dict):
                    raise JoinLedgerError(f"unknown join batch {batch_id!r}")
                batch = candidate
            if not isinstance(batch, dict):
                raise JoinLedgerError("no active wave to settle")
            if (
                batch.get("request_session_id") != session_id
                or batch.get("managed_parent_id") != top_level_parent
            ):
                raise JoinLedgerError("settlement scope does not match the batch")
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("batch assignments are malformed")
            target: dict[str, Any] | None = None
            for entry in assignments:
                if not isinstance(entry, dict):
                    continue
                if assignment_id is not None and entry.get("assignment_id") == assignment_id:
                    target = entry
                    break
                if assignment_id is None and entry.get("tool_use_id") == tool_use_id:
                    target = entry
                    break
            if target is None:
                raise JoinLedgerError(f"tool_use_id {tool_use_id!r} was not claimed by this wave")
            if assignment_id is not None and target.get("tool_use_id") not in {None, tool_use_id}:
                raise JoinLedgerError("settlement tool_use_id does not match the assignment")
            current_attempt_id = target.get("current_attempt_id")
            current_run_id = target.get("current_run_id")
            expected_attempt = attempt_id or current_attempt_id
            expected_run = run_id or current_run_id
            if not isinstance(expected_attempt, str) or not isinstance(expected_run, str):
                raise JoinLedgerError("settlement requires an admitted current attempt and run")
            if current_attempt_id != expected_attempt or current_run_id != expected_run:
                raise JoinLedgerError("settlement names a stale attempt or run")
            event_id = terminal_event_id or tool_use_id
            if not event_id:
                raise JoinLedgerError("terminal_event_id must be non-empty")
            payload_digest = terminal_payload_digest or _digest(
                {
                    "outcome": outcome,
                    "result_reference": result_reference,
                    "result_digest": result_digest,
                    "cleanup_outcome": cleanup_outcome,
                }
            )
            if target.get("terminal_event_id") is not None:
                if (
                    target.get("terminal_event_id") == event_id
                    and target.get("terminal_payload_digest") == payload_digest
                ):
                    return batch
                raise JoinLedgerError("conflicting terminal event or payload for assignment")
            target["outcome"] = outcome
            target["terminal_event_id"] = event_id
            target["terminal_payload_digest"] = payload_digest
            target["result_reference"] = result_reference
            target["result_digest"] = result_digest
            target["cleanup_outcome"] = cleanup_outcome
            target["lifecycle_state"] = "terminal"
            target["updated_at"] = ts
            attempts = target.get("attempts")
            if isinstance(attempts, list) and attempts:
                current_attempt = attempts[-1]
                if current_attempt.get("attempt_id") != expected_attempt:
                    raise JoinLedgerError("current attempt record is malformed")
                current_attempt.update(
                    {
                        "terminal_at": ts,
                        "terminal_event_id": event_id,
                        "terminal_payload_digest": payload_digest,
                        "outcome": outcome,
                        "result_reference": result_reference,
                        "result_digest": result_digest,
                    }
                )
            aggregate = _aggregate_wave_outcome(assignments)
            batch["wave_outcome"] = aggregate
            if aggregate != WAVE_PENDING:
                batch["lifecycle_state"] = "terminal"
                batch["settled_at"] = ts
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during settle: {exc}") from exc


def cancel_batch(
    flag_dir: Path,
    *,
    batch_id: str,
    terminal_event_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    return _terminalize_unsettled(
        flag_dir,
        batch_id=batch_id,
        terminal_event_id=terminal_event_id,
        outcome=OUTCOME_CANCELLED,
        now=now,
    )


def reconcile_batch(
    flag_dir: Path,
    *,
    batch_id: str,
    terminal_event_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    return _terminalize_unsettled(
        flag_dir,
        batch_id=batch_id,
        terminal_event_id=terminal_event_id,
        outcome=OUTCOME_MISSING,
        now=now,
    )


def _terminalize_unsettled(
    flag_dir: Path,
    *,
    batch_id: str,
    terminal_event_id: str,
    outcome: str,
    now: float | None,
) -> dict[str, Any]:
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch = payload["batches"].get(batch_id)
            if not isinstance(batch, dict):
                raise JoinLedgerError(f"unknown join batch {batch_id!r}")
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("batch assignments are malformed")
            for entry in assignments:
                if not isinstance(entry, dict) or entry.get("outcome") != OUTCOME_PENDING:
                    continue
                assignment_event_id = f"{terminal_event_id}:{entry.get('assignment_id', '')}"
                entry.update(
                    {
                        "outcome": outcome,
                        "terminal_event_id": assignment_event_id,
                        "terminal_payload_digest": _digest({"outcome": outcome}),
                        "lifecycle_state": "terminal",
                        "updated_at": ts,
                    }
                )
                attempts = entry.get("attempts")
                if isinstance(attempts, list) and attempts:
                    attempts[-1].update(
                        {
                            "terminal_at": ts,
                            "terminal_event_id": entry["terminal_event_id"],
                            "terminal_payload_digest": entry["terminal_payload_digest"],
                            "outcome": outcome,
                        }
                    )
            batch["wave_outcome"] = _aggregate_wave_outcome(assignments)
            batch["lifecycle_state"] = "terminal"
            batch["settled_at"] = ts
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during terminalization: {exc}") from exc


def aggregate_batch(flag_dir: Path, *, batch_id: str) -> str:
    ledger_path, lock_path = ledger_paths(flag_dir)
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch = payload["batches"].get(batch_id)
            if not isinstance(batch, dict):
                raise JoinLedgerError(f"unknown join batch {batch_id!r}")
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("batch assignments are malformed")
            return _aggregate_wave_outcome(assignments)
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during aggregate: {exc}") from exc


def active_batch(
    flag_dir: Path, *, session_id: str, top_level_parent: str
) -> dict[str, Any] | None:
    ledger_path, lock_path = ledger_paths(flag_dir)
    try:
        with _flock(lock_path):
            return _active_from_payload(_read_locked(ledger_path), session_id, top_level_parent)
    except (_CorruptedLedger, OSError) as exc:
        return {"_corrupted": True, "error": str(exc)}


def can_release_stop(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    session_binding: dict[str, Any] | None,
) -> tuple[bool, str]:
    if session_binding is None or not bool(session_binding.get("join_required")):
        return (True, "no join-bearing skill loaded in this session")
    if (
        not bool(session_binding.get("binding_valid", True))
        or session_binding.get("managed_parent_id", top_level_parent) != top_level_parent
    ):
        return (False, "join-bearing binding has invalid managed scope")
    batch = active_batch(flag_dir, session_id=session_id, top_level_parent=top_level_parent)
    if batch is None:
        return (False, "join-bearing skill loaded but no declared wave for this parent")
    if batch.get("_corrupted"):
        return (False, f"join ledger is unreadable: {batch.get('error')}")
    wave_outcome = batch.get("wave_outcome", WAVE_PENDING)
    if wave_outcome == WAVE_COMPLETE:
        return (True, "active wave is complete")
    if wave_outcome in _NON_SUCCESS_WAVE_OUTCOMES:
        return (False, f"active wave settled non-success: {wave_outcome}")
    return (False, f"active wave is unresolved: {wave_outcome}")


__all__ = """
JOIN_LEDGER_SCHEMA_VERSION JoinLedgerError LEDGER_FILENAME LOCK_FILENAME
OUTCOME_CANCELLED OUTCOME_COMPLETED OUTCOME_FAILED OUTCOME_FAILURE
OUTCOME_INTERRUPTED OUTCOME_INTERRUPTION OUTCOME_LAUNCH_FAILED OUTCOME_MISSING
OUTCOME_PENDING OUTCOME_REAPED OUTCOME_SUCCESS OUTCOME_TIMED_OUT OUTCOME_TIMEOUT
WAVE_CANCELLED WAVE_COMPLETE WAVE_FAILURE WAVE_INTERRUPTION WAVE_LAUNCH_FAILED
WAVE_MISSING_CHILD WAVE_PARTIAL WAVE_PARTIAL_TIMEOUT WAVE_PENDING WAVE_REAPED
active_batch admit_assignment aggregate_batch append_retry_attempt can_release_stop
cancel_batch claim_assignment declare_batch ledger_paths mark_assignment_running
open_or_replay reconcile_batch resolve_flag_dir settle_assignment write_join_ledger
""".split()
