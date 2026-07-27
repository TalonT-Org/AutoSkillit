"""Filesystem and bounded SQLite primitives for context-admission storage."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionValidationError,
    context_admission_envelope_header,
    context_admission_reducer_for_protocol,
    decode_stored_context_admission_envelope,
)

SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE streams (
    stream_id BLOB PRIMARY KEY,
    stream_key BLOB NOT NULL UNIQUE,
    genesis_envelope BLOB NOT NULL,
    state_envelope BLOB NOT NULL,
    aggregate_revision INTEGER NOT NULL,
    admission_sequence INTEGER NOT NULL,
    latest_journal_sequence INTEGER NOT NULL,
    health_status TEXT NOT NULL,
    failure_reason TEXT,
    reason_code TEXT
) STRICT;
CREATE TABLE journal_events (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    event_envelope BLOB NOT NULL,
    decision_envelope BLOB NOT NULL,
    expected_revision INTEGER NOT NULL,
    prior_aggregate_revision INTEGER NOT NULL,
    prior_admission_sequence INTEGER NOT NULL,
    resulting_aggregate_revision INTEGER NOT NULL,
    resulting_admission_sequence INTEGER NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    UNIQUE (stream_id, event_id),
    FOREIGN KEY (stream_id) REFERENCES streams(stream_id)
) STRICT;
CREATE TABLE effect_outbox (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    effect_ordinal INTEGER NOT NULL,
    effect_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence, effect_ordinal),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
CREATE TABLE shadow_decisions (
    stream_id BLOB NOT NULL,
    journal_sequence INTEGER NOT NULL,
    shadow_envelope BLOB NOT NULL,
    PRIMARY KEY (stream_id, journal_sequence),
    FOREIGN KEY (stream_id, journal_sequence)
        REFERENCES journal_events(stream_id, journal_sequence)
) STRICT;
"""


class _LedgerOpenError(RuntimeError):
    def __init__(
        self,
        reason: ContextAdmissionStorageFailureReason,
        reason_code: str,
    ) -> None:
        super().__init__(reason_code)
        self.reason = reason
        self.reason_code = reason_code


class _LedgerReadBudget:
    __slots__ = ("_bytes", "_max_bytes", "_max_rows", "_reason_code", "_rows")

    def __init__(
        self,
        reason_code: str,
        *,
        max_rows: int,
        max_bytes: int,
    ) -> None:
        self._rows = 0
        self._bytes = 0
        self._reason_code = reason_code
        self._max_rows = max_rows
        self._max_bytes = max_bytes

    def consume(self, row: tuple[Any, ...]) -> tuple[Any, ...]:
        self._rows += 1
        self._bytes += sum(
            len(value)
            if isinstance(value, bytes | bytearray | memoryview)
            else len(value.encode("utf-8"))
            if isinstance(value, str)
            else 0
            for value in row
        )
        if self._rows > self._max_rows or self._bytes > self._max_bytes:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.INTEGRITY,
                self._reason_code,
            )
        return row


def _read_bounded_rows(
    cursor: sqlite3.Cursor,
    budget: _LedgerReadBudget,
) -> tuple[tuple[Any, ...], ...]:
    return tuple(budget.consume(cast(tuple[Any, ...], row)) for row in cursor)


def _preflight_storage_routes(
    connection: sqlite3.Connection,
    read_budget: _LedgerReadBudget,
) -> None:
    queries = (
        "SELECT genesis_envelope FROM streams",
        "SELECT state_envelope FROM streams",
        "SELECT event_envelope FROM journal_events",
        "SELECT decision_envelope FROM journal_events",
        "SELECT effect_envelope FROM effect_outbox",
        "SELECT shadow_envelope FROM shadow_decisions",
    )
    for query in queries:
        for (encoded,) in _read_bounded_rows(connection.execute(query), read_budget):
            encoded_bytes = bytes(encoded)
            encoding_version, protocol_version, discriminator = _envelope_header(encoded_bytes)
            if encoding_version != CONTEXT_ADMISSION_ENCODING_VERSION:
                try:
                    envelope = decode_stored_context_admission_envelope(encoded_bytes)
                except ContextAdmissionValidationError as exc:
                    raise _LedgerOpenError(
                        ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                        "unsupported-envelope-encoding",
                    ) from exc
                protocol_version = envelope.protocol_version
                discriminator = envelope.type_discriminator
            if discriminator not in CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                    "unsupported-envelope-discriminator",
                )
            try:
                context_admission_reducer_for_protocol(protocol_version)
            except ContextAdmissionValidationError as exc:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
                    "unsupported-envelope-protocol",
                ) from exc


def _envelope_header(value: bytes) -> tuple[int, int, str]:
    try:
        return context_admission_envelope_header(value)
    except ContextAdmissionValidationError:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "invalid-envelope-header",
        ) from None


def reconcile_initialization_links(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
    remove: bool,
) -> bool:
    """Find, and optionally remove, same-inode initialization link artifacts."""
    directory_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        database_stat = os.stat(
            path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(database_stat.st_mode)
            or database_stat.st_uid != owner_id
            or stat.S_IMODE(database_stat.st_mode) != file_mode
            or database_stat.st_nlink <= 1
        ):
            return False
        prefix = f".{path.name}."
        suffix = ".tmp"
        found = False
        for name in os.listdir(directory_fd):
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            token = name[len(prefix) : -len(suffix)]
            if len(token) != 24 or any(character not in "0123456789abcdef" for character in token):
                continue
            try:
                candidate_stat = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            if (
                stat.S_ISREG(candidate_stat.st_mode)
                and candidate_stat.st_dev == database_stat.st_dev
                and candidate_stat.st_ino == database_stat.st_ino
                and candidate_stat.st_uid == owner_id
                and stat.S_IMODE(candidate_stat.st_mode) == file_mode
            ):
                found = True
                if remove:
                    os.unlink(name, dir_fd=directory_fd)
        return found
    finally:
        os.close(directory_fd)


def private_file_identity(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
) -> tuple[int, int] | None:
    """Return a stable private-file identity, or None when validation fails."""
    path_stat = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_stat = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_uid != owner_id
        or stat.S_IMODE(path_stat.st_mode) != file_mode
        or path_stat.st_nlink != 1
        or (path_stat.st_dev, path_stat.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino)
    ):
        return None
    return path_stat.st_dev, path_stat.st_ino


def require_private_file_identity(
    path: Path,
    *,
    owner_id: int,
    file_mode: int,
    reason_code: str,
) -> tuple[int, int]:
    try:
        identity = private_file_identity(
            path,
            owner_id=owner_id,
            file_mode=file_mode,
        )
    except OSError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        ) from exc
    if identity is None:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            reason_code,
        )
    return identity


def validate_sidecars(
    database_path: Path,
    *,
    owner_id: int,
    file_mode: int,
    allow_regular: bool,
) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.IO,
                "store-sidecar-unavailable",
            ) from exc
        if not allow_regular:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "orphan-store-sidecar",
            )
        try:
            require_private_file_identity(
                sidecar,
                owner_id=owner_id,
                file_mode=file_mode,
                reason_code="insecure-store-sidecar",
            )
        except _LedgerOpenError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                continue
            raise


def fsync_file(path: Path) -> None:
    """Synchronize one no-follow regular file descriptor."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    """Synchronize one no-follow directory descriptor."""
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def unlink_initialization_artifact(path: Path) -> None:
    """Best-effort cleanup for an unpublished temporary database and journal."""
    for candidate in (path, Path(f"{path}-journal")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
