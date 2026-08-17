"""Shared cross-process join ledger for declared-batch fixed-set fan-in.

Used by the ``declare_join_batch`` MCP tool and the join-aware hook
scripts (``guards/join_claim_guard.py``, ``guards/join_settle_guard.py``,
``guards/join_stop_guard.py``). All callers MUST go through this helper;
hook scripts may not import ``autoskillit.*`` because they run in the
harness's stdlib-only environment.

Persistence:
    * Atomic cross-process locking via ``fcntl.flock`` on a sibling lock file.
    * Atomic replacement via ``os.replace`` from a same-directory tempfile.
    * JSON envelope, one record per session+top-level-parent+batch tuple.

Failure mode: every read/write/claim/settle that cannot proceed safely
returns a structured failure. A corrupted or unreadable ledger is NEVER
treated as "no active wave" — that would let children run unobserved.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import string
import tempfile
import time
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any

LEDGER_FILENAME = "join_ledger.json"
LOCK_FILENAME = "join_ledger.lock"

#: Terminal outcomes for a claimed direct handle.
OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_INTERRUPTION = "interruption"
OUTCOME_MISSING = "missing"

#: Aggregate wave outcomes (set when all handles settle).
WAVE_PENDING = "pending"
WAVE_COMPLETE = "complete"
WAVE_PARTIAL_TIMEOUT = "partial_timeout"
WAVE_FAILURE = "failure"
WAVE_CANCELLED = "cancelled"
WAVE_INTERRUPTION = "interruption"
WAVE_MISSING_CHILD = "missing_child"

_NON_SUCCESS_WAVE_OUTCOMES: frozenset[str] = frozenset(
    {
        WAVE_PARTIAL_TIMEOUT,
        WAVE_FAILURE,
        WAVE_CANCELLED,
        WAVE_INTERRUPTION,
        WAVE_MISSING_CHILD,
    }
)

_BATCH_ID_ALPHABET = string.ascii_lowercase + string.digits


def _new_batch_id() -> str:
    """Return a fresh opaque batch id distinct from ``AdmissionBatchId``."""
    return "".join(secrets.choice(_BATCH_ID_ALPHABET) for _ in range(24))


def ledger_paths(flag_dir: Path) -> tuple[Path, Path]:
    """Return (ledger_path, lock_path) inside the flag directory."""
    return (flag_dir / LEDGER_FILENAME, flag_dir / LOCK_FILENAME)


@contextlib.contextmanager
def _flock(lock_path: Path) -> Generator[int, None, None]:
    """Acquire an exclusive ``fcntl.flock`` on ``lock_path`` for this process.

    Each hook process must open the lock file independently rather than
    inherit an fd — a lock is released only when every duplicate fd to it
    closes. Raises ``OSError`` on contention or filesystem failure.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _read_locked(ledger_path: Path) -> dict[str, Any]:
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema_version": 1, "sessions": {}}
    except OSError:
        raise
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _CorruptedLedger(f"join ledger is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _CorruptedLedger("join ledger top level must be an object")
    sessions = parsed.get("sessions")
    if not isinstance(sessions, dict):
        raise _CorruptedLedger("join ledger sessions must be an object")
    parsed["sessions"] = sessions
    parsed.setdefault("schema_version", 1)
    return parsed


def _atomic_write_locked(ledger_path: Path, payload: dict[str, Any]) -> None:
    """Write the ledger content via an atomic tempfile + ``os.replace``."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".join_ledger.", suffix=".tmp", dir=str(ledger_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, ledger_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _CorruptedLedger(Exception):
    """Raised when the on-disk ledger cannot be parsed safely."""


class JoinLedgerError(Exception):
    """Base class for join-ledger failures visible to MCP/hook callers."""


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
    """Open one parent/wave ledger with the given resolved assignments.

    Returns the new batch record (including its ``join_batch_id``). Refuses
    when another wave is already open for the same session+parent.
    """
    if not session_id or not top_level_parent:
        raise JoinLedgerError("declare_batch requires session_id and top_level_parent")
    if not skill_name:
        raise JoinLedgerError("declare_batch requires skill_name")
    if not artifact_digest:
        raise JoinLedgerError("declare_batch requires artifact_digest")
    labels = tuple(assignments)
    if not labels:
        raise JoinLedgerError(
            "declare_batch requires a non-empty ordered set of assignment labels"
        )
    if len(set(labels)) != len(labels):
        raise JoinLedgerError("declare_batch assignment labels must be unique")

    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = now if now is not None else time.time()
    with _flock(lock_path):
        payload = _read_locked(ledger_path)
        sessions = payload["sessions"]
        session_record = sessions.get(session_id)
        if not isinstance(session_record, dict):
            session_record = {"top_level_parents": {}}
            sessions[session_id] = session_record
        parents = session_record.get("top_level_parents")
        if not isinstance(parents, dict):
            parents = {}
            session_record["top_level_parents"] = parents
        parent_record = parents.get(top_level_parent)
        if isinstance(parent_record, dict):
            active = parent_record.get("active_batch")
            if isinstance(active, dict):
                # Only refuse when the prior wave is still pending (not
                # terminal). A complete/failed/etc wave is replaced by the
                # new declaration.
                if active.get("wave_outcome", WAVE_PENDING) == WAVE_PENDING:
                    raise JoinLedgerError(
                        f"another wave is already open for {session_id!r}/{top_level_parent!r}: "
                        f"join_batch_id={active.get('join_batch_id')!r}"
                    )
        join_batch_id = _new_batch_id()
        batch_record: dict[str, Any] = {
            "join_batch_id": join_batch_id,
            "skill_name": skill_name,
            "artifact_digest": artifact_digest,
            "session_id": session_id,
            "top_level_parent": top_level_parent,
            "assignments": [
                {"label": label, "tool_use_id": None, "outcome": OUTCOME_PENDING, "ts": ts}
                for label in labels
            ],
            "opened_at": ts,
            "wave_outcome": WAVE_PENDING,
            "settled_at": None,
        }
        parents[top_level_parent] = {"active_batch": batch_record}
        _atomic_write_locked(ledger_path, payload)
    return batch_record


def claim_assignment(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    tool_use_id: str,
    agent_id: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next unclaimed assignment for the active wave.

    Returns the claimed assignment record, or ``None`` when the parent has
    no open wave (the agent_id-bearing call is exempt; the caller should
    not invoke us from inside a claimed child's subagent context). Refuses
    to claim a duplicate tool_use_id or when all assignments are taken.
    """
    if not session_id or not top_level_parent or not tool_use_id:
        raise JoinLedgerError(
            "claim_assignment requires session_id, top_level_parent, tool_use_id"
        )
    if agent_id:
        # Caller mistake — exempt join re-evaluation inside a child's context.
        return None

    ledger_path, lock_path = ledger_paths(flag_dir)
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            sessions = payload["sessions"]
            session_record = sessions.get(session_id)
            if not isinstance(session_record, dict):
                return None
            parents = session_record.get("top_level_parents", {})
            parent_record = parents.get(top_level_parent) if isinstance(parents, dict) else None
            if not isinstance(parent_record, dict):
                return None
            batch = parent_record.get("active_batch")
            if not isinstance(batch, dict):
                return None
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                return None
            # Detect duplicate claims before taking a new slot.
            for entry in assignments:
                if (
                    isinstance(entry, dict)
                    and entry.get("tool_use_id") == tool_use_id
                    and entry.get("outcome") not in (OUTCOME_PENDING,)
                ):
                    raise JoinLedgerError(
                        f"tool_use_id {tool_use_id!r} already settled for this wave"
                    )
            for entry in assignments:
                if isinstance(entry, dict) and entry.get("tool_use_id") is None:
                    entry["tool_use_id"] = tool_use_id
                    entry["outcome"] = OUTCOME_PENDING
                    entry["ts"] = time.time()
                    _atomic_write_locked(ledger_path, payload)
                    return entry
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
    now: float | None = None,
) -> dict[str, Any]:
    """Mark a claimed handle terminal and compute the aggregate wave outcome.

    Identical duplicate (tool_use_id, outcome) events are idempotent.
    Conflicting terminal events for the same handle fail closed.
    """
    if outcome not in (
        OUTCOME_SUCCESS,
        OUTCOME_FAILURE,
        OUTCOME_TIMEOUT,
        OUTCOME_CANCELLED,
        OUTCOME_INTERRUPTION,
        OUTCOME_MISSING,
    ):
        raise JoinLedgerError(f"invalid outcome {outcome!r}")
    ledger_path, lock_path = ledger_paths(flag_dir)
    ts = now if now is not None else time.time()
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
            sessions = payload["sessions"]
            session_record = sessions.get(session_id)
            if not isinstance(session_record, dict):
                raise JoinLedgerError(f"no session record for {session_id!r}")
            parents = session_record.get("top_level_parents", {})
            parent_record = parents.get(top_level_parent) if isinstance(parents, dict) else None
            if not isinstance(parent_record, dict):
                raise JoinLedgerError(f"no parent record for {top_level_parent!r}")
            batch = parent_record.get("active_batch")
            if not isinstance(batch, dict):
                raise JoinLedgerError("no active wave to settle")
            assignments = batch.get("assignments")
            if not isinstance(assignments, list):
                raise JoinLedgerError("wave assignments malformed")
            target: dict[str, Any] | None = None
            for entry in assignments:
                if isinstance(entry, dict) and entry.get("tool_use_id") == tool_use_id:
                    target = entry
                    break
            if target is None:
                raise JoinLedgerError(f"tool_use_id {tool_use_id!r} was not claimed by this wave")
            existing_outcome = target.get("outcome")
            if existing_outcome == outcome:
                # Idempotent identical duplicate — accept without rewriting.
                return batch
            if existing_outcome != OUTCOME_PENDING and existing_outcome != outcome:
                raise JoinLedgerError(
                    f"conflicting terminal outcome for {tool_use_id!r}: "
                    f"existing={existing_outcome!r}, new={outcome!r}"
                )
            target["outcome"] = outcome
            target["ts"] = ts

            # Compute the aggregate wave outcome.
            aggregate = _aggregate_wave_outcome(assignments)
            if aggregate != WAVE_PENDING:
                batch["wave_outcome"] = aggregate
                batch["settled_at"] = ts
            _atomic_write_locked(ledger_path, payload)
    except _CorruptedLedger as exc:
        raise JoinLedgerError(f"join ledger is unreadable: {exc}") from exc
    except OSError as exc:
        raise JoinLedgerError(f"join ledger IO error during settle: {exc}") from exc
    return batch


def _aggregate_wave_outcome(assignments: list[object]) -> str:
    """Return the deterministic aggregate outcome for the wave."""
    if not assignments:
        return WAVE_MISSING_CHILD
    outcomes: list[str] = []
    for entry in assignments:
        if isinstance(entry, dict):
            outcomes.append(str(entry.get("outcome", OUTCOME_PENDING)))
    if any(o == OUTCOME_PENDING for o in outcomes):
        return WAVE_PENDING
    if all(o == OUTCOME_SUCCESS for o in outcomes):
        return WAVE_COMPLETE
    if any(o == OUTCOME_INTERRUPTION for o in outcomes):
        return WAVE_INTERRUPTION
    if any(o == OUTCOME_CANCELLED for o in outcomes):
        return WAVE_CANCELLED
    if any(o == OUTCOME_TIMEOUT for o in outcomes):
        return WAVE_PARTIAL_TIMEOUT
    if any(o == OUTCOME_FAILURE for o in outcomes):
        return WAVE_FAILURE
    if all(o == OUTCOME_MISSING for o in outcomes):
        return WAVE_MISSING_CHILD
    return WAVE_PENDING


def active_batch(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
) -> dict[str, Any] | None:
    """Return the active batch record or ``None`` when none is open.

    Returns a failure envelope ``{"_corrupted": True, ...}`` when the
    ledger cannot be parsed — callers MUST treat this as fail-closed.
    """
    ledger_path, lock_path = ledger_paths(flag_dir)
    try:
        with _flock(lock_path):
            payload = _read_locked(ledger_path)
    except _CorruptedLedger as exc:
        return {"_corrupted": True, "error": str(exc)}
    except OSError as exc:
        return {"_corrupted": True, "error": str(exc)}
    sessions = payload.get("sessions", {})
    session_record = sessions.get(session_id) if isinstance(sessions, dict) else None
    if not isinstance(session_record, dict):
        return None
    parents = session_record.get("top_level_parents", {})
    parent_record = parents.get(top_level_parent) if isinstance(parents, dict) else None
    if not isinstance(parent_record, dict):
        return None
    batch = parent_record.get("active_batch")
    if not isinstance(batch, dict):
        return None
    return batch


def can_release_stop(
    flag_dir: Path,
    *,
    session_id: str,
    top_level_parent: str,
    session_binding: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Return ``(allow_stop, reason)`` for the Stop completion gate.

    When the session binding has no join-bearing skill loaded, Stop is
    unconditionally allowed (no-op guard). Otherwise the active batch —
    if any — must be ``complete`` to release the success path; partial,
    failed, cancelled, interrupted, or missing waves block Stop with
    a deterministic reason.
    """
    binding_required = bool(session_binding and session_binding.get("join_required"))
    if not binding_required:
        return (True, "no join-bearing skill loaded in this session")
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


__all__ = [
    "JoinLedgerError",
    "LEDGER_FILENAME",
    "LOCK_FILENAME",
    "OUTCOME_PENDING",
    "OUTCOME_SUCCESS",
    "OUTCOME_FAILURE",
    "OUTCOME_TIMEOUT",
    "OUTCOME_CANCELLED",
    "OUTCOME_INTERRUPTION",
    "OUTCOME_MISSING",
    "WAVE_PENDING",
    "WAVE_COMPLETE",
    "WAVE_PARTIAL_TIMEOUT",
    "WAVE_FAILURE",
    "WAVE_CANCELLED",
    "WAVE_INTERRUPTION",
    "WAVE_MISSING_CHILD",
    "active_batch",
    "can_release_stop",
    "claim_assignment",
    "declare_batch",
    "ledger_paths",
    "settle_assignment",
]
