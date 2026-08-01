"""Descriptor-anchored shell-capture authority and isolated runner."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import stat
import subprocess
import sys
from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _delivery as _capture_delivery
    from autoskillit.hooks._capture import _replay as _capture_replay
    from autoskillit.hooks._capture._authority import (
        _DIRECTORY_FLAGS,
        _READ_FLAGS,
        CAPTURE_PATH_COMPONENTS,
        CaptureRoot,
        CaptureSetupError,
        CaptureStoreAbsentError,
        FileIdentity,
        ProjectAnchor,
        _open_directory_component,
        _same_identity,
        open_capture_lifecycle,
        open_capture_root,
        open_project_anchor,
    )
    from autoskillit.hooks._capture._observation import (
        record_runner_observation,
        validate_lineage_reference,
    )
    from autoskillit.hooks._capture._reader import VerifiedCaptureReader
    from autoskillit.hooks._capture._snapshot import (
        CaptureWriteAuthority,
        CommandOutcome,
        FinalizedCapture,
        IssuedCaptureReference,
        PublishedCaptureReference,
        UnavailableCaptureReference,
        verify_capture_snapshot,
    )
    from autoskillit.hooks._capture._types import CaptureFailureEvidence
    from autoskillit.hooks._capture_contract import (
        _CAPTURE_ID_RE,
        _MAX_COMMAND_BYTES,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        decode_capture_request,
    )
    from autoskillit.hooks._capture_lifecycle import (
        CaptureDeliveryStatus,
        CaptureLifecycleError,
        CaptureLifecycleStore,
        CaptureTransitionCommittedError,
    )
    from autoskillit.hooks._capture_process import (
        _TRUSTED_BASH_CANDIDATES,
        OwnedProcessGroup,
        _DrainResult,
        _normalized_returncode,
        _own_spawned_process,
        _settle_failed_capture,
        _spawn_bash,
    )
    from autoskillit.hooks._capture_process import (
        _drain_capture as _drain_owned_capture,
    )
    from autoskillit.hooks._capture_process import (
        _resolve_bash as _resolve_trusted_bash,
    )
    from autoskillit.hooks._hook_settings import (
        HOOK_CONFIG_FILENAME,
        HOOK_CONFIG_OVERLAY_FILENAME,
        merge_hook_configs,
    )
else:
    from _capture import _delivery as _capture_delivery
    from _capture import _replay as _capture_replay
    from _capture._authority import (
        _DIRECTORY_FLAGS,
        _READ_FLAGS,
        CAPTURE_PATH_COMPONENTS,
        CaptureRoot,
        CaptureSetupError,
        CaptureStoreAbsentError,
        FileIdentity,
        ProjectAnchor,
        _open_directory_component,
        _same_identity,
        open_capture_lifecycle,
        open_capture_root,
        open_project_anchor,
    )
    from _capture._observation import (
        record_runner_observation,
        validate_lineage_reference,
    )
    from _capture._reader import VerifiedCaptureReader
    from _capture._snapshot import (
        CaptureWriteAuthority,
        CommandOutcome,
        FinalizedCapture,
        IssuedCaptureReference,
        PublishedCaptureReference,
        UnavailableCaptureReference,
        verify_capture_snapshot,
    )
    from _capture._types import CaptureFailureEvidence
    from _capture_contract import (
        _CAPTURE_ID_RE,
        _MAX_COMMAND_BYTES,
        CaptureLineageRef,
        CaptureProtocolError,
        CaptureRequest,
        decode_capture_request,
    )
    from _capture_lifecycle import (
        CaptureDeliveryStatus,
        CaptureLifecycleError,
        CaptureLifecycleStore,
        CaptureTransitionCommittedError,
    )
    from _capture_process import (
        _TRUSTED_BASH_CANDIDATES,
        OwnedProcessGroup,
        _DrainResult,
        _normalized_returncode,
        _own_spawned_process,
        _settle_failed_capture,
        _spawn_bash,
    )
    from _capture_process import (
        _drain_capture as _drain_owned_capture,
    )
    from _capture_process import (
        _resolve_bash as _resolve_trusted_bash,
    )
    from _hook_settings import (
        HOOK_CONFIG_FILENAME,
        HOOK_CONFIG_OVERLAY_FILENAME,
        merge_hook_configs,
    )

__all__ = [
    "CAPTURE_PATH_COMPONENTS",
    "CaptureArtifact",
    "CapturePolicy",
    "CaptureRoot",
    "CaptureSetupError",
    "ProjectAnchor",
    "create_capture_artifact",
    "open_capture_lifecycle",
    "open_capture_root",
    "open_project_anchor",
    "read_capture_policy",
    "run_capture",
    "verify_reference_publication_binding",
]

_DEFAULT_INLINE_BYTES = 12_000
_MAX_INLINE_BYTES = 1_000_000
_MAX_POLICY_FILE_BYTES = 64 * 1024
_MAX_CLEANUP_DETAIL_BYTES = 240
_CAPTURE_RUNTIME_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
    TypeError,
    UnicodeError,
    ValueError,
)
_ARTIFACT_FACTORY_TOKEN = object()
logger = logging.getLogger(__name__)  # noqa: TID251 - isolated stdlib runner
logger.addHandler(logging.NullHandler())
logger.propagate = False


@dataclass(frozen=True, slots=True)
class CaptureArtifact:
    fd: int
    name: str
    identity: FileIdentity
    lease_fd: int
    authority: CaptureWriteAuthority
    drain_writer_fd: int = -1
    _factory_token: InitVar[object | None] = None

    def __post_init__(self, _factory_token: object | None) -> None:
        if _factory_token is not _ARTIFACT_FACTORY_TOKEN:
            raise CaptureSetupError("CaptureArtifact must be created by create_capture_artifact")

    def close_artifact_fd(self) -> None:
        if self.fd >= 0:
            descriptor = self.fd
            object.__setattr__(self, "fd", -1)
            os.close(descriptor)

    def release_lease(self) -> None:
        if self.lease_fd >= 0:
            descriptor = self.lease_fd
            object.__setattr__(self, "lease_fd", -1)
            os.close(descriptor)

    def close_drain_writer(self) -> None:
        if self.drain_writer_fd >= 0:
            descriptor = self.drain_writer_fd
            object.__setattr__(self, "drain_writer_fd", -1)
            os.close(descriptor)

    def transfer_to_reader(
        self,
        lifecycle: CaptureLifecycleStore,
        finalized: FinalizedCapture,
    ) -> VerifiedCaptureReader:
        if self.drain_writer_fd >= 0:
            raise CaptureSetupError("capture drain writer is still open")
        if self.fd < 0 or self.lease_fd < 0:
            raise CaptureSetupError("capture artifact ownership is unavailable")
        carrier_fd = self.fd
        lease_fd = self.lease_fd
        object.__setattr__(self, "fd", -1)
        object.__setattr__(self, "lease_fd", -1)
        try:
            os.close(lease_fd)
        except OSError as exc:
            try:
                os.close(carrier_fd)
            except OSError:
                pass
            raise CaptureSetupError("cannot transfer capture carrier lease") from exc
        return lifecycle._adopt_verified_capture(finalized, carrier_fd)


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    disabled: bool = False
    inline_bytes: int = _DEFAULT_INLINE_BYTES


def create_capture_artifact(
    root: CaptureRoot,
    capture_id: str,
    lifecycle: CaptureLifecycleStore,
) -> CaptureArtifact:
    if not _CAPTURE_ID_RE.fullmatch(capture_id):
        raise CaptureSetupError("invalid capture id")
    try:
        fd, lease_fd, public_name, raw_identity, authority = lifecycle.create_artifact(capture_id)
        identity = FileIdentity(device=raw_identity[0], inode=raw_identity[1])
        return CaptureArtifact(
            fd=fd,
            name=public_name,
            identity=identity,
            lease_fd=lease_fd,
            authority=authority,
            _factory_token=_ARTIFACT_FACTORY_TOKEN,
        )
    except (CaptureLifecycleError, CaptureTransitionCommittedError, OSError) as exc:
        raise CaptureSetupError("cannot create managed capture artifact") from exc


def _duplicate_artifact_writer(artifact: CaptureArtifact) -> int:
    writer_fd = -1
    try:
        if artifact.fd < 0 or artifact.drain_writer_fd >= 0:
            raise CaptureSetupError("capture drain writer ownership is unavailable")
        writer_fd = os.dup(artifact.fd)
        if not _same_identity(writer_fd, artifact.identity):
            raise CaptureSetupError("duplicated capture artifact identity changed")
        object.__setattr__(artifact, "drain_writer_fd", writer_fd)
        return writer_fd
    except (CaptureSetupError, OSError) as exc:
        if writer_fd >= 0:
            try:
                os.close(writer_fd)
            except OSError:
                pass
        if isinstance(exc, CaptureSetupError):
            raise
        raise CaptureSetupError("cannot duplicate capture artifact fd") from exc


def _read_bounded_file_at(directory_fd: int, name: str) -> dict:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_mode & stat.S_IWOTH:
            return {}
        data = bytearray()
        while len(data) <= _MAX_POLICY_FILE_BYTES:
            chunk = os.read(fd, min(8192, _MAX_POLICY_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > _MAX_POLICY_FILE_BYTES:
            return {}
        parsed = json.loads(data)
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}
    finally:
        os.close(fd)


def _policy_inline_bytes(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return _DEFAULT_INLINE_BYTES
    return min(value, _MAX_INLINE_BYTES)


def read_capture_policy(anchor: ProjectAnchor) -> CapturePolicy:
    autoskillit_fd = -1
    temp_fd = -1
    try:
        try:
            autoskillit_fd = _open_directory_component(
                anchor.fd, CAPTURE_PATH_COMPONENTS[0], create=False
            )
            temp_fd = _open_directory_component(
                autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], create=False
            )
        except CaptureSetupError:
            return CapturePolicy()
        base = _read_bounded_file_at(temp_fd, HOOK_CONFIG_FILENAME)
        overlay = _read_bounded_file_at(temp_fd, HOOK_CONFIG_OVERLAY_FILENAME)
        merged = merge_hook_configs(base, overlay)
        section = merged.get("output_budget_policy", {})
        if not isinstance(section, dict):
            section = {}
        return CapturePolicy(
            disabled=section.get("disabled") is True,
            inline_bytes=_policy_inline_bytes(section.get("shell_max_inline_bytes")),
        )
    finally:
        try:
            if temp_fd >= 0:
                os.close(temp_fd)
        finally:
            if autoskillit_fd >= 0:
                os.close(autoskillit_fd)


def _open_and_match_directory(parent_fd: int, name: str, expected: FileIdentity) -> int:
    try:
        fd = _open_directory_component(parent_fd, name, create=False)
    except CaptureSetupError:
        return -1
    try:
        matches = _same_identity(fd, expected)
    except BaseException:
        os.close(fd)
        raise
    if not matches:
        os.close(fd)
        return -1
    return fd


def verify_reference_publication_binding(
    anchor: ProjectAnchor,
    root: CaptureRoot,
    artifact: CaptureArtifact,
    issuance: IssuedCaptureReference,
) -> bool:
    """Verify that an issued tuple still resolves through the retained authorities."""

    if type(issuance) is not IssuedCaptureReference:
        raise CaptureSetupError("publication binding requires an issued reference")
    manifest = issuance.snapshot.manifest
    if (
        manifest.project_identity != (anchor.identity.device, anchor.identity.inode)
        or manifest.root_identity != (root.identity.device, root.identity.inode)
        or manifest.carrier_name != artifact.name
        or manifest.carrier_identity != (artifact.identity.device, artifact.identity.inode)
    ):
        return False
    opened: list[int] = []
    try:
        try:
            project_path = Path(os.path.realpath(anchor.supplied_path))
            project_fd = os.open(
                project_path,
                _DIRECTORY_FLAGS,
            )
        except OSError:
            return False
        opened.append(project_fd)
        if not _same_identity(project_fd, anchor.identity):
            return False

        autoskillit_fd = _open_and_match_directory(
            project_fd, CAPTURE_PATH_COMPONENTS[0], root.autoskillit_identity
        )
        if autoskillit_fd < 0:
            return False
        opened.append(autoskillit_fd)

        temp_fd = _open_and_match_directory(
            autoskillit_fd, CAPTURE_PATH_COMPONENTS[1], root.temp_identity
        )
        if temp_fd < 0:
            return False
        opened.append(temp_fd)

        capture_fd = _open_and_match_directory(temp_fd, CAPTURE_PATH_COMPONENTS[2], root.identity)
        if capture_fd < 0:
            return False
        opened.append(capture_fd)

        try:
            current_artifact_fd = os.open(artifact.name, _READ_FLAGS, dir_fd=capture_fd)
        except OSError:
            return False
        opened.append(current_artifact_fd)
        current_value = os.fstat(current_artifact_fd)
        if (
            FileIdentity.from_stat(current_value) != artifact.identity
            or not stat.S_ISREG(current_value.st_mode)
            or current_value.st_nlink != 1
            or current_value.st_mode & stat.S_IWOTH
        ):
            return False
        return True
    finally:
        for fd in reversed(opened):
            os.close(fd)


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
        raise CaptureSetupError("oversized capture lacks issued reference")
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
        raise CaptureSetupError("invalid capture authority")
    try:
        command_bytes = command.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CaptureSetupError("invalid command encoding") from exc
    if (
        not _CAPTURE_ID_RE.fullmatch(capture_id)
        or "\x00" in command
        or len(command_bytes) > _MAX_COMMAND_BYTES
    ):
        raise CaptureSetupError("invalid capture request")

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
                raise CaptureSetupError("runner observation recording failed")
        if effective_direct:
            try:
                spawned = _spawn_bash(anchor, bash_path, command, capture_output=False)
                process = _own_spawned_process(spawned, capture_output=False)
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
                        stage="direct process",
                        detail=f"direct process failed: {type(exc).__name__}: {exc}",
                        shell_returncode=(
                            None if direct_settlement is None else direct_settlement.returncode
                        ),
                        settlement=direct_settlement,
                    )
                )

        root = open_capture_root(anchor, create=True)
        lifecycle = CaptureLifecycleStore.from_open_authorities(anchor, root)
        artifact = create_capture_artifact(root, capture_id, lifecycle)
        artifact_writer_fd = _duplicate_artifact_writer(artifact)
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
            spawned = _spawn_bash(anchor, bash_path, command, capture_output=True)
            process = _own_spawned_process(spawned, capture_output=True)
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
            finalized = lifecycle.commit_verified_snapshot(
                verified,
                issue_reference=result.measurement.total_bytes > policy.inline_bytes,
            )
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
                    raise CaptureSetupError("capture delivery value is unavailable")
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
            recovery_detail = ""
            if command_outcome is None and process is not None:
                settlement = _settle_failed_capture(process)
            if not terminal_committed:
                try:
                    failure_detail = _capture_replay._bounded_detail(
                        f"{type(exc).__name__}: {exc}"
                    )
                    lifecycle.commit_capture_failure(
                        artifact.authority,
                        CaptureFailureEvidence(
                            stage=_capture_replay._failure_stage(failure_stage),
                            detail=failure_detail,
                            settlement_returncode=(
                                None if settlement is None else settlement.returncode
                            ),
                        ),
                        observed_size=max(0, os.fstat(artifact.fd).st_size),
                    )
                    terminal_committed = True
                except _CAPTURE_RUNTIME_ERRORS as recovery_error:
                    recovery_detail = (
                        "; failed-state recovery also failed: "
                        f"{type(recovery_error).__name__}: {recovery_error}"
                    )
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
                    stage=failure_stage,
                    detail=(
                        f"{failure_stage} failed: {type(exc).__name__}: {exc}{recovery_detail}"
                    ),
                    shell_returncode=command_returncode,
                    settlement=settlement,
                )
            )
    finally:
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
            )
        )
    try:
        if request.command is None:
            raise CaptureSetupError("run request is missing command")
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
            _capture_replay.runner_failure("capture_setup", str(exc))
        )
    except (OSError, subprocess.SubprocessError):
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure("capture_runner", "capture runner failed")
        )


def _emit_cleanup_failure(detail: str) -> None:
    safe_detail = " ".join(detail.split()).replace("]", "\\u005d")
    bounded_detail = safe_detail.encode("utf-8")[:_MAX_CLEANUP_DETAIL_BYTES].decode(
        "utf-8",
        errors="ignore",
    )
    try:
        sys.stderr.write(f"[AutoSkillit shell capture cleanup failed: {bounded_detail}]\n")
    except _CAPTURE_RUNTIME_ERRORS:
        pass


def _sweep_after_runner(requested_cwd: str) -> None:
    try:
        with open_capture_lifecycle(requested_cwd, create=False) as lifecycle:
            outcome = lifecycle.sweep()
            if outcome.errors:
                _emit_cleanup_failure(f"cleanup deferred after {outcome.errors} errors")
    except CaptureStoreAbsentError:
        return
    except CaptureSetupError as exc:
        _emit_cleanup_failure(f"{type(exc).__name__}: {exc}")
    except (CaptureLifecycleError, OSError) as exc:
        _emit_cleanup_failure(f"{type(exc).__name__}: {exc}")


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_invocation", "invalid capture runner invocation"
            )
        )
    try:
        request = decode_capture_request(args[0])
    except CaptureProtocolError:
        return _capture_replay.capture_failure_return(
            _capture_replay.runner_failure(
                "capture_invocation", "invalid capture runner invocation"
            )
        )
    try:
        user_result = _dispatch_runner(request)
    except _CAPTURE_RUNTIME_ERRORS:
        user_result = _capture_replay.capture_failure_return(
            _capture_replay.runner_failure("capture_runner", "capture runner failed")
        )
    _sweep_after_runner(request.cwd)
    return user_result


if __name__ == "__main__":
    sys.exit(_main())
