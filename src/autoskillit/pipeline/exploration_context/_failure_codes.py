"""Failure-code dispatch for the store's typed inner exception classes.

The :data:`EXPLORATION_STORE_FAILURE_CODES` table is built once at module load
and maps each inner exception class on :class:`OwnerBoundExplorationContextStore`
to a member of :class:`ExplorationFailureCode`.

**Immutability invariant (documented, not enforced).**  The dict captures the
current ``OwnerBoundExplorationContextStore.TrustedRootMismatch`` (and other
inner exception classes) as keys by class identity at module load.  If any
test or future refactor rebinds an inner exception class via
``monkeypatch.setattr(OwnerBoundExplorationContextStore, 'TrustedRootMismatch',
CustomException)``, the rebound attribute is raised but the dispatch table
still keys on the original class, so
:func:`resolve_exploration_store_failure_code` walks the rebound class's MRO
and reaches its ``AssertionError`` branch.  Inner exception classes are
immutable after module load.

The runtime import direction is ``_failure_codes → _store``: the store's
inner exception classes are reachable via the class reference, and the
dispatch table is built once at module load after ``_store.py`` finishes
defining the class.  ``_store.py`` never imports from ``_failure_codes.py``.
"""

from collections.abc import Mapping

from autoskillit.core import ExplorationFailureCode

from ._store import OwnerBoundExplorationContextStore

EXPLORATION_STORE_FAILURE_CODES: Mapping[type[BaseException], ExplorationFailureCode] = {
    OwnerBoundExplorationContextStore.TrustedRootMismatch: (
        ExplorationFailureCode.TRUSTED_ROOT_MISMATCH
    ),
    # InvalidSourceIdentity is unreachable from enable_exploration's own call
    # site (it always passes a well-formed interactive:<session_id> string) —
    # the store is the contract boundary this row proves, not that call site.
    OwnerBoundExplorationContextStore.InvalidSourceIdentity: (
        ExplorationFailureCode.INVALID_SOURCE_IDENTITY
    ),
    OwnerBoundExplorationContextStore.InvalidSessionBinding: (
        ExplorationFailureCode.SESSION_ID_INVALID
    ),
    OwnerBoundExplorationContextStore.ServiceNotConfigured: (
        ExplorationFailureCode.SERVICE_NOT_CONFIGURED
    ),
    OwnerBoundExplorationContextStore.SnapshotStale: ExplorationFailureCode.SNAPSHOT_STALE,
    OwnerBoundExplorationContextStore.SnapshotTruncated: (
        ExplorationFailureCode.SNAPSHOT_TRUNCATED
    ),
    OwnerBoundExplorationContextStore.SnapshotCaptureFailed: (
        ExplorationFailureCode.SNAPSHOT_CAPTURE_FAILED
    ),
    OwnerBoundExplorationContextStore.StoreClosed: ExplorationFailureCode.STORE_CLOSED,
    OwnerBoundExplorationContextStore.CapacityExceeded: ExplorationFailureCode.CAPACITY_EXCEEDED,
}


def resolve_exploration_store_failure_code(exc: BaseException) -> ExplorationFailureCode:
    """Resolve the nearest mapped ancestor's code by walking the MRO.

    ``except tuple(EXPLORATION_STORE_FAILURE_CODES)`` matches by
    ``isinstance``, so a subclass of a mapped store exception is caught even
    though it is not an exact key of the mapping. Walking the MRO instead of
    doing an exact-type lookup preserves the subclass-resolves-to-its-
    nearest-ancestor property without risking a ``KeyError`` from inside a
    caller's exception handler — which would escape a "Never raises" contract
    rather than being converted to a structured failure envelope.
    """
    for klass in type(exc).__mro__:
        if klass in EXPLORATION_STORE_FAILURE_CODES:
            return EXPLORATION_STORE_FAILURE_CODES[klass]
    raise AssertionError(
        f"{type(exc)!r} matched the exploration store-exception allowlist but has no "
        "mapped ancestor in EXPLORATION_STORE_FAILURE_CODES"
    )
