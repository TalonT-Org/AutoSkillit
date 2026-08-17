"""Tracker authority selection and lifecycle helpers for the pipeline tracker tool."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    ArtifactLease,
    AuditIdentityReservation,
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
)
from autoskillit.pipeline import get_kitchen_process_identity
from autoskillit.server.tools._overlay_state import read_overlay
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


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
    expected: bool | None = None,
) -> tuple[
    TrackerAuthorityTarget | None,
    TrackerAuthorityReadResult | None,
    TrackerParticipantKey | None,
    ArtifactLease | None,
]:
    explicit_target = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    target_order_id = explicit_target or tool_ctx.kitchen_id
    if not target_order_id:
        return None, None, None, None
    if expected is None:
        expected = bool(explicit_target)
        if not expected and tool_ctx.active_recipe_steps:
            try:
                expected = bool(_derive_phase_a_deps(tool_ctx.active_recipe_steps))
            except (AttributeError, TypeError):
                expected = False
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        target_order_id,
        expected=expected,
    )
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="manual",
        owner_id=f"selection:{uuid.uuid4().hex}",
    )
    try:
        authority = read_tracker_authority(target, lease)
    except Exception:
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
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="kitchen",
        owner_id=tool_ctx.kitchen_id or target_order_id,
    )
    try:
        authority = read_tracker_authority(target, lease)
    except Exception:
        if key != current_key:
            _release_context_tracker(tool_ctx, key)
        raise
    if current_key is not None and current_key != key:
        _release_context_tracker(tool_ctx, current_key)
    return target, authority, key, lease


def _authority_blocks_dependency_check(authority: TrackerAuthorityReadResult | None) -> bool:
    return bool(
        authority is not None
        and (authority.error is not None or (authority.data or {}).get("dependencies"))
    )


def _resolve_skipped_steps(project_dir: Path, pipeline_id: str) -> set[str]:
    try:
        overlay = read_overlay(project_dir)
        pid_locks = overlay.get("locked_steps", {}).get(pipeline_id, {})
        return {s for s, v in pid_locks.items() if v is False}
    except OSError:
        return set()
