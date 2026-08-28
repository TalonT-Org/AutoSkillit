"""Adapt durable launch authority to Store-owned exploration operations.

State mutation occurs only while ``reopen_launch_environment`` holds the Store lock.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from autoskillit.core import (
    CapabilityResolutionStatus,
    ContinuationCursor,
    EvidencePage,
    ExplorationQuerySpec,
    canonical_json_bytes,
    get_logger,
)
from autoskillit.pipeline.exploration_context_durable import (
    EXPLORATION_PRINCIPAL_ROLE,
    _ExplorationLaunchAuthorityStore,
    _ReopenedLaunchAuthority,
    _safe_submit_failure_reason,
)

from ._constants import _SHARED_SOURCE_IDENTITY_DOMAIN
from ._types import _CapabilityLease

if TYPE_CHECKING:
    from autoskillit.pipeline.exploration_context._store import OwnerBoundExplorationContextStore

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
    store: OwnerBoundExplorationContextStore,
    *,
    max_ttl_seconds: float,
    clock: Callable[[], float],
) -> tuple[str, _ReopenedLaunchAuthority] | None:
    """Reopen only a current, verifier-matched durable child authority.

    ``max_ttl_seconds`` and ``clock`` are passed explicitly so the
    helper does not reach into the store's instance attributes beyond
    the named list.  The helper does construct a
    :class:`_CapabilityLease` and insert it into ``store._leases`` —
    that is a side effect on the store, not a return value.
    """
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
            value=cast(Any, None),
            origin="launch",
            cwd=authority.cwd,
            repository_root=store._trusted_root,
            source_identity=authority.source_identity,
            snapshot_digest=authority.snapshot_digest,
        )
        store._session_capabilities.setdefault(authority.session_id, set()).add(capability)
        return reopened


def submit_from_launch_environment(
    store: OwnerBoundExplorationContextStore,
    query: ExplorationQuerySpec,
    page_size: int,
    max_ttl_seconds: float,
    clock: Callable[[], float],
) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
    """Collect using only authority injected into the role-local server config."""
    reopened = reopen_launch_environment(
        store,
        max_ttl_seconds=max_ttl_seconds,
        clock=clock,
    )
    if reopened is None:
        return CapabilityResolutionStatus.INVALID, None
    capability, authority = reopened
    try:
        _replacement, page = store.submit_for_capability(
            capability=capability,
            query=query,
            page_size=page_size,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "exploration_submit_failed",
            exception_type=type(exc).__name__,
            reason=_safe_submit_failure_reason(
                exc,
                capability=capability,
                authority=authority,
            ),
        )
        return CapabilityResolutionStatus.INVALID, None
    # A terminal cleanup or replacement may have occurred while collecting.
    if (
        reopen_launch_environment(
            store,
            max_ttl_seconds=max_ttl_seconds,
            clock=clock,
        )
        != reopened
    ):
        store.discard(capability)
        return CapabilityResolutionStatus.INVALID, None
    return CapabilityResolutionStatus.OK, page


def get_page_from_launch_environment(
    store: OwnerBoundExplorationContextStore,
    page_size: int,
    cursor: ContinuationCursor | None,
    max_ttl_seconds: float,
    clock: Callable[[], float],
) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
    """Read an active page while proving the durable authority is still live."""
    reopened = reopen_launch_environment(
        store,
        max_ttl_seconds=max_ttl_seconds,
        clock=clock,
    )
    if reopened is None:
        return CapabilityResolutionStatus.INVALID, None
    capability, _authority = reopened
    status, page = store.get_page_for_capability(
        capability=capability,
        page_size=page_size,
        cursor=cursor,
    )
    if status is not CapabilityResolutionStatus.OK or page is None:
        return status, page
    if (
        reopen_launch_environment(
            store,
            max_ttl_seconds=max_ttl_seconds,
            clock=clock,
        )
        != reopened
    ):
        store.discard(capability)
        return CapabilityResolutionStatus.INVALID, None
    return CapabilityResolutionStatus.OK, page


def validate_launch_environment(
    store: OwnerBoundExplorationContextStore,
    max_ttl_seconds: float,
    clock: Callable[[], float],
) -> bool:
    """Prove that this process holds a current durable explorer authority."""
    return (
        reopen_launch_environment(
            store,
            max_ttl_seconds=max_ttl_seconds,
            clock=clock,
        )
        is not None
    )
