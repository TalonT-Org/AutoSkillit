"""Assignment attempt mutation primitives for the join ledger."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .declaration import JoinLedgerError, _digest
from .model import OUTCOME_PENDING, _aggregate_wave_outcome, is_terminal_outcome
from .storage import (
    _CorruptedLedger,
    _flock,
    _read_locked,
    ledger_paths,
    write_join_ledger,
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
                if not is_terminal_outcome(assignment.get("outcome")):
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
            any_updated = False
            for entry in assignments:
                if not isinstance(entry, dict) or entry.get("outcome") != OUTCOME_PENDING:
                    continue
                any_updated = True
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
            if not any_updated:
                # Already-terminal batch — return without rewriting the
                # ledger so we don't overwrite the original `settled_at`
                # timestamp or churn the file with identical contents.
                return batch
            batch["wave_outcome"] = _aggregate_wave_outcome(assignments)
            batch["lifecycle_state"] = "terminal"
            batch["settled_at"] = ts
            write_join_ledger(ledger_path, payload)
            return batch
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during terminalization: {exc}") from exc
