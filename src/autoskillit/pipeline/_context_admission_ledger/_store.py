"""Store-side filesystem and SQLite connection authorities.

Owns the sidecar/parent/file-init concern: ``_ensure_store``,
``_recover_initialization_link``, ``_has_initialization_link``,
``_ensure_private_parent``, ``_validate_database_file``, ``_connect``, and
``_configure_connection``. Also owns the persisted-state validators
``_validate_integrity`` and ``_validate_metadata``. These are module-level
helpers rebound onto :class:`DefaultContextAdmissionLedger` from
``__init__.py``.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Final

from autoskillit.core import (
    CONTEXT_ADMISSION_ENCODING_VERSION,
    CONTEXT_ADMISSION_PROTOCOL_VERSION,
    ContextAdmissionStorageFailureReason,
    ContextAdmissionStorageHealthStatus,
)

from ._sqlite_errors import (
    _SQLITE_BUSY_CODES,
    _LedgerContended,
    _sqlite_primary_code,
)
from ._storage import (
    _LedgerOpenError,
    fsync_directory,
    fsync_file,
    reconcile_initialization_links,
    require_private_file_identity,
    unlink_initialization_artifact,
    validate_sidecars,
)

_SCHEMA_VERSION: Final = 1
_DATABASE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700

__all__ = [
    "_SCHEMA_VERSION",
    "_DATABASE_MODE",
    "_DIRECTORY_MODE",
    "_ensure_store",
    "_recover_initialization_link",
    "_has_initialization_link",
    "_ensure_private_parent",
    "_validate_database_file",
    "_validate_integrity",
    "_validate_metadata",
    "_connect",
    "_configure_connection",
]


def _ensure_store(self) -> None:
    self._ensure_private_parent()
    if self._path.exists():
        self._recover_initialization_link()
        self._validate_database_file()
        validate_sidecars(
            self._path,
            owner_id=self._authority.expected_owner_id,
            file_mode=_DATABASE_MODE,
            allow_regular=True,
        )
        return
    validate_sidecars(
        self._path,
        owner_id=self._authority.expected_owner_id,
        file_mode=_DATABASE_MODE,
        allow_regular=False,
    )
    temporary_path = self._path.parent / (f".{self._path.name}.{secrets.token_hex(12)}.tmp")
    from ._storage import SCHEMA_SQL  # local import avoids cycle with codec/status

    try:
        descriptor = os.open(
            temporary_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            _DATABASE_MODE,
        )
        os.close(descriptor)
        connection = self._configure_connection(temporary_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            metadata = {
                "schema_version": str(_SCHEMA_VERSION),
                "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
                "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
                "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                tuple(metadata.items()),
            )
            connection.execute("COMMIT")
        except BaseException:
            from ._sqlite_errors import _rollback

            _rollback(connection)
            raise
        finally:
            connection.close()
        os.chmod(temporary_path, _DATABASE_MODE)
        fsync_file(temporary_path)
        os.link(temporary_path, self._path, follow_symlinks=False)
        unlink_initialization_artifact(temporary_path)
        fsync_directory(self._path.parent)
        self._validate_database_file()
    except FileExistsError as exc:
        if self._path.exists():
            self._recover_initialization_link()
            self._validate_database_file()
            return
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            "store-publication-collision",
        ) from exc
    except _LedgerOpenError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IO,
            "store-initialization-failed",
        ) from exc
    finally:
        unlink_initialization_artifact(temporary_path)


def _recover_initialization_link(self) -> None:
    import time

    deadline = time.monotonic() + (self._busy_timeout_ms / 1_000)
    try:
        while self._has_initialization_link():
            if time.monotonic() < deadline:
                time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
                continue
            if reconcile_initialization_links(
                self._path,
                owner_id=self._authority.expected_owner_id,
                file_mode=_DATABASE_MODE,
                remove=True,
            ):
                fsync_directory(self._path.parent)
            return
    except (FileNotFoundError, NotADirectoryError):
        return
    except OSError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IO,
            "store-initialization-link-recovery-failed",
        ) from exc


def _has_initialization_link(self) -> bool:
    return reconcile_initialization_links(
        self._path,
        owner_id=self._authority.expected_owner_id,
        file_mode=_DATABASE_MODE,
        remove=False,
    )


def _ensure_private_parent(self) -> None:
    parent = self._path.parent
    trusted_parent = parent.parent
    try:
        trusted_stat = trusted_parent.lstat()
        if (
            not stat.S_ISDIR(trusted_stat.st_mode)
            or trusted_stat.st_uid != self._authority.expected_owner_id
            or trusted_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "untrusted-store-parent",
            )
        try:
            parent.mkdir(mode=_DIRECTORY_MODE)
            fsync_directory(trusted_parent)
        except FileExistsError:
            pass
        parent_stat = parent.lstat()
    except _LedgerOpenError:
        raise
    except OSError as exc:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.IO,
            "store-parent-unavailable",
        ) from exc
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != self._authority.expected_owner_id
        or stat.S_IMODE(parent_stat.st_mode) != _DIRECTORY_MODE
    ):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
            "insecure-store-parent",
        )


def _validate_database_file(self) -> tuple[int, int]:
    return require_private_file_identity(
        self._path,
        owner_id=self._authority.expected_owner_id,
        file_mode=_DATABASE_MODE,
        reason_code="insecure-store-file",
    )


def _validate_integrity(connection: sqlite3.Connection) -> None:
    """Static method bound onto DefaultContextAdmissionLedger."""
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if row != ("ok",):
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "sqlite-integrity-failed",
        )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _LedgerOpenError(
            ContextAdmissionStorageFailureReason.INTEGRITY,
            "sqlite-foreign-key-check-failed",
        )


def _validate_metadata(metadata: dict[str, str]) -> None:
    """Static method bound onto DefaultContextAdmissionLedger."""
    expected = {
        "schema_version": str(_SCHEMA_VERSION),
        "encoding_version": str(CONTEXT_ADMISSION_ENCODING_VERSION),
        "protocol_version": str(CONTEXT_ADMISSION_PROTOCOL_VERSION),
        "store_health": ContextAdmissionStorageHealthStatus.HEALTHY.value,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            reason = {
                "schema_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_SCHEMA,
                "encoding_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_ENCODING,
                "protocol_version": ContextAdmissionStorageFailureReason.UNSUPPORTED_PROTOCOL,
            }.get(key, ContextAdmissionStorageFailureReason.INTEGRITY)
            raise _LedgerOpenError(reason, f"invalid-{key.replace('_', '-')}")


def _connect(self) -> sqlite3.Connection:
    before = self._validate_database_file()
    validate_sidecars(
        self._path,
        owner_id=self._authority.expected_owner_id,
        file_mode=_DATABASE_MODE,
        allow_regular=True,
    )
    connection = self._configure_connection(self._path)
    try:
        after = self._validate_database_file()
        if before != after:
            raise _LedgerOpenError(
                ContextAdmissionStorageFailureReason.SECURITY_IDENTITY,
                "store-identity-changed",
            )
        return connection
    except BaseException:
        connection.close()
        raise


def _configure_connection(self, path: Path) -> sqlite3.Connection:
    try:
        connection = self._connection_factory(
            f"{path.as_uri()}?mode=rw",
            uri=True,
            timeout=self._busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        expected_pragmas = (
            ("journal_mode", "DELETE", ("delete",)),
            ("synchronous", "EXTRA", (3,)),
            ("foreign_keys", "ON", (1,)),
            ("busy_timeout", str(self._busy_timeout_ms), (self._busy_timeout_ms,)),
        )
        for name, value, expected in expected_pragmas:
            connection.execute(f"PRAGMA {name}={value}")
            row = connection.execute(f"PRAGMA {name}").fetchone()
            if row != expected:
                raise _LedgerOpenError(
                    ContextAdmissionStorageFailureReason.CONFIGURATION,
                    "sqlite-pragma-mismatch",
                )
        return connection
    except _LedgerOpenError:
        raise
    except sqlite3.Error as exc:
        if _sqlite_primary_code(exc) in _SQLITE_BUSY_CODES:
            raise _LedgerContended from exc
        raise
