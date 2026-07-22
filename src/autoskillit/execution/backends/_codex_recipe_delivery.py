"""Protected Codex recipe attestation and durable insertion receipts.

Rollout files and kitchen markers are correlation inputs only.  They are
same-user writable and never authorize an elevated result limit.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

from autoskillit.core import (
    CodexRecipeDeliveryEvidenceDef,
    RecipeDeliveryAttestation,
    RecipeDeliveryRequest,
)

_MARKER_MAX_BYTES = 64 * 1024
_ROLLOUT_RECORD_MAX_BYTES = 256 * 1024
_LEDGER_SCHEMA_VERSION = 1
_DATABASE_NAME = "codex-recipe-delivery.sqlite3"


@dataclass(frozen=True, slots=True)
class CodexHostCorrelation:
    """Diagnostic marker-to-rollout correlation for one canonical thread."""

    thread_id: str
    marker_path: Path
    rollout_path: Path


@dataclass(frozen=True, slots=True)
class ProtectedStoreAuthority:
    """Host assertion that a local store is outside model/direct-MCP authority."""

    root: Path
    security_identity: str
    local_filesystem: bool
    caller_writable: bool
    initialized_by_host: bool


@dataclass(frozen=True, slots=True)
class CodexAttestationResult:
    """Fail-closed protected-host attestation result."""

    attestation: RecipeDeliveryAttestation | None
    evidence: CodexRecipeDeliveryEvidenceDef | None
    correlation: CodexHostCorrelation | None
    reason: str


@dataclass(frozen=True, slots=True)
class RecipeReceiptHandle:
    """Opaque owner-bound handle kept internal to finalization/enforcement."""

    thread_id: str
    turn_id: str
    outer_call_id: str
    code_digest: str
    reservation_id: str
    owner_token: str


@dataclass(frozen=True, slots=True)
class RecipeReservationResult:
    """Outcome of atomically consuming evidence and reserving a receipt."""

    handle: RecipeReceiptHandle | None
    reason: str


@runtime_checkable
class ProtectedHostAttestationProvider(Protocol):
    """Unforgeable pre-call host channel; implementations live outside MCP input."""

    def attest(
        self,
        *,
        request: RecipeDeliveryRequest,
        correlation: CodexHostCorrelation,
        now_unix: int,
    ) -> RecipeDeliveryAttestation | None: ...

    def store_authority(self, *, thread_id: str) -> ProtectedStoreAuthority | None: ...


@dataclass(frozen=True, slots=True)
class NullProtectedHostAttestationProvider:
    """Default provider: current Codex exposes no protected selected-limit channel."""

    def attest(
        self,
        *,
        request: RecipeDeliveryRequest,
        correlation: CodexHostCorrelation,
        now_unix: int,
    ) -> RecipeDeliveryAttestation | None:
        del request, correlation, now_unix
        return None

    def store_authority(self, *, thread_id: str) -> ProtectedStoreAuthority | None:
        del thread_id
        return None


def _marker_state_dir(project_dir: Path) -> Path:
    override = os.environ.get("AUTOSKILLIT_STATE_DIR")
    if override:
        return Path(override) / "kitchen_state"
    base = project_dir / ".autoskillit" / "temp" / "kitchen_state"
    campaign_id = os.environ.get("AUTOSKILLIT_CAMPAIGN_ID", "")
    if campaign_id:
        if "/" in campaign_id or os.sep in campaign_id:
            return base / "__invalid_campaign__"
        return base / campaign_id
    return base


def _fresh_marker_session_id(path: Path, *, now_unix: int, ttl_seconds: int) -> str | None:
    try:
        with path.open("rb") as stream:
            descriptor = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(descriptor.st_mode)
                or descriptor.st_size <= 0
                or descriptor.st_size > _MARKER_MAX_BYTES
            ):
                return None
            raw = stream.read(_MARKER_MAX_BYTES + 1)
        if len(raw) != descriptor.st_size:
            return None
        record = json.loads(raw.decode("utf-8"))
        session_id = record["session_id"]
        opened_at = datetime.fromisoformat(record["opened_at"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(session_id, str) or not session_id or path.stem != session_id:
        return None
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=UTC)
    age = now_unix - int(opened_at.timestamp())
    if age < 0 or age >= ttl_seconds:
        return None
    return session_id


def enumerate_fresh_codex_marker_ids(
    project_dir: Path,
    *,
    now_unix: int,
    ttl_seconds: int = 24 * 60 * 60,
) -> tuple[tuple[str, Path], ...]:
    """Return every fresh marker; ordering never grants authority."""
    state_dir = _marker_state_dir(project_dir)
    try:
        candidates = tuple(state_dir.glob("*.json"))
    except OSError:
        return ()
    fresh: list[tuple[str, Path]] = []
    for path in candidates:
        session_id = _fresh_marker_session_id(
            path,
            now_unix=now_unix,
            ttl_seconds=ttl_seconds,
        )
        if session_id is not None:
            fresh.append((session_id, path))
    return tuple(sorted(fresh, key=lambda item: (item[0], str(item[1]))))


def read_rollout_thread_id(
    path: Path, *, max_record_bytes: int = _ROLLOUT_RECORD_MAX_BYTES
) -> str:
    """Read only the bounded first rollout record and return its canonical thread ID."""
    try:
        with path.open("rb") as stream:
            raw = stream.readline(max_record_bytes + 1)
    except OSError:
        return ""
    if not raw or len(raw) > max_record_bytes or not raw.endswith(b"\n"):
        return ""
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(record, dict):
        return ""
    record_type = record.get("type")
    if record_type == "thread.started":
        thread_id = record.get("thread_id")
    elif record_type == "session_meta":
        payload = record.get("payload")
        thread_id = payload.get("id") if isinstance(payload, dict) else None
    else:
        return ""
    return thread_id if isinstance(thread_id, str) else ""


def resolve_unique_codex_host_correlation(
    project_dir: Path,
    *,
    locate_rollout: Callable[[str], Path | None],
    now_unix: int,
) -> CodexHostCorrelation | None:
    """Require exactly one fresh marker whose rollout first event has the same thread."""
    matches: list[CodexHostCorrelation] = []
    for session_id, marker_path in enumerate_fresh_codex_marker_ids(
        project_dir,
        now_unix=now_unix,
    ):
        try:
            rollout_path = locate_rollout(session_id)
        except (OSError, RuntimeError, ValueError):
            continue
        if rollout_path is None:
            continue
        thread_id = read_rollout_thread_id(rollout_path)
        if thread_id != session_id:
            continue
        matches.append(
            CodexHostCorrelation(
                thread_id=thread_id,
                marker_path=marker_path,
                rollout_path=rollout_path,
            )
        )
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True, slots=True)
class CodexOuterBudgetAttestor:
    """Combine diagnostic correlation with a protected provider, failing closed."""

    provider: ProtectedHostAttestationProvider
    locate_rollout: Callable[[str], Path | None]
    supported_evidence: Mapping[str, CodexRecipeDeliveryEvidenceDef]

    def attest(
        self,
        *,
        request: RecipeDeliveryRequest,
        project_dir: Path,
        now_unix: int,
    ) -> CodexAttestationResult:
        correlation = resolve_unique_codex_host_correlation(
            project_dir,
            locate_rollout=self.locate_rollout,
            now_unix=now_unix,
        )
        if correlation is None:
            return CodexAttestationResult(None, None, None, "host_correlation_ambiguous")
        try:
            attestation = self.provider.attest(
                request=request,
                correlation=correlation,
                now_unix=now_unix,
            )
        except (OSError, RuntimeError, ValueError, TypeError):
            return CodexAttestationResult(None, None, correlation, "protected_provider_failed")
        if attestation is None:
            return CodexAttestationResult(
                None, None, correlation, "protected_evidence_unavailable"
            )
        if attestation.thread_id != correlation.thread_id:
            return CodexAttestationResult(None, None, correlation, "protected_thread_mismatch")
        evidence = self.supported_evidence.get(attestation.evidence_identity)
        if evidence is None:
            return CodexAttestationResult(None, None, correlation, "unsupported_evidence_identity")
        return CodexAttestationResult(attestation, evidence, correlation, "attested")


class RecipeDeliveryReceiptLedger:
    """Atomic SQLite consumed-call journal and per-thread insertion receipt."""

    def __init__(self, authority: ProtectedStoreAuthority) -> None:
        if not self._authority_valid(authority):
            raise ValueError("protected local store authority required")
        self._authority = authority
        self._path = authority.root / _DATABASE_NAME

    @staticmethod
    def _authority_valid(authority: ProtectedStoreAuthority) -> bool:
        return bool(
            authority.root.is_absolute()
            and authority.security_identity
            and authority.local_filesystem
            and not authority.caller_writable
            and authority.initialized_by_host
        )

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def initialize_protected(
        cls, authority: ProtectedStoreAuthority
    ) -> RecipeDeliveryReceiptLedger:
        """Initialize a store only after the protected host attests its boundary."""
        ledger = cls(authority)
        authority.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if ledger._path.exists():
            existing = cls.open_existing(authority)
            if existing is None:
                raise RuntimeError("existing protected receipt store is invalid")
            return existing
        connection = sqlite3.connect(ledger._path, timeout=0, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=0")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumed_calls (
                    thread_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    outer_call_id TEXT NOT NULL,
                    code_digest TEXT NOT NULL,
                    delivery_call_id TEXT NOT NULL,
                    consumed_at_unix INTEGER NOT NULL,
                    PRIMARY KEY (thread_id, turn_id, outer_call_id, code_digest)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    thread_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'committed')),
                    reservation_id TEXT NOT NULL UNIQUE,
                    owner_token TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    evidence_identity TEXT NOT NULL,
                    caller_requested_outer_tokens INTEGER NOT NULL,
                    host_observed_requested_outer_tokens INTEGER NOT NULL,
                    selected_result_token_limit INTEGER NOT NULL,
                    updated_at_unix INTEGER NOT NULL
                );
                COMMIT;
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(_LEDGER_SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('security_identity', ?)",
                (authority.security_identity,),
            )
        finally:
            connection.close()
        try:
            os.chmod(ledger._path, 0o600)
        except OSError:
            pass
        return ledger

    @classmethod
    def open_existing(
        cls, authority: ProtectedStoreAuthority
    ) -> RecipeDeliveryReceiptLedger | None:
        try:
            ledger = cls(authority)
        except ValueError:
            return None
        if not ledger._path.is_file():
            return None
        connection = ledger._connect()
        if connection is None:
            return None
        try:
            rows = dict(connection.execute("SELECT key, value FROM metadata"))
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        if rows.get("schema_version") != str(_LEDGER_SCHEMA_VERSION):
            return None
        if rows.get("security_identity") != authority.security_identity:
            return None
        return ledger

    def _connect(self) -> sqlite3.Connection | None:
        try:
            connection = sqlite3.connect(
                f"file:{self._path}?mode=rw",
                uri=True,
                timeout=0,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout=0")
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                connection.close()
                return None
            if connection.execute("PRAGMA synchronous").fetchone() != (2,):
                connection.close()
                return None
            return connection
        except sqlite3.Error:
            return None

    def reserve(
        self,
        *,
        request: RecipeDeliveryRequest,
        attestation: RecipeDeliveryAttestation,
        producer: str,
        payload_sha256: str,
        now_unix: int,
    ) -> RecipeReservationResult:
        """Consume one host call and create its pending receipt in one transaction."""
        if (
            request.delivery_call_id != attestation.delivery_call_id
            or request.caller_requested_outer_tokens
            != attestation.host_observed_requested_outer_tokens
            or request.code_digest != attestation.code_digest
            or not producer
            or not payload_sha256.startswith("sha256:")
        ):
            return RecipeReservationResult(None, "reservation_identity_mismatch")
        connection = self._connect()
        if connection is None:
            return RecipeReservationResult(None, "store_unavailable")
        reservation_id = uuid4().hex
        owner_token = uuid4().hex
        handle = RecipeReceiptHandle(
            thread_id=attestation.thread_id,
            turn_id=attestation.turn_id,
            outer_call_id=attestation.outer_call_id,
            code_digest=attestation.code_digest,
            reservation_id=reservation_id,
            owner_token=owner_token,
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM receipts WHERE thread_id = ?",
                (attestation.thread_id,),
            ).fetchone()
            if existing is not None:
                connection.execute("ROLLBACK")
                return RecipeReservationResult(None, f"receipt_{existing[0]}")
            connection.execute(
                """
                INSERT INTO consumed_calls(
                    thread_id, turn_id, outer_call_id, code_digest,
                    delivery_call_id, consumed_at_unix
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attestation.thread_id,
                    attestation.turn_id,
                    attestation.outer_call_id,
                    attestation.code_digest,
                    attestation.delivery_call_id,
                    now_unix,
                ),
            )
            connection.execute(
                """
                INSERT INTO receipts(
                    thread_id, status, reservation_id, owner_token, producer,
                    payload_sha256, evidence_identity,
                    caller_requested_outer_tokens,
                    host_observed_requested_outer_tokens,
                    selected_result_token_limit, updated_at_unix
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attestation.thread_id,
                    reservation_id,
                    owner_token,
                    producer,
                    payload_sha256,
                    attestation.evidence_identity,
                    request.caller_requested_outer_tokens,
                    attestation.host_observed_requested_outer_tokens,
                    attestation.selected_result_token_limit,
                    now_unix,
                ),
            )
            connection.execute("COMMIT")
            return RecipeReservationResult(handle, "reserved")
        except sqlite3.IntegrityError:
            self._rollback(connection)
            return RecipeReservationResult(None, "host_call_replayed")
        except sqlite3.OperationalError as exc:
            self._rollback(connection)
            reason = "store_busy" if "locked" in str(exc).lower() else "store_operational_error"
            return RecipeReservationResult(None, reason)
        except sqlite3.Error:
            self._rollback(connection)
            return RecipeReservationResult(None, "store_error")
        finally:
            connection.close()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def commit(self, handle: RecipeReceiptHandle, *, now_unix: int) -> bool:
        connection = self._connect()
        if connection is None:
            return False
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE receipts SET status = 'committed', updated_at_unix = ?
                WHERE thread_id = ? AND reservation_id = ? AND owner_token = ?
                  AND status = 'pending'
                """,
                (
                    now_unix,
                    handle.thread_id,
                    handle.reservation_id,
                    handle.owner_token,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                return False
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            self._rollback(connection)
            return self._status_matches(handle, "committed")
        finally:
            connection.close()

    def abort(self, handle: RecipeReceiptHandle) -> bool:
        """Delete only the owner's pending receipt; consumed evidence remains durable."""
        connection = self._connect()
        if connection is None:
            return False
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM receipts
                WHERE thread_id = ? AND reservation_id = ? AND owner_token = ?
                  AND status = 'pending'
                """,
                (handle.thread_id, handle.reservation_id, handle.owner_token),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                return False
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            self._rollback(connection)
            return self._receipt_state_exact(handle) == "absent"
        finally:
            connection.close()

    def _status_matches(self, handle: RecipeReceiptHandle, expected: str) -> bool:
        connection = self._connect()
        if connection is None:
            return False
        try:
            row = connection.execute(
                """
                SELECT status FROM receipts
                WHERE thread_id = ? AND reservation_id = ? AND owner_token = ?
                """,
                (handle.thread_id, handle.reservation_id, handle.owner_token),
            ).fetchone()
            return row == (expected,)
        except sqlite3.Error:
            return False
        finally:
            connection.close()

    def _receipt_state_exact(self, handle: RecipeReceiptHandle) -> str:
        connection = self._connect()
        if connection is None:
            return "unknown"
        try:
            row = connection.execute(
                """
                SELECT status, reservation_id, owner_token FROM receipts
                WHERE thread_id = ?
                """,
                (handle.thread_id,),
            ).fetchone()
            if row is None:
                return "absent"
            if row[1:] != (handle.reservation_id, handle.owner_token):
                return "different_owner"
            return str(row[0])
        except sqlite3.Error:
            return "unknown"
        finally:
            connection.close()

    def receipt_status(self, thread_id: str) -> str | None:
        connection = self._connect()
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT status FROM receipts WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return row[0] if row is not None else None
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    def recover_stale_pending(self, *, thread_id: str, before_unix: int) -> bool:
        """Release a stale pending receipt while retaining consumed-call evidence."""
        connection = self._connect()
        if connection is None:
            return False
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM receipts
                WHERE thread_id = ? AND status = 'pending' AND updated_at_unix < ?
                """,
                (thread_id, before_unix),
            )
            connection.execute("COMMIT")
            return cursor.rowcount == 1
        except sqlite3.Error:
            self._rollback(connection)
            return False
        finally:
            connection.close()
