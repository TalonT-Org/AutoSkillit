"""Durable native-shell lineage preparation for food-truck dispatches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStatus,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    SessionCheckpoint,
    get_logger,
    new_managed_launch_id,
    resolve_native_shell_capture_decision,
)
from autoskillit.fleet.state import (
    DispatchRecord,
    DispatchStateHandle,
    DispatchStatus,
    read_state,
)
from autoskillit.fleet.state_recovery import ResumePreflight

if TYPE_CHECKING:
    from autoskillit.pipeline.context import ToolContext

logger = get_logger(__name__)

LaunchPreparation = tuple[str, Any, Any, Path]


class FoodTruckLineageInitializationError(RuntimeError):
    """Raised when a durable FOOD_TRUCK lineage cannot be initialized."""


def resolve_dispatch_timeout(
    timeout_sec: int | None,
    default_timeout_sec: int,
) -> float:
    """Resolve every dispatch timeout surface to one concrete value."""
    if timeout_sec is not None:
        return float(timeout_sec)
    return float(default_timeout_sec)


@dataclass(frozen=True, slots=True)
class DispatchIdentityPreparation:
    """State identity and prior resume evidence resolved before lineage validation."""

    handle: DispatchStateHandle
    resume_requested: bool
    prior_success_record: DispatchRecord | None
    prior_session_chain: tuple[str, ...]
    prior_dispatched_session_id: str
    prior_managed_lineage_ref: ManagedHeadlessSessionLineageRef | None
    resume_lineage_status: ManagedHeadlessSessionLineageStatus | None


@dataclass(frozen=True, slots=True)
class FoodTruckLineagePreparation:
    """Verified launch controls and possibly refreshed dispatch identity."""

    handle: DispatchStateHandle
    launch: LaunchPreparation
    capture_decision: NativeShellCaptureDecision
    managed_lineage_ref: ManagedHeadlessSessionLineageRef
    preflight: ResumePreflight | None
    resume_session_id: str | None
    resume_checkpoint: SessionCheckpoint | None
    resume_message: str | None
    prior_session_chain: tuple[str, ...]
    prior_dispatched_session_id: str
    halted_reason: str | None = None


def _invalid_lineage_decision(
    status: ManagedHeadlessSessionLineageStatus,
) -> NativeShellCaptureDecision:
    """Return the closed fail-safe decision for an untrusted resume lineage."""
    return NativeShellCaptureDecision(
        mode=NativeShellCaptureMode.CAPTURE,
        reason=NativeShellCaptureReason.INVALID_LINEAGE,
        lineage_status=status,
    )


def _lineage_status_from_error(exc: Exception) -> ManagedHeadlessSessionLineageStatus:
    """Map store failures to the closed resume-diagnostic vocabulary."""
    if isinstance(exc, FileNotFoundError):
        return ManagedHeadlessSessionLineageStatus.MISSING
    message = str(exc).lower()
    if "unsupported" in message or "schema" in message:
        return ManagedHeadlessSessionLineageStatus.UNSUPPORTED
    if "anchor" in message or "identity" in message:
        return ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH
    return ManagedHeadlessSessionLineageStatus.CORRUPT


def _verify_resume_lineage(
    *,
    tool_ctx: ToolContext,
    reference: ManagedHeadlessSessionLineageRef | None,
    lineage_anchor: Path,
    dispatch_id: str,
    resume_session_id: str,
    backend_name: str,
) -> tuple[
    ManagedHeadlessSessionLineage | None,
    ManagedHeadlessSessionLineageStatus,
]:
    """Load and strictly verify every resume identity before direct mode can survive."""
    if reference is None:
        return None, ManagedHeadlessSessionLineageStatus.MISSING
    try:
        lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(reference)
    except Exception as exc:
        status = _lineage_status_from_error(exc)
        logger.warning(
            "managed_food_truck_resume_lineage_load_failed",
            lineage_status=status.value,
            exc_info=True,
        )
        return None, status

    if lineage.session_kind is not ManagedHeadlessSessionKind.FOOD_TRUCK:
        return None, ManagedHeadlessSessionLineageStatus.UNSUPPORTED
    if Path(lineage.lineage_anchor) != lineage_anchor:
        return None, ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH
    if lineage.backend != backend_name:
        return None, ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH
    if lineage.launch_id != reference.launch_id:
        return None, ManagedHeadlessSessionLineageStatus.LAUNCH_MISMATCH
    if lineage.dispatch_id != dispatch_id:
        return None, ManagedHeadlessSessionLineageStatus.DISPATCH_MISMATCH
    if (
        not resume_session_id
        or lineage.final_native_session_id is None
        or lineage.final_native_session_id != resume_session_id
    ):
        return None, ManagedHeadlessSessionLineageStatus.NATIVE_SESSION_MISMATCH
    return lineage, ManagedHeadlessSessionLineageStatus.VALID


def set_lineage_terminal_state(
    tool_ctx: ToolContext,
    reference: ManagedHeadlessSessionLineageRef,
    terminal_state: ManagedHeadlessSessionTerminalState,
) -> None:
    """CAS-close the latest durable lineage generation."""
    lineage = tool_ctx.managed_headless_session_lineage_store.load_reference(reference)
    tool_ctx.managed_headless_session_lineage_store.set_terminal_state(
        lineage_anchor=Path(lineage.lineage_anchor),
        launch_id=lineage.launch_id,
        terminal_state=terminal_state,
        expected_generation=lineage.generation,
        expected_record_digest=lineage.record_digest,
    )


def prepare_dispatch_identity(
    *,
    create_fresh_handle: Callable[[], DispatchStateHandle],
    dispatches_dir: Path,
    effective_name: str,
    resume_session_id: str | None,
    prior_dispatch_id: str | None,
) -> DispatchIdentityPreparation:
    """Resolve the state handle and prior resume evidence without trusting lineage yet."""
    prior_session_chain: tuple[str, ...] = ()
    prior_dispatched_session_id = ""
    prior_managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
    resume_lineage_status: ManagedHeadlessSessionLineageStatus | None = None
    resume_requested = bool(resume_session_id)
    prior_success_record: DispatchRecord | None = None

    if resume_session_id and prior_dispatch_id:
        try:
            handle = DispatchStateHandle.open_continued(dispatches_dir, prior_dispatch_id)
            prior_state = read_state(handle.state_path)
            if prior_state is None:
                resume_lineage_status = ManagedHeadlessSessionLineageStatus.CORRUPT
                handle = create_fresh_handle()
            else:
                prior_record = next(
                    (d for d in prior_state.dispatches if d.name == effective_name),
                    None,
                )
                if prior_record is not None:
                    prior_managed_lineage_ref = prior_record.managed_lineage_ref
                    if prior_record.status == DispatchStatus.SUCCESS:
                        prior_success_record = prior_record
                    prior_session_chain = tuple(prior_record.session_chain)
                    prior_dispatched_session_id = prior_record.dispatched_session_id
                if prior_managed_lineage_ref is None:
                    resume_lineage_status = ManagedHeadlessSessionLineageStatus.MISSING
        except (OSError, KeyError, TypeError):
            logger.warning("failed to read prior session chain from state", exc_info=True)
            resume_lineage_status = ManagedHeadlessSessionLineageStatus.MISSING
            handle = create_fresh_handle()
    else:
        if resume_requested:
            resume_lineage_status = ManagedHeadlessSessionLineageStatus.MISSING
        handle = create_fresh_handle()

    return DispatchIdentityPreparation(
        handle=handle,
        resume_requested=resume_requested,
        prior_success_record=prior_success_record,
        prior_session_chain=prior_session_chain,
        prior_dispatched_session_id=prior_dispatched_session_id,
        prior_managed_lineage_ref=prior_managed_lineage_ref,
        resume_lineage_status=resume_lineage_status,
    )


def prepare_food_truck_lineage(
    *,
    tool_ctx: ToolContext,
    identity_preparation: DispatchIdentityPreparation,
    launch: LaunchPreparation,
    prepare_launch: Callable[[str], LaunchPreparation],
    create_fresh_handle: Callable[[], DispatchStateHandle],
    effective_name: str,
    prior_dispatch_id: str | None,
    resume_session_id: str | None,
    resume_checkpoint: SessionCheckpoint | None,
    resume_message: str | None,
    resume_preparer: Callable[[], ResumePreflight | None],
    native_shell_capture_mode: NativeShellCaptureMode | None,
    lineage_backend_name: str,
) -> FoodTruckLineagePreparation:
    """Validate resume lineage or create one fresh FOOD_TRUCK lineage."""
    handle = identity_preparation.handle
    dispatch_id = handle.identity.dispatch_id
    prior_session_chain = identity_preparation.prior_session_chain
    prior_dispatched_session_id = identity_preparation.prior_dispatched_session_id
    resume_lineage_status = identity_preparation.resume_lineage_status
    prompt, plugin_authority, capability_preparation, lineage_anchor = launch
    managed_lineage: ManagedHeadlessSessionLineage | None = None
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
    preflight: ResumePreflight | None = None

    if identity_preparation.resume_requested:
        if resume_lineage_status is None:
            managed_lineage, resume_lineage_status = _verify_resume_lineage(
                tool_ctx=tool_ctx,
                reference=identity_preparation.prior_managed_lineage_ref,
                lineage_anchor=lineage_anchor,
                dispatch_id=dispatch_id,
                resume_session_id=resume_session_id or "",
                backend_name=lineage_backend_name,
            )
        if managed_lineage is not None:
            capture_decision = managed_lineage.decision
            managed_lineage_ref = managed_lineage.reference
            if (
                native_shell_capture_mode is not None
                and native_shell_capture_mode is not capture_decision.mode
            ):
                logger.warning(
                    "native_shell_capture_resume_override_rejected",
                    requested_mode=native_shell_capture_mode.value,
                    inherited_mode=capture_decision.mode.value,
                    reason=NativeShellCaptureReason.RESUME_OVERRIDE_REJECTED.value,
                    lineage_status=ManagedHeadlessSessionLineageStatus.OVERRIDE_REJECTED.value,
                    dispatch_id=dispatch_id,
                )
            else:
                logger.info(
                    "native_shell_capture_resume_inherited",
                    mode=capture_decision.mode.value,
                    reason=NativeShellCaptureReason.RESUME_INHERITED.value,
                    lineage_status=ManagedHeadlessSessionLineageStatus.VALID.value,
                    dispatch_id=dispatch_id,
                )
            preflight = resume_preparer()
            if preflight is not None:
                if preflight.halt:
                    return FoodTruckLineagePreparation(
                        handle=handle,
                        launch=launch,
                        capture_decision=capture_decision,
                        managed_lineage_ref=managed_lineage_ref,
                        preflight=preflight,
                        resume_session_id=resume_session_id,
                        resume_checkpoint=resume_checkpoint,
                        resume_message=resume_message,
                        prior_session_chain=prior_session_chain,
                        prior_dispatched_session_id=prior_dispatched_session_id,
                        halted_reason=(
                            preflight.halted_reason or "Resume refused by precondition chokepoint"
                        ),
                    )
                prior_session_chain = tuple(preflight.prior_session_chain)
                prior_dispatched_session_id = preflight.prior_dispatched_session_id
        else:
            invalid_status = resume_lineage_status or ManagedHeadlessSessionLineageStatus.CORRUPT
            logger.warning(
                "native_shell_capture_resume_lineage_invalid",
                requested_mode=(
                    native_shell_capture_mode.value
                    if native_shell_capture_mode is not None
                    else None
                ),
                effective_mode=NativeShellCaptureMode.CAPTURE.value,
                reason=NativeShellCaptureReason.INVALID_LINEAGE.value,
                lineage_status=invalid_status.value,
                prior_dispatch_id=prior_dispatch_id,
            )
            if prior_dispatch_id and dispatch_id == prior_dispatch_id:
                handle = create_fresh_handle()
                dispatch_id = handle.identity.dispatch_id
                launch = prepare_launch(dispatch_id)
                prompt, plugin_authority, capability_preparation, lineage_anchor = launch
            resume_session_id = None
            resume_checkpoint = None
            resume_message = None
            prior_session_chain = ()
            prior_dispatched_session_id = ""
            capture_decision = _invalid_lineage_decision(invalid_status)
    else:
        capture_decision = resolve_native_shell_capture_decision(native_shell_capture_mode)

    if managed_lineage is None:
        try:
            managed_lineage = tool_ctx.managed_headless_session_lineage_store.create(
                lineage_anchor=lineage_anchor,
                launch_id=new_managed_launch_id(),
                decision=capture_decision,
                backend=lineage_backend_name,
                session_kind=ManagedHeadlessSessionKind.FOOD_TRUCK,
                dispatch_id=dispatch_id,
            )
        except Exception as exc:
            logger.warning("managed_food_truck_lineage_create_failed", exc_info=True)
            raise FoodTruckLineageInitializationError from exc
        managed_lineage_ref = managed_lineage.reference
    if managed_lineage_ref is None:
        raise FoodTruckLineageInitializationError

    return FoodTruckLineagePreparation(
        handle=handle,
        launch=(
            prompt,
            plugin_authority,
            capability_preparation,
            lineage_anchor,
        ),
        capture_decision=capture_decision,
        managed_lineage_ref=managed_lineage_ref,
        preflight=preflight,
        resume_session_id=resume_session_id,
        resume_checkpoint=resume_checkpoint,
        resume_message=resume_message,
        prior_session_chain=prior_session_chain,
        prior_dispatched_session_id=prior_dispatched_session_id,
    )
