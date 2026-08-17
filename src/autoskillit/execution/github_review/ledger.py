"""Private crash-safe SQLite authority for review publication."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

from autoskillit.core import (
    GitHubReviewReceipt,
    ReviewOperationState,
    fsync_directory,
    private_file_identity,
    private_sidecar_issue,
    publish_private_file,
    reconcile_initialization_links,
    review_receipt_validation_error,
    unlink_sqlite_initialization_artifacts,
)
from ._ledger_schema import (  # noqa: F401 — re-exported for sibling modules
    MutationSlot,
    ReviewAttemptRecord,
    ReviewOperationRecord,
    _DIRECTORY_MODE,
    _DATABASE_MODE,
    _SCHEMA,
    _SCHEMA_VERSION,
)

class GitHubReviewLedger:
    """Durable operation, attempt, receipt, and cross-process pacing authority."""

    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 0,
        expected_owner_id: int | None = None,
    ) -> None:
        if not database_path.is_absolute():
            raise ValueError("review ledger path must be absolute")
        self.database_path = database_path
        self.busy_timeout_ms = max(0, busy_timeout_ms)
        self.expected_owner_id = os.getuid() if expected_owner_id is None else expected_owner_id

    def initialize(self) -> None:
        self._ensure_private_parent()
        if self.database_path.exists():
            self._recover_initialization_link()
            self._validate_database_file()
            self._validate_sidecars(allow_regular=True)
            self._validate_metadata()
            return
        self._validate_sidecars(allow_regular=False)
        temporary_path = self.database_path.parent / (
            f".{self.database_path.name}.{secrets.token_hex(12)}.tmp"
        )
        try:
            descriptor = os.open(
                temporary_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                _DATABASE_MODE,
            )
            os.close(descriptor)
            connection = self._configure_connection(temporary_path)
            try:
                connection.executescript(_SCHEMA)
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES(?, ?)",
                    (
                        ("schema_version", str(_SCHEMA_VERSION)),
                        ("scope_salt", secrets.token_hex(32)),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            os.chmod(temporary_path, _DATABASE_MODE)
            publish_private_file(temporary_path, self.database_path)
            self._validate_database_file()
            self._validate_metadata()
        except FileExistsError:
            if not self.database_path.exists():
                raise ValueError("review ledger publication collision")
            self._recover_initialization_link()
            self._validate_database_file()
            self._validate_metadata()
        finally:
            unlink_sqlite_initialization_artifacts(temporary_path)

    def prepare(
        self,
        *,
        operation_key: str,
        request_digest: str,
        request_json: bytes,
        findings: tuple[tuple[int, int, str, bytes], ...] = (),
    ) -> ReviewOperationState:
        self.initialize()
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_digest, request_json, state FROM operations "
                "WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        operation_key,
                        request_digest,
                        request_json,
                        ReviewOperationState.PREPARED.value,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    "INSERT INTO operation_findings VALUES (?, ?, ?, ?, ?)",
                    (
                        (operation_key, canonical_index, original_index, digest, payload)
                        for canonical_index, original_index, digest, payload in findings
                    ),
                )
                return ReviewOperationState.PREPARED
            if row[0] != request_digest or bytes(row[1]) != request_json:
                raise ValueError("review operation identity conflicts with persisted request")
            persisted = connection.execute(
                "SELECT canonical_index, original_index, finding_digest, payload_json "
                "FROM operation_findings WHERE operation_key = ? ORDER BY canonical_index",
                (operation_key,),
            ).fetchall()
            expected = [
                (canonical_index, original_index, digest, payload)
                for canonical_index, original_index, digest, payload in findings
            ]
            actual = [
                (int(item[0]), int(item[1]), str(item[2]), bytes(item[3])) for item in persisted
            ]
            if actual != expected:
                raise ValueError("review operation findings conflict with persisted request")
            return ReviewOperationState(row[2])

    def load_operation(self, operation_key: str) -> ReviewOperationRecord | None:
        if not self.database_path.exists():
            return None
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_digest, request_json, state, created_at, updated_at "
                "FROM operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
        if row is None:
            return None
        return ReviewOperationRecord(
            operation_key=operation_key,
            request_digest=str(row[0]),
            request_json=bytes(row[1]),
            state=ReviewOperationState(row[2]),
            created_at=float(row[3]),
            updated_at=float(row[4]),
        )

    def load_state(self, operation_key: str) -> ReviewOperationState | None:
        operation = self.load_operation(operation_key)
        return None if operation is None else operation.state

    def claim_operation(self, operation_key: str) -> bool:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? "
                "WHERE operation_key = ? AND state = ?",
                (
                    ReviewOperationState.POSTING.value,
                    time.time(),
                    operation_key,
                    ReviewOperationState.PREPARED.value,
                ),
            )
            return cursor.rowcount == 1

    def set_operation_state(
        self,
        operation_key: str,
        state: ReviewOperationState,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ?",
                (state.value, time.time(), operation_key),
            )

    def begin_attempt(
        self,
        *,
        operation_key: str,
        attempt_number: int,
        attempt_digest: str,
        payload_json: bytes,
        canonical_indexes: tuple[int, ...],
        omitted_dispositions: tuple[GitHubReviewFindingDisposition, ...],
        effective_event: str,
        effective_body_digest: str,
    ) -> ReviewAttemptRecord:
        now = time.time()
        indexes_json = json.dumps(canonical_indexes, separators=(",", ":")).encode()
        omitted_json = _dispositions_json(omitted_dispositions)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempt_digest, payload_json, canonical_indexes_json, "
                "omitted_dispositions_json, effective_event, effective_body_digest, "
                "state, response_class, "
                "status_code, error, created_at, updated_at FROM attempts "
                "WHERE operation_key = ? AND attempt_number = ?",
                (operation_key, attempt_number),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_key,
                        attempt_number,
                        attempt_digest,
                        payload_json,
                        indexes_json,
                        omitted_json,
                        effective_event,
                        effective_body_digest,
                        ReviewOperationState.POSTING.value,
                        ReviewResponseClass.NONE.value,
                        None,
                        None,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ?",
                    (ReviewOperationState.POSTING.value, now, operation_key),
                )
                return ReviewAttemptRecord(
                    operation_key=operation_key,
                    attempt_number=attempt_number,
                    attempt_digest=attempt_digest,
                    payload_json=payload_json,
                    canonical_indexes=canonical_indexes,
                    omitted_dispositions=omitted_dispositions,
                    effective_event=effective_event,
                    effective_body_digest=effective_body_digest,
                    state=ReviewOperationState.POSTING.value,
                    response_class=ReviewResponseClass.NONE,
                    status_code=None,
                    error=None,
                    created_at=now,
                    updated_at=now,
                )
            immutable = (
                str(row[0]),
                bytes(row[1]),
                bytes(row[2]),
                bytes(row[3]),
                str(row[4]),
                str(row[5]),
            )
            expected = (
                attempt_digest,
                payload_json,
                indexes_json,
                omitted_json,
                effective_event,
                effective_body_digest,
            )
            if immutable != expected:
                raise ValueError("review attempt conflicts with persisted payload")
            return _attempt_from_row(operation_key, attempt_number, row)

    def complete_attempt(
        self,
        *,
        operation_key: str,
        attempt_number: int,
        state: ReviewOperationState,
        response_class: ReviewResponseClass,
        status_code: int | None,
        error: str | None,
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE attempts SET state = ?, response_class = ?, status_code = ?, "
                "error = ?, updated_at = ? WHERE operation_key = ? AND attempt_number = ?",
                (
                    state.value,
                    response_class.value,
                    status_code,
                    error,
                    now,
                    operation_key,
                    attempt_number,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("review attempt was not persisted before completion")
            connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ?",
                (state.value, now, operation_key),
            )

    def complete_attempt_and_schedule_retry(
        self,
        *,
        operation_key: str,
        completed_attempt_number: int,
        response_class: ReviewResponseClass,
        status_code: int | None,
        error: str | None,
        retry_attempt_number: int,
        retry_attempt_digest: str,
        retry_payload_json: bytes,
        retry_canonical_indexes: tuple[int, ...],
        retry_omitted_dispositions: tuple[GitHubReviewFindingDisposition, ...],
        retry_effective_event: str,
        retry_effective_body_digest: str,
    ) -> None:
        """Atomically finish one attempt and persist the exact retry intent."""

        if retry_attempt_number <= completed_attempt_number:
            raise ValueError("review retry attempt number must advance")
        now = time.time()
        indexes_json = json.dumps(retry_canonical_indexes, separators=(",", ":")).encode()
        omitted_json = _dispositions_json(retry_omitted_dispositions)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            completed = connection.execute(
                "UPDATE attempts SET state = ?, response_class = ?, status_code = ?, "
                "error = ?, updated_at = ? WHERE operation_key = ? AND attempt_number = ? "
                "AND state = ?",
                (
                    ReviewOperationState.TERMINAL.value,
                    response_class.value,
                    status_code,
                    error,
                    now,
                    operation_key,
                    completed_attempt_number,
                    ReviewOperationState.POSTING.value,
                ),
            )
            if completed.rowcount != 1:
                raise ValueError("review attempt was not posting before retry scheduling")
            connection.execute(
                "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_key,
                    retry_attempt_number,
                    retry_attempt_digest,
                    retry_payload_json,
                    indexes_json,
                    omitted_json,
                    retry_effective_event,
                    retry_effective_body_digest,
                    ReviewOperationState.RETRY_PENDING.value,
                    ReviewResponseClass.NONE.value,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            operation = connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ? "
                "AND state = ?",
                (
                    ReviewOperationState.RETRY_PENDING.value,
                    now,
                    operation_key,
                    ReviewOperationState.POSTING.value,
                ),
            )
            if operation.rowcount != 1:
                raise ValueError("review operation was not posting before retry scheduling")

    def claim_retry_attempt(self, *, operation_key: str, attempt_number: int) -> bool:
        """Atomically claim a persisted retry before any network mutation."""

        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = connection.execute(
                "UPDATE attempts SET state = ?, updated_at = ? WHERE operation_key = ? "
                "AND attempt_number = ? AND state = ?",
                (
                    ReviewOperationState.POSTING.value,
                    now,
                    operation_key,
                    attempt_number,
                    ReviewOperationState.RETRY_PENDING.value,
                ),
            )
            if attempt.rowcount != 1:
                return False
            operation = connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ? "
                "AND state = ?",
                (
                    ReviewOperationState.POSTING.value,
                    now,
                    operation_key,
                    ReviewOperationState.RETRY_PENDING.value,
                ),
            )
            if operation.rowcount != 1:
                raise ValueError("persisted review retry has inconsistent operation state")
            return True

    def load_attempts(self, operation_key: str) -> tuple[ReviewAttemptRecord, ...]:
        if not self.database_path.exists():
            return ()
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT attempt_number, attempt_digest, payload_json, "
                "canonical_indexes_json, omitted_dispositions_json, effective_event, "
                "effective_body_digest, "
                "state, response_class, status_code, error, created_at, updated_at "
                "FROM attempts WHERE operation_key = ? ORDER BY attempt_number",
                (operation_key,),
            ).fetchall()
        return tuple(_attempt_from_row(operation_key, int(row[0]), row[1:]) for row in rows)

    def save_receipt(self, receipt: GitHubReviewReceipt) -> None:
        validation_error = review_receipt_validation_error(
            receipt.to_dict(),
            operation_key=receipt.operation_key,
            repository=receipt.repository,
            pr_number=receipt.pr_number,
            head_sha=receipt.head_sha,
            logical_iteration=receipt.logical_iteration,
            post_state=receipt.state.value,
        )
        if validation_error is not None:
            raise ValueError(f"review receipt is invalid: {validation_error}")
        wire = json.dumps(
            receipt.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if not receipt.final_attempt_digest:
            raise ValueError("final review receipt must identify its exact attempt")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE attempts SET state = ?, updated_at = ? "
                "WHERE operation_key = ? AND attempt_digest = ?",
                (
                    receipt.state.value,
                    time.time(),
                    receipt.operation_key,
                    receipt.final_attempt_digest,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("final review receipt does not match one persisted attempt")
            connection.execute(
                "INSERT INTO receipts VALUES (?, ?, ?) "
                "ON CONFLICT(operation_key) DO UPDATE SET receipt_json = excluded.receipt_json",
                (receipt.operation_key, wire, time.time()),
            )
            connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_key = ?",
                (receipt.state.value, time.time(), receipt.operation_key),
            )

    def load_receipt(self, operation_key: str) -> GitHubReviewReceipt | None:
        if not self.database_path.exists():
            return None
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_json FROM receipts WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
        if row is None:
            return None
        return GitHubReviewReceipt.from_wire(json.loads(bytes(row[0])))

    def rate_scope_id(self, *, credential: str, api_origin: str) -> str:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'scope_salt'"
            ).fetchone()
        if row is None:
            raise ValueError("review ledger scope salt is missing")
        salt = bytes.fromhex(str(row[0]))
        material = f"{api_origin}\0{credential}".encode()
        return hmac.new(
            salt,
            b"autoskillit:github-review-rate-scope:v1\0" + material,
            hashlib.sha256,
        ).hexdigest()

    def claim_mutation_slot(
        self,
        *,
        scope_id: str,
        lease_owner: str,
        operation_key: str,
        now: float,
        minimum_interval_seconds: float,
        lease_ttl_seconds: float,
    ) -> MutationSlot:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_owner, lease_generation, lease_expires_at, "
                "next_mutation_not_before, backoff_until, in_flight_operation_key "
                "FROM rate_scopes WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO rate_scopes(scope_id) VALUES (?)",
                    (scope_id,),
                )
                row = (None, 0, 0.0, 0.0, 0.0, None)
            current_owner = row[0]
            generation = int(row[1])
            lease_expires_at = float(row[2])
            next_not_before = float(row[3])
            backoff_until = float(row[4])
            in_flight = row[5]
            if in_flight is not None and in_flight != operation_key:
                return MutationSlot(
                    ready=False,
                    delay=0.0,
                    lease_owner=lease_owner,
                    lease_generation=generation,
                    blocked_operation_key=str(in_flight),
                )
            if (
                current_owner is not None
                and current_owner != lease_owner
                and lease_expires_at > now
            ):
                return MutationSlot(
                    ready=False,
                    delay=lease_expires_at - now,
                    lease_owner=lease_owner,
                    lease_generation=generation,
                )
            if current_owner != lease_owner or lease_expires_at <= now:
                generation += 1
            delay = max(0.0, next_not_before - now, backoff_until - now)
            lease_expires_at = now + lease_ttl_seconds
            if delay > 0:
                connection.execute(
                    "UPDATE rate_scopes SET lease_owner = ?, lease_generation = ?, "
                    "lease_expires_at = ? WHERE scope_id = ?",
                    (lease_owner, generation, lease_expires_at, scope_id),
                )
                return MutationSlot(
                    ready=False,
                    delay=delay,
                    lease_owner=lease_owner,
                    lease_generation=generation,
                )
            connection.execute(
                "UPDATE rate_scopes SET lease_owner = ?, lease_generation = ?, "
                "lease_expires_at = ?, next_mutation_not_before = ?, "
                "in_flight_operation_key = ? WHERE scope_id = ?",
                (
                    lease_owner,
                    generation,
                    lease_expires_at,
                    now + minimum_interval_seconds,
                    operation_key,
                    scope_id,
                ),
            )
            return MutationSlot(
                ready=True,
                delay=0.0,
                lease_owner=lease_owner,
                lease_generation=generation,
            )

    def finish_mutation(
        self,
        *,
        scope_id: str,
        lease_owner: str,
        lease_generation: int,
        operation_key: str,
        keep_in_flight: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE rate_scopes SET lease_owner = NULL, lease_expires_at = 0, "
                "in_flight_operation_key = CASE WHEN ? THEN in_flight_operation_key "
                "ELSE NULL END WHERE scope_id = ? AND lease_owner = ? "
                "AND lease_generation = ? AND in_flight_operation_key = ?",
                (
                    int(keep_in_flight),
                    scope_id,
                    lease_owner,
                    lease_generation,
                    operation_key,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("review mutation lease was lost before release")

    def set_backoff(self, *, scope_id: str, until: float) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO rate_scopes(scope_id, backoff_until) VALUES (?, ?) "
                "ON CONFLICT(scope_id) DO UPDATE SET "
                "backoff_until = MAX(rate_scopes.backoff_until, excluded.backoff_until)",
                (scope_id, until),
            )

    def _ensure_private_parent(self) -> None:
        parent = self.database_path.parent
        try:
            parent.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
            parent_stat = parent.lstat()
        except OSError as exc:
            raise ValueError("review ledger directory is unavailable") from exc
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != self.expected_owner_id
            or stat.S_IMODE(parent_stat.st_mode) != _DIRECTORY_MODE
        ):
            raise ValueError("unsafe review ledger directory identity")

    def _validate_database_file(self) -> tuple[int, int]:
        try:
            identity = private_file_identity(
                self.database_path,
                owner_id=self.expected_owner_id,
                file_mode=_DATABASE_MODE,
            )
        except OSError as exc:
            raise ValueError("review ledger file identity is unavailable") from exc
        if identity is None:
            raise ValueError("unsafe review ledger file identity")
        return identity

    def _validate_sidecars(self, *, allow_regular: bool) -> None:
        issue = private_sidecar_issue(
            self.database_path,
            owner_id=self.expected_owner_id,
            file_mode=_DATABASE_MODE,
            allow_regular=allow_regular,
        )
        if issue is not None:
            raise ValueError(f"unsafe review ledger sidecar: {issue.kind}")

    def _recover_initialization_link(self) -> None:
        try:
            if reconcile_initialization_links(
                self.database_path,
                owner_id=self.expected_owner_id,
                file_mode=_DATABASE_MODE,
                remove=True,
            ):
                fsync_directory(self.database_path.parent)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("review ledger initialization recovery failed") from exc

    def _connect(self) -> sqlite3.Connection:
        before = self._validate_database_file()
        self._validate_sidecars(allow_regular=True)
        connection = self._configure_connection(self.database_path)
        try:
            after = self._validate_database_file()
            if before != after:
                raise ValueError("review ledger identity changed while opening")
            return connection
        except BaseException:
            connection.close()
            raise

    def _configure_connection(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=rw",
            uri=True,
            timeout=self.busy_timeout_ms / 1000,
        )
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=EXTRA")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    def _validate_metadata(self) -> None:
        with self._connect() as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if metadata.get("schema_version") != str(_SCHEMA_VERSION):
            raise ValueError("unsupported review ledger schema version")
        salt = metadata.get("scope_salt", "")
        if len(salt) != 64:
            raise ValueError("review ledger scope salt is invalid")
        bytes.fromhex(salt)


def _attempt_from_row(
    operation_key: str,
    attempt_number: int,
    row: tuple[Any, ...] | sqlite3.Row,
) -> ReviewAttemptRecord:
    return ReviewAttemptRecord(
        operation_key=operation_key,
        attempt_number=attempt_number,
        attempt_digest=str(row[0]),
        payload_json=bytes(row[1]),
        canonical_indexes=tuple(int(item) for item in json.loads(bytes(row[2]))),
        omitted_dispositions=_dispositions_from_wire(json.loads(bytes(row[3]))),
        effective_event=str(row[4]),
        effective_body_digest=str(row[5]),
        state=str(row[6]),
        response_class=ReviewResponseClass(row[7]),
        status_code=None if row[8] is None else int(row[8]),
        error=None if row[9] is None else str(row[9]),
        created_at=float(row[10]),
        updated_at=float(row[11]),
    )


def _dispositions_from_wire(data: object) -> tuple[GitHubReviewFindingDisposition, ...]:
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise TypeError("persisted finding dispositions must be a list of objects")
    return tuple(GitHubReviewFindingDisposition.from_wire(item) for item in data)


def _dispositions_json(
    dispositions: tuple[GitHubReviewFindingDisposition, ...],
) -> bytes:
    return json.dumps(
        [
            {
                "canonical_index": item.canonical_index,
                "kind": item.kind.value,
                "original_index": item.original_index,
                "reason": item.reason,
                "remote_comment_id": item.remote_comment_id,
            }
            for item in dispositions
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
