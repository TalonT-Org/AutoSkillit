"""Managed native-shell lineage implementation for the run-skill boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    CodingAgentBackend,
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionLineageStore,
    NativeShellCaptureDecision,
    NativeShellCaptureMode,
    SkillContractError,
    new_managed_launch_id,
    resolve_native_shell_capture_decision,
)


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
        if requested is NativeShellCaptureMode.DIRECT:
            raise SkillContractError(
                "Cannot grant direct native shell mode: resume lineage is missing"
            )
        return SkillNativeShellLineagePreparation(None, None)

    try:
        lineage = store.load_reference(stored_reference)
    except (OSError, ValueError) as exc:
        raise SkillContractError("Cannot resume session: managed lineage is invalid") from exc
    if backend is None:
        raise SkillContractError("Resume contract backend is unavailable")
    if (
        lineage.backend != backend.name
        or lineage.session_kind is not ManagedHeadlessSessionKind.SKILL
        or Path(lineage.lineage_anchor).resolve() != lineage_anchor.resolve()
        or resume_session_id
        not in {
            lineage.final_native_session_id,
            *lineage.candidate_native_session_ids,
        }
    ):
        raise SkillContractError("Cannot resume session: managed lineage identity mismatch")
    if requested is not None and requested is not lineage.decision.mode:
        raise SkillContractError(
            "Cannot override native shell capture mode on resume: "
            f"lineage requires {lineage.decision.mode.value!r}"
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
    on_rebind(returned_session_id, reference)


__all__ = [
    "SkillNativeShellLineagePreparation",
    "prepare_skill_native_shell_lineage",
    "rebind_verified_final_session",
]
