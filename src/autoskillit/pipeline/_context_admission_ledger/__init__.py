"""Crash-safe SQLite storage for shadow context-admission accounting.

The implementation is decomposed into cohesive shards. The public ledger is
the concrete end of the implementation hierarchy; store, recovery,
inspection, and accounting behavior are inherited from its private bases.
"""

from __future__ import annotations

from ._apply import _LedgerApply
from ._storage import SCHEMA_SQL, _LedgerOpenError

__all__ = [
    "DefaultContextAdmissionLedger",
    "SCHEMA_SQL",
    "_LedgerOpenError",
]


class DefaultContextAdmissionLedger(_LedgerApply):
    """SQLite-backed context-admission journal and verified projections."""
