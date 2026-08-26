"""Failure-code dispatch for the store's typed inner exception classes."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from autoskillit.core import ExplorationFailureCode

from ._store import OwnerBoundExplorationContextStore

EXPLORATION_STORE_FAILURE_CODES: Mapping[type[BaseException], ExplorationFailureCode] = (
    MappingProxyType(
        {
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
            OwnerBoundExplorationContextStore.CapacityExceeded: (
                ExplorationFailureCode.CAPACITY_EXCEEDED
            ),
        }
    )
)


def resolve_exploration_store_failure_code(exc: BaseException) -> ExplorationFailureCode:
    """Resolve the nearest mapped ancestor's code by walking the MRO."""
    for klass in type(exc).__mro__:
        if klass in EXPLORATION_STORE_FAILURE_CODES:
            return EXPLORATION_STORE_FAILURE_CODES[klass]
    raise AssertionError(
        f"{type(exc)!r} matched the exploration store-exception allowlist but has no "
        "mapped ancestor in EXPLORATION_STORE_FAILURE_CODES"
    )
