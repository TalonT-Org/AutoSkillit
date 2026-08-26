"""Launch-environment adaptation helpers — pure functions of the store.

The three helpers in this shard convert the original
``OwnerBoundExplorationContextStore`` classmethods/instance method into
module-level pure functions that take a store reference and return
either a value (``verified_repository_root_from_launch_environment``,
``_shared_source_identity``) or mutate the store's state under its
private lock (``reopen_launch_environment``).

Lock acquisition order is preserved exactly: file I/O via
``load_from_environment()`` runs BEFORE ``store._lock`` is acquired.  The
lock covers state mutation only.  This ordering is preserved verbatim
from the pre-decomposition ``_reopen_launch_environment`` method.

This is the only runtime cross-shard call path besides the
``_failure_codes → _store`` direction.  The store imports this module
as ``launch_adapter`` so it can call the helpers via
``launch_adapter.reopen_launch_environment(self, ...)`` from its
one-line delegate methods.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from autoskillit.core import canonical_json_bytes, get_logger
from autoskillit.pipeline.exploration_context_durable import (
    EXPLORATION_PRINCIPAL_ROLE,
    _ExplorationLaunchAuthorityStore,
    _ReopenedLaunchAuthority,
)

from ._constants import _SHARED_SOURCE_IDENTITY_DOMAIN
from ._types import _CapabilityLease

logger = get_logger(__name__)


def verified_repository_root_from_launch_environment() -> Path | None:
    """Recover only the HMAC-verified root needed to bootstrap an explorer store.

    Takes no ``store`` parameter: this is called from
    ``server/_factory.make_context`` BEFORE any
    :class:`OwnerBoundExplorationContextStore` exists, so it cannot
    depend on a store instance.  The durable store is fully static.
    """
    reopened = _ExplorationLaunchAuthorityStore().load_from_environment()
    if reopened is None:
        return None
    _capability, authority = reopened
    return authority.repository_root


def _shared_source_identity(source_identities: Mapping[str, str]) -> str:
    """Bind one principal to the complete current explorer definition set."""
    records = [
        {"role": role, "source_identity": source_identities[role]}
        for role in sorted(source_identities)
    ]
    digest = hashlib.sha256(
        _SHARED_SOURCE_IDENTITY_DOMAIN + canonical_json_bytes(records)
    ).hexdigest()
    return f"sha256:{digest}"


def reopen_launch_environment(
    store: OwnerBoundExplorationContextStore,  # type: ignore[name-defined]  # noqa: F821
    *,
    max_ttl_seconds: float,
    clock: Callable[[], float],
) -> tuple[str, _ReopenedLaunchAuthority] | None:
    """Reopen only a current, verifier-matched durable child authority.

    The body mirrors the pre-decomposition ``_reopen_launch_environment``
    instance method verbatim: file I/O via
    ``store._launch_authorities.load_from_environment()`` happens before
    ``store._lock`` is acquired; only the state mutation runs inside
    the lock.

    ``max_ttl_seconds`` and ``clock`` are passed explicitly so the
    helper does not reach into the store's instance attributes beyond
    the named list.  The helper does construct a
    :class:`_CapabilityLease` and insert it into ``store._leases`` —
    that is a side effect on the store, not a return value.
    """
    # File I/O runs BEFORE the lock is acquired (load-outside-lock).
    reopened = store._launch_authorities.load_from_environment()
    if reopened is None:
        return None
    capability, authority = reopened
    with store._lock:
        if store._closed or authority.repository_root != store._trusted_root:
            return None
        current = store._resolve_capability_locked(capability)
        expected = (
            authority.session_id,
            authority.cwd,
            authority.repository_root,
            authority.source_identity,
            authority.snapshot_digest,
        )
        if (
            current is not None
            and (
                current.session_id,
                current.cwd,
                current.repository_root,
                current.source_identity,
                current.snapshot_digest,
            )
            == expected
            and current.role == EXPLORATION_PRINCIPAL_ROLE
        ):
            return reopened
        if current is not None:
            store._discard_locked(capability)
        if len(store._leases) >= store._max_active_leases:
            store._cleanup_expired_locked()
        if len(store._leases) >= store._max_active_leases:
            return None
        store._leases[capability] = _CapabilityLease(
            owner_id="launch-authority",
            role=EXPLORATION_PRINCIPAL_ROLE,
            session_id=authority.session_id,
            expires_at=clock()
            + min(max_ttl_seconds, max(0.0, authority.expires_at - time.time())),
            value=None,  # type: ignore[arg-type]
            origin="launch",
            cwd=authority.cwd,
            repository_root=store._trusted_root,
            source_identity=authority.source_identity,
            snapshot_digest=authority.snapshot_digest,
        )
        store._session_capabilities.setdefault(authority.session_id, set()).add(capability)
        return reopened
