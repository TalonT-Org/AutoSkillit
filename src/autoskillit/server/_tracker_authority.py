"""Server-internal ownership for pipeline tracker authority mechanics."""

from __future__ import annotations

import os
import uuid
from typing import Literal

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    ArtifactLease,
    AuditIdentityReservation,
    KitchenProcessIdentity,
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    get_logger,
    read_tracker_authority,
    register_active_kitchen,
    release_tracker_lease,
    retain_tracker_lease,
    try_retire_tracker,
    unregister_active_kitchen,
)
from autoskillit.pipeline import ToolContext, get_kitchen_process_identity
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps

logger = get_logger(__name__)


def select_tracker_authority_expected(tool_ctx: ToolContext, order_id: str) -> bool:
    """Resolve whether a kitchen-scoped tracker is 'expected' for this dispatch.

    A tracker is expected when an explicit order id or dispatch env var is
    present, or when the active recipe projection declares phase-A dependencies
    that the tracker must enforce.
    """
    if order_id or os.environ.get(DISPATCH_ID_ENV_VAR, ""):
        return True
    if tool_ctx.active_recipe_projection is None:
        return False
    try:
        return bool(_derive_phase_a_deps(tool_ctx.active_recipe_projection))
    except (AttributeError, TypeError):
        return False


def read_tracker_identity(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
) -> tuple[str, str] | None:
    """Read kitchen and incarnation identity under the target's retained lease."""
    authority = read_tracker_authority(target, lease)
    if authority.data is None:
        return None
    kitchen_id = authority.data.get("kitchen_id")
    incarnation_id = authority.data.get("tracker_incarnation_id")
    if not isinstance(kitchen_id, str) or not isinstance(incarnation_id, str):
        logger.warning(
            "tracker_identity_malformed",
            target=str(target.path),
            kitchen_id_type=type(kitchen_id).__name__,
            incarnation_id_type=type(incarnation_id).__name__,
        )
        return None
    return kitchen_id, incarnation_id


def select_tracker_target(
    tool_ctx: ToolContext,
    order_id: str,
    *,
    expected: bool,
) -> TrackerAuthorityTarget | None:
    """Select one explicit target without scanning for ambient tracker files."""
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "") or tool_ctx.kitchen_id
    if not effective_oid:
        return None
    return TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        effective_oid,
        expected=expected,
    )


def _retain_context_tracker(
    tool_ctx: ToolContext,
    target: TrackerAuthorityTarget,
    *,
    owner_kind: Literal["kitchen", "dispatch", "manual"],
    owner_id: str,
) -> tuple[TrackerParticipantKey, ArtifactLease]:
    with tool_ctx.tracker_leases_lock:
        identity = get_kitchen_process_identity(tool_ctx, owner_id)
        key = TrackerParticipantKey(
            target=target,
            owner_kind=owner_kind,
            owner_id=owner_id,
            pid=identity.pid,
            create_time=identity.create_time,
            project_path=identity.project_path,
        )
        lease = retain_tracker_lease(tool_ctx.tracker_leases, key)
    return key, lease


def _release_context_tracker(tool_ctx: ToolContext, key: TrackerParticipantKey) -> None:
    with tool_ctx.tracker_leases_lock:
        release_tracker_lease(tool_ctx.tracker_leases, key)


def _select_tracker_authority(
    tool_ctx: ToolContext,
    order_id: str,
    *,
    expected: bool,
) -> tuple[
    TrackerAuthorityTarget | None,
    TrackerAuthorityReadResult | None,
    TrackerParticipantKey | None,
    ArtifactLease | None,
]:
    target = select_tracker_target(tool_ctx, order_id, expected=expected)
    if target is None:
        return None, None, None, None
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="manual",
        owner_id=f"selection:{uuid.uuid4().hex}",
    )
    try:
        authority = read_tracker_authority(target, lease)
    except Exception:
        logger.warning(
            "tracker_authority_read_failed",
            target=str(target.path),
            exc_info=True,
        )
        _release_context_tracker(tool_ctx, key)
        raise
    return target, authority, key, lease


def _restore_reserved_tracker_authority(
    tool_ctx: ToolContext,
    reservation: AuditIdentityReservation,
    current_key: TrackerParticipantKey | None,
) -> tuple[
    TrackerAuthorityTarget | None,
    TrackerAuthorityReadResult | None,
    TrackerParticipantKey | None,
    ArtifactLease | None,
]:
    target_order_id = reservation.tracker_target_order_id
    if target_order_id is None:
        if current_key is not None:
            _release_context_tracker(tool_ctx, current_key)
        return None, None, None, None
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        target_order_id,
        expected=reservation.tracker_expected,
    )
    if current_key is not None and current_key.target == target:
        with tool_ctx.tracker_leases_lock:
            lease = tool_ctx.tracker_leases.get(current_key)
        if lease is None:
            return None, None, None, None
        try:
            authority = read_tracker_authority(target, lease)
        except Exception:
            logger.warning(
                "tracker_authority_read_failed",
                target=str(target.path),
                exc_info=True,
            )
            _release_context_tracker(tool_ctx, current_key)
            raise
        return target, authority, current_key, lease
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="dispatch",
        owner_id=target_order_id,
    )
    try:
        authority = read_tracker_authority(target, lease)
    except Exception:
        logger.warning(
            "tracker_authority_read_failed",
            target=str(target.path),
            exc_info=True,
        )
        _release_context_tracker(tool_ctx, key)
        raise
    if current_key is not None:
        _release_context_tracker(tool_ctx, current_key)
    return target, authority, key, lease


def _retain_kitchen_tracker_authority(
    tool_ctx: ToolContext,
) -> tuple[TrackerParticipantKey, ArtifactLease]:
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        tool_ctx.kitchen_id,
        expected=False,
    )
    with tool_ctx.tracker_leases_lock:
        identity = get_kitchen_process_identity(tool_ctx)
        key = TrackerParticipantKey(
            target=target,
            owner_kind="kitchen",
            owner_id=identity.kitchen_id,
            pid=identity.pid,
            create_time=identity.create_time,
            project_path=identity.project_path,
        )
        lease = retain_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = key
    return key, lease


def _register_active_kitchen(tool_ctx: ToolContext) -> KitchenProcessIdentity:
    """Register the retained kitchen process while preserving refusal semantics."""
    identity = tool_ctx.kitchen_process_identity
    if identity is None:
        raise RuntimeError(
            "_register_active_kitchen requires tool_ctx.kitchen_process_identity to be set"
        )
    if not register_active_kitchen(identity):
        logger.warning(
            "active_kitchen_registration_refused",
            kitchen_id=identity.kitchen_id,
        )
    return identity


def _release_kitchen_tracker_authority(
    tool_ctx: ToolContext,
    *,
    unregister: bool,
    retire: bool,
) -> None:
    with tool_ctx.tracker_leases_lock:
        key = tool_ctx.kitchen_tracker_key
        identity = tool_ctx.kitchen_process_identity
        if key is not None:
            release_tracker_lease(tool_ctx.tracker_leases, key)
        tool_ctx.kitchen_tracker_key = None
        if unregister:
            tool_ctx.kitchen_process_identity = None
    try:
        if unregister and identity is not None and not unregister_active_kitchen(identity):
            logger.warning(
                "active_kitchen_unregistration_refused",
                kitchen_id=identity.kitchen_id,
            )
    finally:
        if retire and key is not None:
            try_retire_tracker(key.target)


def _drain_context_tracker_leases(tool_ctx: ToolContext) -> set[TrackerAuthorityTarget]:
    with tool_ctx.tracker_leases_lock:
        targets = {key.target for key in tool_ctx.tracker_leases}
        for key in list(tool_ctx.tracker_leases):
            release_tracker_lease(tool_ctx.tracker_leases, key)
    return targets


def _completion_tracker_binding(
    tool_ctx: ToolContext,
    order_id: str,
    *,
    tracker_target: TrackerAuthorityTarget | None = None,
) -> tuple[str, str, str, str]:
    """Resolve immutable tracker identity for a new completion receipt."""
    target = tracker_target or select_tracker_target(tool_ctx, order_id, expected=bool(order_id))
    if target is None or not target.path.exists():
        return "", "", "", ""
    target_order_id = target.target_order_id
    if not isinstance(target_order_id, str) or not target_order_id:
        logger.warning(
            "completion_tracker_binding_invalid_target_order_id",
            target=str(target.path),
            target_order_id=target_order_id,
        )
        return "", "", "", ""
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="manual",
        owner_id=target_order_id,
    )
    try:
        tracker_identity = read_tracker_identity(target, lease)
        if tracker_identity is None:
            return "", "", "", ""
        kitchen_id, incarnation_id = tracker_identity
        return target_order_id, str(target.path.resolve()), kitchen_id, incarnation_id
    finally:
        _release_context_tracker(tool_ctx, key)
