"""Managed native-shell lineage implementation for the run-skill boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    CodingAgentBackend,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStatus,
    ManagedHeadlessSessionLineageStore,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    NativeShellCaptureReason,
    SkillContractError,
    get_logger,
    new_managed_launch_id,
    resolve_native_shell_capture_decision,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SkillNativeShellLineagePreparation:
    """Trusted launch controls resolved for one fresh or resumed skill."""

    decision: NativeShellCaptureDecision | None
    reference: ManagedHeadlessSessionLineageRef | None


def _parse_requested_mode(value: str) -> NativeShellCaptureMode | None:
    if not value:
        return None
    try:
        return NativeShellCaptureMode(value)
    except ValueError as exc:
        raise SkillContractError(
            "native shell capture mode must be 'capture' or 'direct'"
        ) from exc


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
    """Map store failures to the shared closed resume-diagnostic vocabulary."""
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
    lineage: ManagedHeadlessSessionLineage,
    reference: ManagedHeadlessSessionLineageRef,
    backend: CodingAgentBackend | None,
    lineage_anchor: Path,
    resume_session_id: str,
) -> ManagedHeadlessSessionLineageStatus:
    """Return the exact closed status for every skill-resume identity check."""
    if lineage.session_kind is not ManagedHeadlessSessionKind.SKILL:
        return ManagedHeadlessSessionLineageStatus.UNSUPPORTED
    if Path(lineage.lineage_anchor).resolve() != lineage_anchor.resolve():
        return ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH
    if backend is None or lineage.backend != backend.name:
        return ManagedHeadlessSessionLineageStatus.IDENTITY_MISMATCH
    if lineage.launch_id != reference.launch_id:
        return ManagedHeadlessSessionLineageStatus.LAUNCH_MISMATCH
    if lineage.dispatch_id is not None:
        return ManagedHeadlessSessionLineageStatus.DISPATCH_MISMATCH
    if (
        not resume_session_id
        or lineage.final_native_session_id is None
        or lineage.final_native_session_id != resume_session_id
    ):
        return ManagedHeadlessSessionLineageStatus.NATIVE_SESSION_MISMATCH
    return ManagedHeadlessSessionLineageStatus.VALID


def _invalid_resume_preparation(
    *,
    store: ManagedHeadlessSessionLineageStore,
    backend: CodingAgentBackend | None,
    lineage_anchor: Path,
    requested: NativeShellCaptureMode | None,
    status: ManagedHeadlessSessionLineageStatus,
    resume_session_id: str,
) -> SkillNativeShellLineagePreparation:
    """Record and return the canonical capture fallback for invalid lineage."""
    logger.warning(
        "native_shell_capture_resume_lineage_invalid",
        requested_mode=requested.value if requested is not None else None,
        effective_mode=NativeShellCaptureMode.CAPTURE.value,
        reason=NativeShellCaptureReason.INVALID_LINEAGE.value,
        lineage_status=status.value,
        resume_session_id=resume_session_id,
    )
    if backend is None or not backend.capabilities.session_dir_persistent:
        return SkillNativeShellLineagePreparation(None, None)
    decision = _invalid_lineage_decision(status)
    lineage = store.create(
        lineage_anchor=lineage_anchor,
        launch_id=new_managed_launch_id(),
        decision=decision,
        backend=backend.name,
        session_kind=ManagedHeadlessSessionKind.SKILL,
    )
    return SkillNativeShellLineagePreparation(decision, lineage.reference)


def prepare_skill_native_shell_lineage(
    *,
    store: ManagedHeadlessSessionLineageStore,
    backend: CodingAgentBackend | None,
    lineage_anchor: Path,
    stored_reference: ManagedHeadlessSessionLineageRef | None,
    resume_session_id: str,
    requested_mode: str,
    is_resume: bool,
) -> SkillNativeShellLineagePreparation:
    """Create fresh lineage or verify that a resume inherits its stored decision."""
    if not is_resume:
        if backend is None:
            raise SkillContractError("Managed skill launch backend is unavailable")
        if not backend.capabilities.session_dir_persistent:
            return SkillNativeShellLineagePreparation(None, None)
        decision = resolve_native_shell_capture_decision(requested_mode or None)
        lineage = store.create(
            lineage_anchor=lineage_anchor,
            launch_id=new_managed_launch_id(),
            decision=decision,
            backend=backend.name,
            session_kind=ManagedHeadlessSessionKind.SKILL,
        )
        return SkillNativeShellLineagePreparation(decision, lineage.reference)

    requested = _parse_requested_mode(requested_mode)
    if stored_reference is None:
        return _invalid_resume_preparation(
            store=store,
            backend=backend,
            lineage_anchor=lineage_anchor,
            requested=requested,
            status=ManagedHeadlessSessionLineageStatus.MISSING,
            resume_session_id=resume_session_id,
        )

    try:
        lineage = store.load_reference(stored_reference)
    except Exception as exc:
        lineage_status = _lineage_status_from_error(exc)
        logger.warning(
            "managed_skill_resume_lineage_load_failed",
            lineage_status=lineage_status.value,
            exc_info=True,
        )
        return _invalid_resume_preparation(
            store=store,
            backend=backend,
            lineage_anchor=lineage_anchor,
            requested=requested,
            status=lineage_status,
            resume_session_id=resume_session_id,
        )
    try:
        lineage_status = _verify_resume_lineage(
            lineage=lineage,
            reference=stored_reference,
            backend=backend,
            lineage_anchor=lineage_anchor,
            resume_session_id=resume_session_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        lineage_status = _lineage_status_from_error(exc)
    if lineage_status is not ManagedHeadlessSessionLineageStatus.VALID:
        return _invalid_resume_preparation(
            store=store,
            backend=backend,
            lineage_anchor=lineage_anchor,
            requested=requested,
            status=lineage_status,
            resume_session_id=resume_session_id,
        )
    if requested is not None and requested is not lineage.decision.mode:
        logger.warning(
            "native_shell_capture_resume_override_rejected",
            requested_mode=requested.value,
            inherited_mode=lineage.decision.mode.value,
            reason=NativeShellCaptureReason.RESUME_OVERRIDE_REJECTED.value,
            lineage_status=ManagedHeadlessSessionLineageStatus.OVERRIDE_REJECTED.value,
            resume_session_id=resume_session_id,
        )
    else:
        logger.info(
            "native_shell_capture_resume_inherited",
            mode=lineage.decision.mode.value,
            reason=NativeShellCaptureReason.RESUME_INHERITED.value,
            lineage_status=ManagedHeadlessSessionLineageStatus.VALID.value,
            resume_session_id=resume_session_id,
        )
    return SkillNativeShellLineagePreparation(lineage.decision, lineage.reference)


def rebind_verified_final_session(
    *,
    store: ManagedHeadlessSessionLineageStore,
    backend: CodingAgentBackend | None,
    reference: ManagedHeadlessSessionLineageRef | None,
    is_resume: bool,
    requested_session_id: str,
    returned_session_id: str,
    on_rebind: Callable[[str, ManagedHeadlessSessionLineageRef], None],
) -> None:
    """Rebind only a changed final ID proven to belong to the same Codex lineage."""
    if not is_resume or not returned_session_id or returned_session_id == requested_session_id:
        return
    if backend is None or not backend.capabilities.session_dir_persistent or reference is None:
        raise SkillContractError("Resumed execution returned a different final session ID")
    lineage = store.load_reference(reference)
    if returned_session_id not in {
        lineage.final_native_session_id,
        *lineage.candidate_native_session_ids,
    }:
        raise SkillContractError("Resumed Codex execution returned an unverified final session ID")
    if lineage.final_native_session_id not in {
        requested_session_id,
        returned_session_id,
    }:
        raise SkillContractError("Resumed Codex lineage final session ID changed unexpectedly")
    if lineage.final_native_session_id != returned_session_id:
        store.rebind_final_native_session_id(
            lineage_anchor=Path(reference.lineage_anchor),
            launch_id=reference.launch_id,
            expected_session_id=requested_session_id,
            session_id=returned_session_id,
            expected_generation=lineage.generation,
            expected_record_digest=lineage.record_digest,
        )
    on_rebind(returned_session_id, reference)


__all__ = [
    "SkillNativeShellLineagePreparation",
    "prepare_skill_native_shell_lineage",
    "rebind_verified_final_session",
]
