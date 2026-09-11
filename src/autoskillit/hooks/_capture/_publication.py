"""Reference publication settlement for oversized shell captures.

Owns the two steps that run after a capture is finalized but before its
reference is handed back: re-reading the lifecycle record when a transition
was already committed elsewhere, and publishing (or marking unavailable) an
issued reference whose publication binding has been re-verified. Split out of
`_runner.py` so the settlement branch has one owner.

stdlib-only; supports the three shell-capture import spellings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _artifact_setup, _delivery, _snapshot  # noqa: I001
    from autoskillit.hooks import _capture_lifecycle
    from autoskillit.hooks._capture._module_identity import register_module_aliases
elif __package__ == "_capture":
    from _capture import _artifact_setup, _delivery, _snapshot  # noqa: I001
    import _capture_lifecycle
    from _capture._module_identity import register_module_aliases
else:
    from . import _artifact_setup, _delivery, _snapshot  # noqa: I001
    from .. import _capture_lifecycle
    from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_capture_delivery = _delivery
_CAPTURE_RUNTIME_ERRORS: tuple[type[Exception], ...] = _artifact_setup._CAPTURE_RUNTIME_ERRORS
CaptureArtifact = _artifact_setup.CaptureArtifact
CaptureRoot = _artifact_setup.CaptureRoot
CaptureSetupError = _artifact_setup.CaptureSetupError
ProjectAnchor = _artifact_setup.ProjectAnchor
verify_reference_publication_binding = _artifact_setup.verify_reference_publication_binding
FinalizedCapture = _snapshot.FinalizedCapture
PublishedCaptureReference = _snapshot.PublishedCaptureReference
UnavailableCaptureReference = _snapshot.UnavailableCaptureReference
CaptureLifecycleError = _capture_lifecycle.CaptureLifecycleError
CaptureLifecycleStore = _capture_lifecycle.CaptureLifecycleStore
CaptureTransitionCommittedError = _capture_lifecycle.CaptureTransitionCommittedError


def _reference_result_after_transition(
    lifecycle: CaptureLifecycleStore,
    finalized: FinalizedCapture,
    *,
    unavailable_reason: str,
) -> PublishedCaptureReference | UnavailableCaptureReference | None:
    record = lifecycle.get_record(finalized.snapshot.manifest.capture_id)
    return _capture_delivery.reference_result(
        finalized,
        record,
        unavailable_reason=unavailable_reason,
        lifecycle_error=CaptureLifecycleError,
    )


def _publish_oversized_capture(
    anchor: ProjectAnchor,
    root: CaptureRoot,
    artifact: CaptureArtifact,
    lifecycle: CaptureLifecycleStore,
    finalized: FinalizedCapture,
) -> PublishedCaptureReference | UnavailableCaptureReference:
    issuance = finalized.issuance
    if issuance is None:
        raise CaptureSetupError.unknown("oversized capture lacks issued reference")
    try:
        binding_valid = verify_reference_publication_binding(
            anchor,
            root,
            artifact,
            issuance,
        )
    except _CAPTURE_RUNTIME_ERRORS:
        _capture_delivery.invalidate_lost_reference(
            lifecycle,
            finalized,
            reason_code="PUBLICATION_BINDING_FAILED",
            lifecycle_error=CaptureLifecycleError,
            runtime_errors=_CAPTURE_RUNTIME_ERRORS,
        )
        raise
    if not binding_valid:
        reason = "PUBLICATION_BINDING_UNAVAILABLE"
        try:
            return lifecycle.mark_reference_unavailable(
                finalized,
                reason_code=reason,
            )
        except CaptureTransitionCommittedError:
            reconciled = _reference_result_after_transition(
                lifecycle,
                finalized,
                unavailable_reason=reason,
            )
            if type(reconciled) is UnavailableCaptureReference:
                return reconciled
            raise
    try:
        return lifecycle.publish_reference(finalized)
    except CaptureTransitionCommittedError:
        reconciled = _reference_result_after_transition(
            lifecycle,
            finalized,
            unavailable_reason="PUBLICATION_FAILED",
        )
        if type(reconciled) is PublishedCaptureReference:
            return reconciled
        _capture_delivery.invalidate_lost_reference(
            lifecycle,
            finalized,
            reason_code="PUBLICATION_FAILED",
            lifecycle_error=CaptureLifecycleError,
            runtime_errors=_CAPTURE_RUNTIME_ERRORS,
        )
        raise
    except _CAPTURE_RUNTIME_ERRORS:
        _capture_delivery.invalidate_lost_reference(
            lifecycle,
            finalized,
            reason_code="PUBLICATION_FAILED",
            lifecycle_error=CaptureLifecycleError,
            runtime_errors=_CAPTURE_RUNTIME_ERRORS,
        )
        raise
