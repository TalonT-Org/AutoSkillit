"""Durable transactions behind :class:`CaptureLifecycleStore`.

The store remains the public, bound interface and the owner of lock acquisition.
These functions contain transaction mechanics while deliberately calling the
store's bound seams so fault injection and retry behavior remain observable.
"""

from __future__ import annotations

import importlib
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _capacity as _capture_capacity
    from autoskillit.hooks._capture import _failure_policy as _capture_failure_policy
    from autoskillit.hooks._capture import _ledger as _capture_ledger
    from autoskillit.hooks._capture import _ledger_view as _capture_ledger_view
    from autoskillit.hooks._capture import _lifecycle_record as _capture_lifecycle_record
    from autoskillit.hooks._capture import _module_identity
    from autoskillit.hooks._capture import _reference as _capture_reference
    from autoskillit.hooks._capture import _snapshot as _capture_snapshot
    from autoskillit.hooks._capture import _types as _capture_types
    from autoskillit.hooks._capture_lifecycle import _errors
    from autoskillit.hooks._capture_lifecycle._store import CaptureLifecycleStore
else:
    _capture_capacity = importlib.import_module("_capture._capacity")
    _capture_failure_policy = importlib.import_module("_capture._failure_policy")
    _capture_ledger = importlib.import_module("_capture._ledger")
    _capture_ledger_view = importlib.import_module("_capture._ledger_view")
    _capture_lifecycle_record = importlib.import_module("_capture._lifecycle_record")
    _module_identity = importlib.import_module("_capture._module_identity")
    _capture_reference = importlib.import_module("_capture._reference")
    _capture_snapshot = importlib.import_module("_capture._snapshot")
    _capture_types = importlib.import_module("_capture._types")
    _errors = importlib.import_module("_capture_lifecycle._errors")

_module_identity.register_module_aliases(__name__)

CaptureCapacityReason = _capture_types.CaptureCapacityReason
CaptureDeliveryStatus = _capture_lifecycle_record.CaptureDeliveryStatus
CaptureFailureEvidence = _capture_types.CaptureFailureEvidence
CaptureLifecycleRecord = _capture_lifecycle_record.CaptureLifecycleRecord
CaptureReferenceStatus = _capture_lifecycle_record.CaptureReferenceStatus
CaptureRetentionPhase = _capture_lifecycle_record.CaptureRetentionPhase
CaptureSnapshotStatus = _capture_lifecycle_record.CaptureSnapshotStatus
CaptureState = _capture_lifecycle_record.CaptureState
CaptureStatus = _capture_lifecycle_record.CaptureStatus
CaptureTransitionCommittedError = _capture_lifecycle_record.CaptureTransitionCommittedError
CaptureWriteAuthority = _capture_snapshot.CaptureWriteAuthority
FinalizedCapture = _capture_snapshot.FinalizedCapture
VerifiedCaptureSnapshot = _capture_snapshot.VerifiedCaptureSnapshot
CaptureLifecycleError = _errors.CaptureLifecycleError
CaptureLedgerError = _errors.CaptureLedgerError
CaptureCapacityError = _errors.CaptureCapacityError

_BYTE_CAPACITY_REASONS = frozenset(
    {
        CaptureCapacityReason.PROJECTED_COMPACTED_BYTES,
        CaptureCapacityReason.HARD_LEDGER_CAPACITY,
    }
)


def _record_to_dict(record: CaptureLifecycleRecord) -> dict[str, object]:
    try:
        return _capture_lifecycle_record.record_to_dict(record)
    except _capture_lifecycle_record.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _validate_successor(
    previous: CaptureLifecycleRecord,
    candidate: CaptureLifecycleRecord,
) -> None:
    try:
        _capture_lifecycle_record.validate_successor(previous, candidate)
    except _capture_lifecycle_record.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


def _append_locked(
    store: CaptureLifecycleStore,
    record: CaptureLifecycleRecord,
    records: Mapping[str, CaptureLifecycleRecord],
    compaction_epoch: int,
    size: int,
    *,
    compaction_threshold_bytes: int,
) -> None:
    if record.capture_id in store._ledger_view.opaque_capture_ids:
        raise CaptureLedgerError("capture lifecycle state is opaque")
    previous = records.get(record.capture_id)
    if previous is None:
        if record.revision != 1:
            raise CaptureLedgerError("new lifecycle record must start at revision one")
    else:
        _validate_successor(previous, record)
    try:
        frame = _capture_ledger.encode_frame(
            _record_to_dict(record),
            compaction_epoch=compaction_epoch,
        )
    except _capture_lifecycle_record.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc
    if size + len(frame) > min(
        compaction_threshold_bytes,
        store._capacity.compaction_high_bytes,
    ):
        latest = dict(records)
        latest[record.capture_id] = record
        store._compact_locked(latest, compaction_epoch + 1, candidate=record)
        return
    fd = store._open_ledger()
    try:
        _capture_ledger.write_all(fd, frame)
        os.fsync(fd)
        value = os.fstat(fd)
    except BaseException as primary_error:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            primary_error.add_note(
                "ledger descriptor cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        if isinstance(primary_error, _capture_lifecycle_record.LedgerCodecError):
            raise CaptureLedgerError(str(primary_error)) from primary_error
        raise
    try:
        os.close(fd)
    except OSError as exc:
        raise CaptureTransitionCommittedError(
            "lifecycle transition committed before descriptor cleanup failed"
        ) from exc
    store._ledger_view.note_append(records, record, compaction_epoch, value, frame)


def _compact_locked(
    store: CaptureLifecycleStore,
    records: Mapping[str, CaptureLifecycleRecord],
    compaction_epoch: int,
    candidate: CaptureLifecycleRecord | None,
    *,
    max_compaction_bytes: int,
    ledger_name: str,
    untrusted_write_bits: int,
    cloexec: int,
    nofollow: int,
) -> None:
    compacted = _capture_capacity.compacted_records(records, store._capacity)
    try:
        actionable_frames = {
            record.capture_id: _capture_ledger.encode_frame(
                _record_to_dict(record),
                compaction_epoch=compaction_epoch,
            )
            for record in compacted
        }
    except _capture_lifecycle_record.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc
    frames = [*store._ledger_view.opaque_frames, *actionable_frames.values()]
    compacted_bound = min(
        max_compaction_bytes,
        _capture_capacity.transition_compaction_bound(candidate, store._capacity),
    )
    if sum(map(len, frames)) > compacted_bound:
        raise CaptureLedgerError("lifecycle compaction exceeds bound")
    temp_name = f".capture-lifecycle-compact-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | cloexec | nofollow
    fd = os.open(temp_name, flags, 0o600, dir_fd=store._root_fd)
    try:
        _capture_ledger_view.validate_control_file(
            fd, temp_name, untrusted_write_bits, CaptureLifecycleError
        )
        for frame in frames:
            _capture_ledger.write_all(fd, frame)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=store._root_fd)
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.replace(
            temp_name,
            ledger_name,
            src_dir_fd=store._root_fd,
            dst_dir_fd=store._root_fd,
        )
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=store._root_fd)
        except OSError:
            pass
        raise
    os.fsync(store._root_fd)
    value = os.stat(ledger_name, dir_fd=store._root_fd, follow_symlinks=False)
    store._ledger_view.note_compaction(
        {record.capture_id: record for record in compacted},
        compaction_epoch,
        value,
        actionable_frames,
    )


def _transition_locked(
    store: CaptureLifecycleStore,
    *,
    records: dict[str, CaptureLifecycleRecord],
    compaction_epoch: int,
    ledger_size: int,
    authority: CaptureWriteAuthority,
    allowed_states: set[CaptureState],
    transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
) -> CaptureLifecycleRecord:
    if type(authority) is not CaptureWriteAuthority:
        raise CaptureLifecycleError("transition requires capture write authority")
    previous = records.get(authority.capture_id)
    if (
        previous is None
        or previous.incarnation != authority.incarnation
        or previous.revision != authority.expected_revision
        or previous.state not in allowed_states
    ):
        raise CaptureLifecycleError("stale or invalid lifecycle transition")
    candidate = transform(previous)
    if (
        type(candidate) is not CaptureLifecycleRecord
        or candidate.capture_id != previous.capture_id
        or candidate.incarnation != previous.incarnation
        or candidate.revision != previous.revision + 1
    ):
        raise CaptureLifecycleError("transition did not produce one valid successor")
    reason = _capture_capacity.transition_reason(
        records,
        candidate,
        compaction_epoch=compaction_epoch,
        spec=store._capacity,
        sizer=store._ledger_view.sizer,
    )
    if reason is not None:
        raise CaptureCapacityError(reason)
    store._append_locked(candidate, records, compaction_epoch, ledger_size)
    if store._sweep_budget is not None:
        store._sweep_transitions += 1
    return candidate


def _transition(
    store: CaptureLifecycleStore,
    authority: CaptureWriteAuthority,
    *,
    allowed_states: set[CaptureState],
    transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
) -> CaptureLifecycleRecord:
    with store._locked():
        records, compaction_epoch, size = store._load_locked()
        return store._transition_locked(
            records=records,
            compaction_epoch=compaction_epoch,
            ledger_size=size,
            authority=authority,
            allowed_states=allowed_states,
            transform=transform,
        )


def _transition_current(
    store: CaptureLifecycleStore,
    capture_id: str,
    incarnation: str,
    *,
    allowed_states: set[CaptureState],
    transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
) -> CaptureLifecycleRecord:
    with store._locked():
        records, compaction_epoch, size = store._load_locked()
        previous = records.get(capture_id)
        if previous is None or previous.incarnation != incarnation:
            raise CaptureLifecycleError("capture transition authority is unavailable")
        return store._transition_locked(
            records=records,
            compaction_epoch=compaction_epoch,
            ledger_size=size,
            authority=store._authority_for(previous),
            allowed_states=allowed_states,
            transform=transform,
        )


def _with_capacity_rescue(
    store: CaptureLifecycleStore,
    attempt: Callable[[], object],
    *,
    rescuable_reasons: frozenset[CaptureCapacityReason] | None = None,
) -> object:
    """Attempt an operation; on a rescuable ceiling, sweep and retry once.

    Each call of ``attempt`` must perform a complete fresh cycle: acquire the
    lock, reload the ledger, rebuild the candidate, and run the transition.
    Nothing loaded in a failed attempt may be reused by the retry.

    ``rescuable_reasons`` defaults to the soft projected-byte ceiling.
    Admission passes both byte ceilings and the bounded debt-assist reason.
    Record-count ceilings are not rescued here.
    """
    default_rescuable = frozenset({CaptureCapacityReason.PROJECTED_COMPACTED_BYTES})
    effective = rescuable_reasons if rescuable_reasons is not None else default_rescuable
    try:
        return attempt()
    except CaptureCapacityError as exc:
        if exc.reason in _BYTE_CAPACITY_REASONS:
            store.byte_pressure_observed = True
        if exc.reason not in effective:
            raise
        if exc.reason is CaptureCapacityReason.RECLAMATION_DEBT_ASSIST:
            limit = exc.assist_transition_limit
            if (
                type(limit) is not int
                or limit <= 0
                or limit > _capture_types.DEBT_ASSIST_MAX_TRANSITIONS
            ):
                raise CaptureLifecycleError(
                    "debt assist requires a valid transition limit"
                ) from exc
            budget = replace(
                _capture_types.DEBT_ASSIST_BUDGET,
                max_transitions=limit,
            )
        else:
            budget = _capture_types.TRANSITION_RESCUE_BUDGET
        store.sweep(budget)
        return attempt()


def reserve_capture(
    store: CaptureLifecycleStore,
    capture_id: str,
    *,
    capture_id_re: re.Pattern[str],
    retention_seconds: float,
) -> CaptureLifecycleRecord:
    if not capture_id_re.fullmatch(capture_id):
        raise CaptureLifecycleError("invalid capture id")

    def _attempt() -> CaptureLifecycleRecord:
        with store._locked():
            now = store._wall_clock()
            nonce = secrets.token_hex(8)
            incarnation = secrets.token_hex(16)
            record = CaptureLifecycleRecord(
                capture_id=capture_id,
                state=CaptureState.RESERVED,
                staging_name=f".capture-staging-{capture_id}-{nonce}",
                public_name=f"shell_{capture_id}.log",
                project_identity=store._project_identity,
                root_identity=store._root_identity,
                created_at=now,
                next_attempt_at=now + retention_seconds,
                incarnation=incarnation,
                revision=1,
            )
            records, compaction_epoch, size = store._load_locked()
            previous = records.get(capture_id)
            if previous is not None and previous.state is not CaptureState.DELETED:
                raise CaptureLifecycleError("capture id already reserved")
            decision = store._admission_reason(records, record, compaction_epoch, now)
            if decision.reason is not None:
                raise CaptureCapacityError(
                    decision.reason,
                    decision.assist_transition_limit,
                )
            store._append_locked(record, records, compaction_epoch, size)
        return record

    result = store._with_capacity_rescue(
        _attempt,
        rescuable_reasons=_BYTE_CAPACITY_REASONS
        | frozenset({CaptureCapacityReason.RECLAMATION_DEBT_ASSIST}),
    )
    if type(result) is not CaptureLifecycleRecord:
        raise CaptureLifecycleError("reserve_capture rescue produced invalid result")
    return result


def mark_staged(
    store: CaptureLifecycleStore,
    authority: CaptureWriteAuthority,
    artifact_identity: tuple[int, int],
) -> CaptureLifecycleRecord:
    if (
        not isinstance(artifact_identity, tuple)
        or len(artifact_identity) != 2
        or any(
            not isinstance(part, int) or isinstance(part, bool) or part < 0
            for part in artifact_identity
        )
    ):
        raise CaptureLifecycleError("invalid staged artifact identity")
    return store._transition(
        authority,
        allowed_states={CaptureState.RESERVED},
        transform=lambda record: replace(
            record,
            state=CaptureState.STAGED,
            artifact_identity=artifact_identity,
            revision=record.revision + 1,
        ),
    )


def mark_published(
    store: CaptureLifecycleStore,
    authority: CaptureWriteAuthority,
) -> CaptureLifecycleRecord:
    return store._transition(
        authority,
        allowed_states={CaptureState.STAGED},
        transform=lambda record: replace(
            record,
            state=CaptureState.PUBLISHED_WRITING,
            revision=record.revision + 1,
        ),
    )


def create_artifact(
    store: CaptureLifecycleStore,
    capture_id: str,
    *,
    artifact_flags: int,
    untrusted_write_bits: int,
) -> tuple[int, int, str, tuple[int, int], CaptureWriteAuthority]:
    record = store.reserve_capture(capture_id)
    authority = store._authority_for(record)
    fd = -1
    lease_fd = -1
    committed_error = CaptureTransitionCommittedError
    creation_errors = (CaptureLifecycleError, committed_error, OSError)
    recovery_errors = (_capture_snapshot.CaptureAuthorityError, *creation_errors)
    try:
        fd = os.open(record.staging_name, artifact_flags, 0o600, dir_fd=store._root_fd)
        value = os.fstat(fd)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or value.st_uid != os.geteuid()
            or value.st_mode & untrusted_write_bits
        ):
            raise CaptureLifecycleError("unsafe staged capture artifact")
        identity = _capture_ledger_view.identity(value)
        lease_fd = store.acquire_writer_lease(fd)
        staged = store.mark_staged(authority, identity)
        authority = store._authority_for(staged)
        os.fsync(fd)
        os.link(
            record.staging_name,
            record.public_name,
            src_dir_fd=store._root_fd,
            dst_dir_fd=store._root_fd,
            follow_symlinks=False,
        )
        staging_value = os.stat(
            record.staging_name,
            dir_fd=store._root_fd,
            follow_symlinks=False,
        )
        public_value = os.stat(
            record.public_name,
            dir_fd=store._root_fd,
            follow_symlinks=False,
        )
        if (
            _capture_ledger_view.identity(staging_value) != identity
            or _capture_ledger_view.identity(public_value) != identity
            or staging_value.st_nlink != 2
            or public_value.st_nlink != 2
        ):
            raise CaptureLifecycleError("capture publication identity changed")
        os.unlink(record.staging_name, dir_fd=store._root_fd)
        os.fsync(store._root_fd)
        public_value = os.stat(
            record.public_name,
            dir_fd=store._root_fd,
            follow_symlinks=False,
        )
        if _capture_ledger_view.identity(public_value) != identity or public_value.st_nlink != 1:
            raise CaptureLifecycleError("capture publication did not settle")
        published = store.mark_published(authority)
        authority = store._authority_for(published)
        return fd, lease_fd, record.public_name, identity, authority
    except creation_errors as primary_error:
        try:
            current = store.get_record(capture_id)
            if current is not None and current.state in {
                CaptureState.RESERVED,
                CaptureState.STAGED,
                CaptureState.PUBLISHED_WRITING,
            }:
                create_failure_reason = _capture_failure_policy.runtime_failure_reason(
                    primary_error
                )
                store.commit_capture_failure(
                    store._authority_for(current),
                    CaptureFailureEvidence(
                        stage="artifact_publication",
                        detail=f"{type(primary_error).__name__}: {primary_error}",
                        failure_reason=create_failure_reason.value,
                    ),
                    observed_size=os.fstat(fd).st_size if fd >= 0 else 0,
                )
        except recovery_errors as recovery_error:
            primary_error.add_note(
                "failed-state recovery also failed: "
                f"{type(recovery_error).__name__}: {recovery_error}"
            )
        if lease_fd >= 0:
            os.close(lease_fd)
        if fd >= 0:
            os.close(fd)
        raise


def commit_capture_failure(
    store: CaptureLifecycleStore,
    authority: CaptureWriteAuthority,
    evidence: CaptureFailureEvidence,
    *,
    observed_size: int,
    retention_seconds: float,
) -> CaptureLifecycleRecord:
    if type(evidence) is not CaptureFailureEvidence:
        raise CaptureLifecycleError("failure transition requires typed evidence")
    if not _capture_lifecycle_record.validate_observed_size(observed_size):
        raise CaptureLifecycleError("invalid observed capture size")
    now = store._wall_clock()
    grace = retention_seconds
    if (
        evidence.failure_reason is not None
        and evidence.failure_reason in _capture_types._CAPACITY_FAILURE_REASON_VALUES
    ):
        grace = 0.0
    return store._transition(
        authority,
        allowed_states={
            CaptureState.RESERVED,
            CaptureState.STAGED,
            CaptureState.PUBLISHED_WRITING,
        },
        transform=lambda record: replace(
            record,
            state=CaptureState.FAILED,
            retention_at=now,
            next_attempt_at=now + grace,
            observed_size=observed_size,
            failure=evidence,
            capture_status=CaptureStatus.FAILED,
            snapshot_status=CaptureSnapshotStatus.ABSENT,
            retention_phase=CaptureRetentionPhase.ACTIVE,
            revision=record.revision + 1,
        ),
    )


def prepare_verified_finalization(
    previous: CaptureLifecycleRecord | None,
    verified: VerifiedCaptureSnapshot,
    *,
    issue_reference: bool,
    reference_lifetime_seconds: float,
) -> tuple[CaptureLifecycleRecord, FinalizedCapture]:
    base = verified.manifest
    if (
        previous is None
        or previous.state is not CaptureState.PUBLISHED_WRITING
        or previous.incarnation != base.incarnation
        or previous.revision + 1 != base.finalized_at_revision
        or previous.project_identity != base.project_identity
        or previous.root_identity != base.root_identity
        or previous.public_name != base.carrier_name
        or previous.artifact_identity != base.carrier_identity
    ):
        raise CaptureLifecycleError("verified snapshot does not match write authority")
    token: str | None = None
    reference_hash: str | None = None
    reference_expiry: float | None = None
    if issue_reference:
        reference_expiry = min(
            base.finalized_at + reference_lifetime_seconds,
            base.retention_deadline,
        )
        token, reference_hash = _capture_reference._issue_capture_reference(
            verified,
            expiry=reference_expiry,
        )
    finalized = _capture_reference._bind_finalized_snapshot(
        verified,
        reference_token=token,
        reference_hash=reference_hash,
        reference_expiry=reference_expiry,
    )
    manifest = finalized.snapshot.manifest
    candidate = replace(
        previous,
        state=CaptureState.FINALIZED,
        revision=previous.revision + 1,
        finalized_at_revision=manifest.finalized_at_revision,
        retention_at=manifest.finalized_at,
        next_attempt_at=manifest.retention_deadline,
        manifest=manifest,
        manifest_bytes=_capture_snapshot.encode_capture_final_manifest(manifest),
        capture_status=CaptureStatus.COMPLETE,
        snapshot_status=CaptureSnapshotStatus.VERIFIED,
        reference_status=(
            CaptureReferenceStatus.ISSUED
            if finalized.issuance is not None
            else CaptureReferenceStatus.NOT_REQUESTED
        ),
        delivery_status=CaptureDeliveryStatus.NOT_ATTEMPTED,
        retention_phase=CaptureRetentionPhase.ACTIVE,
    )
    return candidate, finalized
