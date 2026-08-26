"""SQLite error-classification primitives.

Owns the busy/locked code masks, the ``_sqlite_primary_code`` classifier, the
``_rollback`` cleanup helper, and the ``_LedgerContended`` exception type used
to signal a recoverable SQLite contention to the ledger's apply/recovery loop.

Split out of ``_status`` to break the cross-shard lazy-import cycle that the
Wavefront 1 decomposition introduced: ``_store._configure_connection`` and
``_status`` both need these symbols, and co-locating them in ``_status`` forced
mid-function imports. The new shard has no internal dependents, so every
consumer can import at module top.

Wavefront 1 of #4667.
"""

from __future__ import annotations

import sqlite3
from typing import Final

from autoskillit.core import get_logger

logger = get_logger(__name__)

_SQLITE_PRIMARY_MASK: Final = 0xFF
_SQLITE_BUSY_CODES: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})
_SQLITE_RECOVERY_CODES: Final = frozenset(
    {
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_INTERRUPT,
        sqlite3.SQLITE_NOMEM,
    }
)


class _LedgerContended(RuntimeError):
    pass


def _rollback(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error as exc:
        logger.debug("context-admission rollback failed: %s", exc)


def _sqlite_primary_code(error: sqlite3.Error) -> int | None:
    code = getattr(error, "sqlite_errorcode", None)
    return code & _SQLITE_PRIMARY_MASK if isinstance(code, int) else None


__all__ = [
    "_SQLITE_PRIMARY_MASK",
    "_SQLITE_BUSY_CODES",
    "_SQLITE_RECOVERY_CODES",
    "_LedgerContended",
    "_rollback",
    "_sqlite_primary_code",
]
