"""Descriptor-anchored shell-capture execution runner."""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _artifact_setup, _authority, _delivery  # noqa: I001
    from autoskillit.hooks._capture import _failure_policy, _publication
    from autoskillit.hooks._capture import _observation, _reader, _reconcile, _replay
    from autoskillit.hooks._capture import _snapshot, _types
    from autoskillit.hooks import _capture_contract, _capture_lifecycle, _capture_process
    from autoskillit.hooks._runtime import _hook_settings, _policy_event
    from autoskillit.hooks._capture._module_identity import register_module_aliases
elif __package__ == "_capture":
    from _capture import _artifact_setup, _authority, _delivery  # noqa: I001
    from _capture import _failure_policy, _observation, _publication
    from _capture import _reader, _reconcile, _replay, _snapshot, _types
    import _capture_contract
    import _capture_lifecycle
    import _capture_process
    from _runtime import _hook_settings, _policy_event  # noqa: I001 — bare-name post-move; cyclic broken by sys.modules register in _capture_artifacts.py
    from _capture._module_identity import register_module_aliases
else:
    from . import _artifact_setup, _authority, _delivery, _failure_policy  # noqa: I001
    from . import _observation, _publication, _reader
    from . import _reconcile, _replay, _snapshot, _types
    from .. import _capture_contract, _capture_lifecycle, _capture_process
    from .._runtime import _hook_settings, _policy_event
    from ._module_identity import register_module_aliases

register_module_aliases(__name__)

_DEFAULT_INLINE_BYTES = _artifact_setup._DEFAULT_INLINE_BYTES
_CAPTURE_RUNTIME_ERRORS: tuple[type[Exception], ...] = _artifact_setup._CAPTURE_RUNTIME_ERRORS
logger = logging.getLogger(__name__)  # noqa: TID251 - isolated stdlib runner
logger.addHandler(logging.NullHandler())
logger.propagate = False

_capture_delivery = _delivery
_capture_failure_policy = _failure_policy
_capture_observation = _observation
_capture_reconcile = _reconcile
_capture_replay = _replay
_capture_types = _types
_DIRECTORY_FLAGS = _authority._DIRECTORY_FLAGS
_READ_FLAGS = _authority._READ_FLAGS
CaptureRoot = _authority.CaptureRoot
CaptureSetupError = _authority.CaptureSetupError
FileIdentity = _authority.FileIdentity
ProjectAnchor = _authority.ProjectAnchor
_open_directory_component = _authority._open_directory_component
_same_identity = _authority._same_identity
open_capture_root = _authority.open_capture_root
open_project_anchor = _authority.open_project_anchor
record_runner_observation = _capture_observation.record_runner_observation
validate_lineage_reference = _capture_observation.validate_lineage_reference
VerifiedCaptureReader = _reader.VerifiedCaptureReader
CaptureWriteAuthority = _snapshot.CaptureWriteAuthority
CommandOutcome = _snapshot.CommandOutcome
FinalizedCapture = _snapshot.FinalizedCapture
IssuedCaptureReference = _snapshot.IssuedCaptureReference
PublishedCaptureReference = _snapshot.PublishedCaptureReference
UnavailableCaptureReference = _snapshot.UnavailableCaptureReference
verify_capture_snapshot = _snapshot.verify_capture_snapshot
CaptureCapacitySpec = _types.CaptureCapacitySpec
CaptureFailureEvidence = _types.CaptureFailureEvidence
HOT_PATH_LOCK_WAIT = _types.HOT_PATH_LOCK_WAIT
_CAPTURE_ID_RE = _capture_contract._CAPTURE_ID_RE
_MAX_COMMAND_BYTES = _capture_contract._MAX_COMMAND_BYTES
CaptureFailureReason = _capture_contract.CaptureFailureReason
CaptureLineageRef = _capture_contract.CaptureLineageRef
CaptureProtocolError = _capture_contract.CaptureProtocolError
CaptureRequest = _capture_contract.CaptureRequest
decode_capture_request = _capture_contract.decode_capture_request
CaptureCapacityError = _capture_lifecycle.CaptureCapacityError
CaptureDeliveryStatus = _capture_lifecycle.CaptureDeliveryStatus
CaptureLifecycleError = _capture_lifecycle.CaptureLifecycleError
CaptureLifecycleStore = _capture_lifecycle.CaptureLifecycleStore
CaptureTransitionCommittedError = _capture_lifecycle.CaptureTransitionCommittedError
_TRUSTED_BASH_CANDIDATES = _capture_process._TRUSTED_BASH_CANDIDATES
OwnedProcessGroup = _capture_process.OwnedProcessGroup
_DrainResult = _capture_process._DrainResult
_normalized_returncode = _capture_process._normalized_returncode
_settle_failed_capture = _capture_process._settle_failed_capture
_spawn_bash = _capture_process._spawn_bash
_drain_owned_capture = _capture_process._drain_capture
_resolve_trusted_bash = _capture_process._resolve_bash
HOOK_CONFIG_FILENAME = _hook_settings.HOOK_CONFIG_FILENAME
HOOK_CONFIG_OVERLAY_FILENAME = _hook_settings.HOOK_CONFIG_OVERLAY_FILENAME
merge_hook_configs = _hook_settings.merge_hook_configs
PolicyEvent = _policy_event.PolicyEvent
render_provenance_prefix = _policy_event.render_provenance_prefix
CaptureArtifact = _artifact_setup.CaptureArtifact
CapturePolicy = _artifact_setup.CapturePolicy
create_capture_artifact = _artifact_setup.create_capture_artifact
_duplicate_artifact_writer = _artifact_setup._duplicate_artifact_writer
read_capture_policy = _artifact_setup.read_capture_policy
verify_reference_publication_binding = _artifact_setup.verify_reference_publication_binding
_publish_oversized_capture = _publication._publish_oversized_capture
_reference_result_after_transition = _publication._reference_result_after_transition


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "capture artifact write made no progress")
        view = view[written:]


def _drain_capture(
    process: subprocess.Popen[bytes] | OwnedProcessGroup,
    artifact_writer_fd: int,
    inline_bytes: int,
) -> _DrainResult:
    return _drain_owned_capture(
        process,
        artifact_writer_fd,
        inline_bytes,
        digest_factory=hashlib.sha256,
        write_all=_write_all,
    )


def _resolve_bash() -> str:
    return _resolve_trusted_bash(_TRUSTED_BASH_CANDIDATES)


def run_capture(
    command: str,
    cwd: str,
    capture_id: str,
    *,
    requested_mode: str = "capture",
    attempt_id: str | None = None,
    lineage_ref: CaptureLineageRef | None = None,
) -> int:
    """Run ``command`` with descriptor-anchored cwd and owned process settlement."""

    if (
        requested_mode not in {"capture", "direct"}
        or (attempt_id is None) != (lineage_ref is None)
        or (requested_mode == "direct" and lineage_ref is None)
    ):
        raise CaptureSetupError.unknown("invalid capture authority")
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureSetupError.unknown("invalid command encoding") from exc
    if (
        not _CAPTURE_ID_RE.fullmatch(capture_id)
        or "\x00" in command
        or len(command_bytes) > _MAX_COMMAND_BYTES
    ):
        raise CaptureSetupError.unknown("invalid capture request")

    anchor = open_project_anchor(cwd)
    root: CaptureRoot | None = None
    lifecycle: CaptureLifecycleStore | None = None
    artifact: CaptureArtifact | None = None
    artifact_writer_fd = -1
    process: subprocess.Popen[bytes] | OwnedProcessGroup | None = None
    try:
        policy = read_capture_policy(anchor)
        bash_path = _resolve_bash()
        lineage_valid = (
            lineage_ref is not None
            and attempt_id is not None
            and validate_lineage_reference(lineage_ref, attempt_id)
        )
        launch_direct = requested_mode == "direct" and lineage_valid
        effective_direct = launch_direct or policy.disabled
        if launch_direct:
            effective_reason = "launch_authorized_direct"
        elif policy.disabled:
            effective_reason = "project_policy_disabled"
        else:
            effective_reason = "capture_enabled"
        if lineage_valid:
            assert lineage_ref is not None
            assert attempt_id is not None
            observation_recorded = record_runner_observation(
                lineage_ref,
                attempt_id,
                effective_mode="direct" if effective_direct else "capture",
                reason=effective_reason,
                project_policy_disabled=policy.disabled,
            )
            if not observation_recorded:
                raise CaptureSetupError.unknown("runner observation recording failed")
        if effective_direct:
            try:
                process = _spawn_bash(bash_path, command, capture_output=False)
                return _normalized_returncode(process.wait())
            except BaseException as exc:
                logger.error("direct_shell_execution_failed", exc_info=True)
                direct_settlement = (
                    _settle_failed_capture(process) if process is not None else None
                )
                if not isinstance(exc, _CAPTURE_RUNTIME_ERRORS):
                    raise
                return _capture_replay.capture_failure_return(
                    _capture_replay.failure_transport(
                        reason=_capture_failure_policy.runtime_failure_reason(exc),
                        stage="direct process",
                        detail="direct process failed",
                        shell_returncode=(
                            None if direct_settlement is None else direct_settlement.returncode
                        ),
                        settlement=direct_settlement,
                    )
                )

        failure_stage = "capture root open"
        try:
            root = open_capture_root(anchor, create=True)
            failure_stage = "capture lifecycle store open"
            lifecycle = CaptureLifecycleStore.from_open_authorities(
                anchor,
                root,
                lock_wait=HOT_PATH_LOCK_WAIT,
                capacity=policy.capacity,
            )
            failure_stage = "capture artifact creation"
            artifact = create_capture_artifact(root, capture_id, lifecycle)
            failure_stage = "capture writer duplication"
            artifact_writer_fd = _duplicate_artifact_writer(artifact)
        except BaseException as exc:
            reason = _capture_failure_policy.runtime_failure_reason(exc)
            failure = _capture_replay.failure_transport(
                reason=reason,
                stage=(
                    "capture setup"
                    if reason in _capture_failure_policy.CAPACITY_FAILURE_REASONS
                    else failure_stage
                ),
                detail=f"{failure_stage} failed",
                shell_returncode=None,
                settlement=None,
            )
            if not isinstance(exc, _CAPTURE_RUNTIME_ERRORS):
                _capture_replay.capture_failure_return(failure)
                raise
            return _capture_replay.capture_failure_return(failure)
        command_outcome: CommandOutcome | None = None
        command_returncode: int | None = None
        settlement: _capture_replay.RunnerSettlementEvidence | None = None
        delivery_value: (
            FinalizedCapture | PublishedCaptureReference | UnavailableCaptureReference | None
        ) = None
        delivery_attempting = False
        delivery_bytes_flushed = False
        terminal_committed = False
        finalized_capture: FinalizedCapture | None = None
        failure_stage = "capture process spawn"
        try:
            process = _spawn_bash(bash_path, command, capture_output=True)
            failure_stage = "capture readback"
            result = _drain_capture(process, artifact_writer_fd, policy.inline_bytes)
            artifact.close_drain_writer()
            failure_stage = "capture process wait"
            command_outcome = CommandOutcome.from_wait_result(process.wait())
            command_returncode = command_outcome.shell_returncode
            if result.truncated:
                failure_stage = "capture truncated-state commit"
                lifecycle.commit_capture_failure(
                    artifact.authority,
                    CaptureFailureEvidence(
                        stage="artifact_read",
                        detail="capture output drain truncated after process-group settlement",
                    ),
                    observed_size=max(0, os.fstat(artifact.fd).st_size),
                )
                terminal_committed = True
                return _capture_replay.capture_failure_return(
                    _capture_replay.failure_transport(
                        reason=CaptureFailureReason.FILESYSTEM_IO,
                        stage=failure_stage,
                        detail=("capture output drain truncated after process-group settlement"),
                        shell_returncode=command_returncode,
                        settlement=None,
                    )
                )
            if result.write_error is not None:
                failure_stage = "capture failed-state commit"
                lifecycle.commit_capture_failure(
                    artifact.authority,
                    CaptureFailureEvidence(
                        stage="artifact_write",
                        detail="capture artifact write failed",
                    ),
                    observed_size=max(0, os.fstat(artifact.fd).st_size),
                )
                terminal_committed = True
                return _capture_replay.capture_failure_return(
                    _capture_replay.failure_transport(
                        reason=CaptureFailureReason.FILESYSTEM_IO,
                        stage="artifact_write",
                        detail="capture artifact write failed",
                        shell_returncode=command_returncode,
                        settlement=None,
                    )
                )
            failure_stage = "capture artifact integrity verification"
            finalized_at, retention_deadline = lifecycle.capture_finalization_window()
            verified = verify_capture_snapshot(
                fd=artifact.fd,
                capture_id=artifact.authority.capture_id,
                incarnation=artifact.authority.incarnation,
                project_identity=(
                    anchor.identity.device,
                    anchor.identity.inode,
                ),
                root_identity=(root.identity.device, root.identity.inode),
                carrier_name=artifact.name,
                carrier_identity=(artifact.identity.device, artifact.identity.inode),
                measurement=result.measurement,
                command_outcome=command_outcome,
                expected_revision=artifact.authority.expected_revision,
                finalized_at=finalized_at,
                retention_deadline=retention_deadline,
            )
            failure_stage = "capture finalization"
            try:
                finalized = lifecycle.commit_verified_snapshot(
                    verified,
                    issue_reference=result.measurement.total_bytes > policy.inline_bytes,
                )
            except _CAPTURE_RUNTIME_ERRORS as finalization_exc:
                finalization_reason = _capture_failure_policy.runtime_failure_reason(
                    finalization_exc
                )
                disposition_entry = _capture_failure_policy.FAILURE_DISPOSITIONS.get(
                    finalization_reason
                )
                if (
                    disposition_entry is None
                    or disposition_entry.disposition
                    is _capture_failure_policy.CaptureFailureDisposition.DISCARD_OUTPUT
                    or verified is None
                    or command_outcome is None
                ):
                    raise  # propagate to the enclosing runtime-error handler
                finalization_detail = _capture_replay._bounded_detail(f"{failure_stage} failed")
                try:
                    lifecycle.commit_capture_failure(
                        artifact.authority,
                        CaptureFailureEvidence(
                            stage=_capture_replay._failure_stage(failure_stage),
                            detail=finalization_detail,
                            failure_reason=finalization_reason.value,
                        ),
                        observed_size=max(0, os.fstat(artifact.fd).st_size),
                    )
                    terminal_committed = True
                except _CAPTURE_RUNTIME_ERRORS:
                    logger.error("capture_degraded_failure_commit_failed", exc_info=True)
                degraded_payload = _capture_replay.render_degraded_capture(
                    verified, reason_code=finalization_reason.value
                )
                try:
                    _capture_replay.write_and_flush_hook_stdout(degraded_payload)
                except _CAPTURE_RUNTIME_ERRORS as delivery_exc:
                    raise finalization_exc from delivery_exc
                degraded_failure = _capture_replay.failure_transport(
                    reason=finalization_reason,
                    stage=failure_stage,
                    detail=finalization_detail,
                    shell_returncode=command_returncode,
                    settlement=None,
                )
                try:
                    _capture_replay._emit_degraded(degraded_failure)
                except _CAPTURE_RUNTIME_ERRORS:
                    pass  # output already delivered; lost diagnostic
                return _capture_replay.degraded_delivery_return(command_returncode)
            finalized_capture = finalized
            terminal_committed = True
            failure_stage = "capture reader transfer"
            with artifact.transfer_to_reader(lifecycle, finalized):
                if finalized.issuance is None:
                    delivery_value = finalized
                else:
                    failure_stage = "capture reference publication"
                    delivery_value = _publish_oversized_capture(
                        anchor,
                        root,
                        artifact,
                        lifecycle,
                        finalized,
                    )
                failure_stage = "capture delivery begin"
                _capture_delivery.transition_delivery_checked(
                    lifecycle,
                    delivery_value,
                    expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
                    target=CaptureDeliveryStatus.ATTEMPTING,
                    lifecycle_error=CaptureLifecycleError,
                    runtime_errors=_CAPTURE_RUNTIME_ERRORS,
                )
                delivery_attempting = True
                failure_stage = "capture replay rendering"
                if isinstance(delivery_value, FinalizedCapture):
                    payload = _capture_replay.render_inline_capture(delivery_value)
                elif isinstance(
                    delivery_value,
                    (PublishedCaptureReference, UnavailableCaptureReference),
                ):
                    payload = _capture_replay.render_oversized_capture(delivery_value)
                else:
                    raise CaptureSetupError.unknown("capture delivery value is unavailable")
                failure_stage = "capture stdout write and flush"

                def record_delivery_progress(_written: int) -> None:
                    nonlocal delivery_bytes_flushed
                    delivery_bytes_flushed = True

                _capture_replay.write_and_flush_hook_stdout(
                    payload,
                    on_progress=record_delivery_progress,
                )
                failure_stage = "capture delivery finish"
                _capture_delivery.transition_delivery_checked(
                    lifecycle,
                    delivery_value,
                    expected=CaptureDeliveryStatus.ATTEMPTING,
                    target=CaptureDeliveryStatus.DELIVERED,
                    lifecycle_error=CaptureLifecycleError,
                    runtime_errors=_CAPTURE_RUNTIME_ERRORS,
                )
                delivery_attempting = False
            return command_returncode
        except BaseException as exc:
            logger.error("capture_shell_execution_failed", exc_info=True)
            if command_outcome is None and process is not None:
                settlement = _settle_failed_capture(process)
            transport_reason = _capture_failure_policy.runtime_failure_reason(exc)
            transport_detail = f"{failure_stage} failed"
            if isinstance(exc, CaptureSetupError):
                transport_reason = exc.reason
                transport_detail = exc.detail
            if not terminal_committed:
                try:
                    failure_detail = _capture_replay._bounded_detail(f"{failure_stage} failed")
                    lifecycle.commit_capture_failure(
                        artifact.authority,
                        CaptureFailureEvidence(
                            stage=_capture_replay._failure_stage(failure_stage),
                            detail=failure_detail,
                            settlement_returncode=(
                                None if settlement is None else settlement.returncode
                            ),
                            failure_reason=transport_reason.value,
                        ),
                        observed_size=max(0, os.fstat(artifact.fd).st_size),
                    )
                    terminal_committed = True
                except _CAPTURE_RUNTIME_ERRORS:
                    logger.error("capture_failure_commit_failed", exc_info=True)
            elif finalized_capture is not None:
                _capture_delivery.settle_finalized_failure(
                    lifecycle,
                    finalized_capture,
                    delivery_value,
                    delivery_attempting=delivery_attempting,
                    delivery_bytes_flushed=delivery_bytes_flushed,
                    lifecycle_error=CaptureLifecycleError,
                    runtime_errors=_CAPTURE_RUNTIME_ERRORS,
                )
            if not isinstance(exc, _CAPTURE_RUNTIME_ERRORS):
                raise
            return _capture_replay.capture_failure_return(
                _capture_replay.failure_transport(
                    reason=transport_reason,
                    stage=failure_stage,
                    detail=transport_detail,
                    shell_returncode=command_returncode,
                    settlement=settlement,
                )
            )
    finally:
        global _BYTE_PRESSURE_OBSERVED  # noqa: PLW0603
        if lifecycle is not None and lifecycle.byte_pressure_observed:
            _BYTE_PRESSURE_OBSERVED = True
        if process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if artifact is not None:
            try:
                artifact.close_drain_writer()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if artifact is not None:
            try:
                artifact.close_artifact_fd()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        if root is not None:
            try:
                root.close()
            except _CAPTURE_RUNTIME_ERRORS:
                pass
        try:
            anchor.close()
        except _CAPTURE_RUNTIME_ERRORS:
            pass
        if artifact is not None:
            try:
                artifact.release_lease()
            except _CAPTURE_RUNTIME_ERRORS:
                pass


def _dispatch_runner(request: CaptureRequest) -> int:
    if request.action == "reject":
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_request",
                "capture request rejected before command execution",
                reason=CaptureFailureReason.UNKNOWN_SETUP,
            )
        )
    try:
        if request.command is None:
            raise CaptureSetupError.unknown("run request is missing command")
        return run_capture(
            request.command,
            request.cwd,
            request.capture_id,
            requested_mode=request.mode,
            attempt_id=request.attempt_id,
            lineage_ref=request.lineage_ref,
        )
    except CaptureSetupError as exc:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_setup",
                exc.detail,
                reason=exc.reason,
            )
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_runner",
                "capture runner failed",
                reason=_capture_failure_policy.runtime_failure_reason(exc),
            )
        )


_BYTE_PRESSURE_OBSERVED = False
_RUNNER_TAIL_OWNER = "runner_tail"


def _emit_runner_tail_crash_diagnostic() -> None:
    event = PolicyEvent(
        hook_id=_RUNNER_TAIL_OWNER,
        hook_version=1,
        event="capture_cleanup",
        decision="failed",
        reason_code="runner-tail reconciliation raised an unexpected exception",
    )
    _capture_reconcile.emit_bounded_diagnostic(
        render_provenance_prefix(event),
        maximum_bytes=_capture_reconcile.DIAGNOSTIC_MAX_BYTES,
        write=sys.stderr.write,
    )


def _sweep_after_runner(requested_cwd: str) -> None:
    global _BYTE_PRESSURE_OBSERVED  # noqa: PLW0603
    byte_pressure_observed = _BYTE_PRESSURE_OBSERVED
    _BYTE_PRESSURE_OBSERVED = False
    try:
        budget = (
            _capture_types.TRANSITION_RESCUE_BUDGET
            if byte_pressure_observed
            else _capture_reconcile.RUNNER_TAIL_BUDGET
        )
        outcome = _capture_reconcile.reconcile_capture_store(requested_cwd, budget)
        _capture_reconcile.emit_owner_diagnostic(
            outcome, owner=_RUNNER_TAIL_OWNER, write=sys.stderr.write
        )
    except _CAPTURE_RUNTIME_ERRORS:
        _emit_runner_tail_crash_diagnostic()


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_invocation",
                "invalid capture runner invocation",
                reason=CaptureFailureReason.UNKNOWN_SETUP,
            )
        )
    try:
        request = decode_capture_request(args[0])
    except CaptureProtocolError:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_invocation",
                "invalid capture runner invocation",
                reason=CaptureFailureReason.UNKNOWN_SETUP,
            )
        )
    try:
        try:
            user_result = _dispatch_runner(request)
        except _CAPTURE_RUNTIME_ERRORS as exc:
            user_result = _capture_replay.capture_failure_return(
                _capture_replay.runner_failure(
                    "capture_runner",
                    "capture runner failed",
                    reason=_capture_failure_policy.runtime_failure_reason(exc),
                )
            )
    finally:
        _sweep_after_runner(request.cwd)
    return user_result
