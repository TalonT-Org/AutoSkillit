"""Stdlib-only durable authority for declared fixed-set join batches.

Hook subprocesses import this module as ``_join_ledger`` while package callers
import it as ``autoskillit.hooks._join_ledger``. Keep both paths working and do
not add runtime imports from ``autoskillit.*``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

if __package__:
    from ._session_binding import resolve_channel_dir as _resolve_channel_dir
else:
    from _session_binding import (  # type: ignore[import-not-found,no-redef]
        resolve_channel_dir as _resolve_channel_dir,
    )

if __package__:
    from ._join import (  # noqa: F401
        _NON_SUCCESS_WAVE_OUTCOMES,
        JOIN_LEDGER_SCHEMA_VERSION,
        LEDGER_FILENAME,
        LOCK_FILENAME,
        OUTCOME_CANCELLED,
        OUTCOME_FAILURE,
        OUTCOME_INTERRUPTION,
        OUTCOME_LAUNCH_FAILED,
        OUTCOME_MISSING,
        OUTCOME_PENDING,
        OUTCOME_REAPED,
        OUTCOME_SUCCESS,
        OUTCOME_TIMEOUT,
        WAVE_CANCELLED,
        WAVE_COMPLETE,
        WAVE_FAILURE,
        WAVE_INTERRUPTION,
        WAVE_LAUNCH_FAILED,
        WAVE_MISSING_CHILD,
        WAVE_PARTIAL,
        WAVE_PARTIAL_TIMEOUT,
        WAVE_PENDING,
        WAVE_REAPED,
        JoinLedgerError,
        _active_from_payload,
        _aggregate_wave_outcome,
        _append_attempt,
        _batch_and_assignment,
        _canonical,
        _CorruptedLedger,
        _digest,
        _flock,
        _make_batch,
        _mutate_attempt,
        _new_batch_id,
        _normalize_scope,
        _read_locked,
        _scope_record,
        _terminalize_unsettled,
        is_terminal_outcome,
        ledger_paths,
        write_join_ledger,
    )
else:
    from _join import (  # type: ignore[import-not-found,no-redef]  # noqa: F401
        _NON_SUCCESS_WAVE_OUTCOMES,
        JOIN_LEDGER_SCHEMA_VERSION,
        LEDGER_FILENAME,
        LOCK_FILENAME,
        OUTCOME_CANCELLED,
        OUTCOME_FAILURE,
        OUTCOME_INTERRUPTION,
        OUTCOME_LAUNCH_FAILED,
        OUTCOME_MISSING,
        OUTCOME_PENDING,
        OUTCOME_REAPED,
        OUTCOME_SUCCESS,
        OUTCOME_TIMEOUT,
        WAVE_CANCELLED,
        WAVE_COMPLETE,
        WAVE_FAILURE,
        WAVE_INTERRUPTION,
        WAVE_LAUNCH_FAILED,
        WAVE_MISSING_CHILD,
        WAVE_PARTIAL,
        WAVE_PARTIAL_TIMEOUT,
        WAVE_PENDING,
        WAVE_REAPED,
        JoinLedgerError,
        _active_from_payload,
        _aggregate_wave_outcome,
        _append_attempt,
        _batch_and_assignment,
        _canonical,
        _CorruptedLedger,
        _digest,
        _flock,
        _make_batch,
        _mutate_attempt,
        _new_batch_id,
        _normalize_scope,
        _read_locked,
        _scope_record,
        _terminalize_unsettled,
        is_terminal_outcome,
        ledger_paths,
        write_join_ledger,
    )


def resolve_flag_dir(project_root: Path) -> Path:
    return _resolve_channel_dir(project_root)


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
                # Direct Agent calls have no server admission phase, so the
                # claim itself records their namespaced attempt and run identity.
                _append_attempt(
                    entry,
                    attempt_id=tool_use_id,
                    run_id=f"claim:{tool_use_id}",
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
    if not is_terminal_outcome(outcome):
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


def settle_unadmitted_assignment(
    flag_dir: Path,
    *,
    batch_id: str,
    assignment_id: str,
    terminal_event_id: str,
    terminal_payload_digest: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Mark a single pre-admission assignment terminal without touching peers.

    Unlike :func:`settle_assignment`, this helper does not require
    ``current_attempt_id``/``current_run_id`` on the target entry — the
    assignment never advanced through ``admit_assignment``, so those fields
    are intentionally absent. The helper also confines its update to the
    single named entry, leaving peer assignments in their PENDING state so
    they can continue through their own lifecycle.

    Use this only for assignments whose lifecycle terminated before the
    supervisor ever recorded an attempt (e.g. a projection-time failure
    after permit acquisition). For post-admission failures, prefer
    :func:`settle_assignment` because it enforces the attempt/run guard.
    """
    if not terminal_event_id:
        raise JoinLedgerError("terminal_event_id must be non-empty")
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = time.time() if now is None else now
    payload_digest = terminal_payload_digest or _digest({"assignment_id": assignment_id})
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            batch = payload["batches"].get(batch_id)
            if not isinstance(batch, dict):
                raise JoinLedgerError(f"unknown join batch {batch_id!r}")
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("batch assignments are malformed")
            target: dict[str, Any] | None = None
            for entry in assignments:
                if isinstance(entry, dict) and entry.get("assignment_id") == assignment_id:
                    target = entry
                    break
            if target is None:
                raise JoinLedgerError(
                    f"assignment {assignment_id!r} is not a member of batch {batch_id!r}"
                )
            if target.get("terminal_event_id") is not None:
                if (
                    target.get("terminal_event_id") == terminal_event_id
                    and target.get("terminal_payload_digest") == payload_digest
                ):
                    return batch
                raise JoinLedgerError(
                    "conflicting terminal event or payload for un-admitted assignment"
                )
            target["outcome"] = OUTCOME_LAUNCH_FAILED
            target["terminal_event_id"] = terminal_event_id
            target["terminal_payload_digest"] = payload_digest
            target["lifecycle_state"] = "terminal"
            target["updated_at"] = ts
            batch["wave_outcome"] = _aggregate_wave_outcome(assignments)
            if batch["wave_outcome"] != WAVE_PENDING:
                batch["lifecycle_state"] = "terminal"
                batch["settled_at"] = ts
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during un-admitted settle: {exc}") from exc


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
OUTCOME_CANCELLED OUTCOME_FAILURE OUTCOME_INTERRUPTION OUTCOME_LAUNCH_FAILED
OUTCOME_MISSING OUTCOME_PENDING OUTCOME_REAPED OUTCOME_SUCCESS OUTCOME_TIMEOUT
WAVE_CANCELLED WAVE_COMPLETE WAVE_FAILURE WAVE_INTERRUPTION WAVE_LAUNCH_FAILED
WAVE_MISSING_CHILD WAVE_PARTIAL WAVE_PARTIAL_TIMEOUT WAVE_PENDING WAVE_REAPED
active_batch admit_assignment aggregate_batch append_retry_attempt can_release_stop
cancel_batch claim_assignment declare_batch ledger_paths mark_assignment_running
open_or_replay reconcile_batch resolve_flag_dir settle_assignment
settle_unadmitted_assignment write_join_ledger
""".split()
