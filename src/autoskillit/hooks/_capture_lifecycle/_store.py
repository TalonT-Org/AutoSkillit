"""CaptureLifecycleStore and the structural constants it depends on.

Admission helpers delegate to ``_admission`` and durable transaction methods
delegate to ``_transactions`` while preserving their bound signatures and
fault-injection seams. This module retains lock and ledger loading, finalization
recovery, delivery wiring, and sweep orchestration.
"""

from __future__ import annotations

import errno
import fcntl
import importlib
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

# Bootstrap block — mirrors ``_capture/_authority.py`` so that bare-name
# ``_capture`` siblings resolve under ``python -I -S -B`` with only ``hooks/``
# on ``sys.path``.
_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _module_identity
else:
    _module_identity = importlib.import_module("_capture._module_identity")
_module_identity.register_module_aliases(__name__)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _capacity as _capture_capacity
    from autoskillit.hooks._capture import _delivery as _capture_delivery
    from autoskillit.hooks._capture import _ledger as _capture_ledger
    from autoskillit.hooks._capture import _ledger_view as _capture_ledger_view
    from autoskillit.hooks._capture import _lifecycle_policy as _capture_lifecycle_policy
    from autoskillit.hooks._capture import _lifecycle_record as _capture_lifecycle_record
    from autoskillit.hooks._capture import _migration as _capture_migration
    from autoskillit.hooks._capture import _orphan_scan as _capture_orphan_scan
    from autoskillit.hooks._capture import _reader as _capture_reader
    from autoskillit.hooks._capture import _resolver as _capture_resolver
    from autoskillit.hooks._capture import _snapshot as _capture_snapshot
    from autoskillit.hooks._capture import _sweep as _capture_sweep
    from autoskillit.hooks._capture import _sweep_cursor as _capture_sweep_cursor
    from autoskillit.hooks._capture import _syntax as _capture_syntax
    from autoskillit.hooks._capture import _types as _capture_types
    from autoskillit.hooks._capture_lifecycle import _admission, _transactions
else:
    _capture_capacity = importlib.import_module("_capture._capacity")
    _capture_delivery = importlib.import_module("_capture._delivery")
    _capture_ledger = importlib.import_module("_capture._ledger")
    _capture_ledger_view = importlib.import_module("_capture._ledger_view")
    _capture_lifecycle_policy = importlib.import_module("_capture._lifecycle_policy")
    _capture_lifecycle_record = importlib.import_module("_capture._lifecycle_record")
    _capture_migration = importlib.import_module("_capture._migration")
    _capture_orphan_scan = importlib.import_module("_capture._orphan_scan")
    _capture_reader = importlib.import_module("_capture._reader")
    _capture_resolver = importlib.import_module("_capture._resolver")
    _capture_snapshot = importlib.import_module("_capture._snapshot")
    _capture_sweep = importlib.import_module("_capture._sweep")
    _capture_sweep_cursor = importlib.import_module("_capture._sweep_cursor")
    _capture_syntax = importlib.import_module("_capture._syntax")
    _capture_types = importlib.import_module("_capture._types")
    _admission = importlib.import_module("_capture_lifecycle._admission")
    _transactions = importlib.import_module("_capture_lifecycle._transactions")

CaptureCleanupOutcome = _capture_types.CaptureCleanupOutcome
CaptureCapacityReason = _capture_types.CaptureCapacityReason
CleanupBlocker = _capture_types.CleanupBlocker
CleanupProgress = _capture_types.CleanupProgress
DueKey = _capture_types.DueKey
SweepAttempt = _capture_types.SweepAttempt
SweepBudgetSpec = _capture_types.SweepBudgetSpec
LockWaitSpec = _capture_types.LockWaitSpec
_ObservedArtifact = _capture_types.ObservedArtifact
_CarrierLeaseLive = _capture_types.CarrierLeaseLive
CaptureAuthorityError = _capture_snapshot.CaptureAuthorityError
CaptureFailureEvidence = _capture_types.CaptureFailureEvidence
CaptureFinalManifest = _capture_snapshot.CaptureFinalManifest
CaptureWriteAuthority = _capture_snapshot.CaptureWriteAuthority
FinalizedCapture = _capture_snapshot.FinalizedCapture
IssuedCaptureReference = _capture_snapshot.IssuedCaptureReference
LegacyCleanupOnly = _capture_types.LegacyCleanupOnly
PublishedCaptureReference = _capture_snapshot.PublishedCaptureReference
UnavailableCaptureReference = _capture_snapshot.UnavailableCaptureReference
VerifiedCaptureSnapshot = _capture_snapshot.VerifiedCaptureSnapshot

FRAME_MAGIC = _capture_ledger.FRAME_MAGIC
LEDGER_NAME = ".capture-lifecycle.ledger"
LOCK_NAME = ".capture-lifecycle.lock"
MAX_LEDGER_BYTES = _capture_ledger.MAX_LEDGER_BYTES
# Single source of truth: the reclaimability declaration owns the sweep grace.
_RETENTION_SECONDS = float(_capture_lifecycle_policy.SWEEP_GRACE_SECONDS)
_REFERENCE_LIFETIME_SECONDS = 1800.0
if _RETENTION_SECONDS < _REFERENCE_LIFETIME_SECONDS:
    raise AssertionError("capture retention must cover the replay-reference lifetime")
_MAX_RETRY_SECONDS = 3600.0
_COMPACTION_THRESHOLD_BYTES = 31 * 1024 * 1024 // 8
_MAX_COMPACTION_BYTES = 4 * 1024 * 1024
_CAPTURE_ID_RE = _capture_syntax.CAPTURE_ID_RE
_CLOEXEC, _NOFOLLOW, _NONBLOCK = os.O_CLOEXEC, os.O_NOFOLLOW, os.O_NONBLOCK
_CONTROL_FLAGS = os.O_RDWR | os.O_CREAT | _CLOEXEC | _NOFOLLOW
_OBSERVE_FLAGS = os.O_RDWR | _CLOEXEC | _NOFOLLOW | _NONBLOCK
_ARTIFACT_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW
_UNTRUSTED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_STORE_FACTORY_TOKEN = object()


CaptureLifecycleError = _transactions.CaptureLifecycleError
CaptureLedgerError = _transactions.CaptureLedgerError
CaptureCapacityError = _transactions.CaptureCapacityError


CaptureState = _capture_lifecycle_record.CaptureState
CaptureReferenceStatus = _capture_lifecycle_record.CaptureReferenceStatus
CaptureDeliveryStatus = _capture_lifecycle_record.CaptureDeliveryStatus
CaptureRetentionPhase = _capture_lifecycle_record.CaptureRetentionPhase
CaptureSnapshotStatus = _capture_lifecycle_record.CaptureSnapshotStatus
CaptureStatus = _capture_lifecycle_record.CaptureStatus
CaptureLifecycleRecord = _capture_lifecycle_record.CaptureLifecycleRecord
CaptureTransitionCommittedError = _capture_lifecycle_record.CaptureTransitionCommittedError
TERMINAL_STATES = frozenset({CaptureState.DELETED})


_record_to_dict = _transactions._record_to_dict


def _record_from_dict(value: object) -> CaptureLifecycleRecord:
    try:
        return _capture_lifecycle_record.record_from_dict(value)
    except _capture_lifecycle_record.LedgerCodecError as exc:
        raise CaptureLedgerError(str(exc)) from exc


_validate_successor = _transactions._validate_successor


class CaptureLifecycleStore:
    def __init__(
        self,
        root_fd: int,
        *,
        project_identity: tuple[int, int],
        root_identity: tuple[int, int],
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        lock_wait: LockWaitSpec,
        capacity: _capture_types.CaptureCapacitySpec | None = None,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _STORE_FACTORY_TOKEN:
            raise CaptureLifecycleError("CaptureLifecycleStore must be factory-created")
        self._root_fd = root_fd
        self._project_identity = project_identity
        self._root_identity = root_identity
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._ledger_view = _capture_ledger_view.LedgerView()
        self._lock_wait = lock_wait
        self._capacity = capacity if capacity is not None else _capture_types.CaptureCapacitySpec()
        self._sweep_budget: SweepBudgetSpec | None = None
        self._sweep_started_monotonic: float | None = None
        self._sweep_records_inspected = self._sweep_replay_bytes = 0
        self._sweep_transitions = self._sweep_cursor_writes = 0
        self.byte_pressure_observed = False

    @classmethod
    def from_open_authorities(
        cls,
        anchor: object,
        root: object,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        lock_wait: LockWaitSpec,
        capacity: _capture_types.CaptureCapacitySpec | None = None,
    ) -> CaptureLifecycleStore:
        """Open a store with a required lifecycle-lock wait policy."""
        if type(lock_wait) is not LockWaitSpec:
            raise CaptureLifecycleError("invalid lifecycle lock wait specification")
        if capacity is not None and type(capacity) is not _capture_types.CaptureCapacitySpec:
            raise CaptureLifecycleError("invalid capture capacity specification")
        anchor_identity = getattr(anchor, "identity")
        root_identity = getattr(root, "identity")
        store = cls(
            getattr(root, "fd"),
            project_identity=(anchor_identity.device, anchor_identity.inode),
            root_identity=(root_identity.device, root_identity.inode),
            wall_clock=wall_clock,
            monotonic=monotonic,
            lock_wait=lock_wait,
            capacity=capacity,
            _factory_token=_STORE_FACTORY_TOKEN,
        )
        store._normalize_interrupted_deliveries()
        return store

    def _normalize_interrupted_deliveries(self) -> None:
        _capture_delivery.normalize_interrupted_deliveries(
            self,
            lifecycle_error=CaptureLifecycleError,
            lease_live=_CarrierLeaseLive,
            tampered=_capture_types.Tampered,
        )

    def capture_finalization_window(self) -> tuple[float, float]:
        return (now := self._wall_clock()), now + _RETENTION_SECONDS

    def _acquire_flock(self, fd: int) -> None:
        """Thin wrapper — delegates to ``_admission._acquire_flock``.

        The lock-retry loop, composed-deadline check, and ``LockContended``
        raise now live in ``_admission.py`` so the lock-retry primitive is
        physically separate from the transition/capacity accounting it
        shares with the rest of the store. The wrapper preserves the
        public ``self``-binding so monkeypatching this name on the class
        continues to dispatch the same way.
        """
        return _admission._acquire_flock(self, fd)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        _capture_sweep.validate_store_root(self, CaptureLifecycleError)
        try:
            fd = os.open(LOCK_NAME, _CONTROL_FLAGS, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            raise CaptureLifecycleError.from_os_error("cannot open lifecycle lock", exc) from exc
        try:
            _capture_ledger_view.validate_control_file(
                fd, LOCK_NAME, _UNTRUSTED_WRITE_BITS, CaptureLifecycleError
            )
            try:
                self._acquire_flock(fd)
            except OSError as exc:
                raise CaptureLifecycleError.from_os_error(
                    "cannot acquire lifecycle lock", exc
                ) from exc
            _capture_sweep.validate_store_root(self, CaptureLifecycleError)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _open_ledger(self) -> int:
        try:
            fd = os.open(LEDGER_NAME, _CONTROL_FLAGS | os.O_APPEND, 0o600, dir_fd=self._root_fd)
        except OSError as exc:
            raise CaptureLedgerError.from_os_error("cannot open lifecycle ledger", exc) from exc
        try:
            _capture_ledger_view.validate_control_file(
                fd, LEDGER_NAME, _UNTRUSTED_WRITE_BITS, CaptureLifecycleError
            )
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _load_locked(self) -> tuple[dict[str, CaptureLifecycleRecord], int, int]:
        fd = self._open_ledger()
        try:
            try:
                return _capture_migration.load_ledger(
                    self,
                    fd,
                    self._ledger_view,
                    max_ledger_bytes=MAX_LEDGER_BYTES,
                )
            except _capture_ledger_view.LegacyCompacted:
                return self._load_locked()
            except _capture_lifecycle_record.LedgerCodecError as exc:
                error = CaptureLedgerError(str(exc))
                error.reason = exc.reason
                error.observed_version = exc.observed_version
                error.current_version = exc.current_version
                raise error from exc
        finally:
            os.close(fd)

    def _append_locked(
        self,
        record: CaptureLifecycleRecord,
        records: Mapping[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        size: int,
    ) -> None:
        _transactions._append_locked(
            self,
            record,
            records,
            compaction_epoch,
            size,
            compaction_threshold_bytes=_COMPACTION_THRESHOLD_BYTES,
        )

    def _compact_locked(
        self,
        records: Mapping[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        candidate: CaptureLifecycleRecord | None = None,
    ) -> None:
        _transactions._compact_locked(
            self,
            records,
            compaction_epoch,
            candidate,
            max_compaction_bytes=_MAX_COMPACTION_BYTES,
            ledger_name=LEDGER_NAME,
            untrusted_write_bits=_UNTRUSTED_WRITE_BITS,
            cloexec=_CLOEXEC,
            nofollow=_NOFOLLOW,
        )

    @staticmethod
    def _authority_for(record: CaptureLifecycleRecord) -> CaptureWriteAuthority:
        return _capture_snapshot._make_write_authority(
            record.capture_id,
            record.incarnation,
            record.revision,
        )

    def _transition_locked(
        self,
        *,
        records: dict[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        ledger_size: int,
        authority: CaptureWriteAuthority,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        return _transactions._transition_locked(
            self,
            records=records,
            compaction_epoch=compaction_epoch,
            ledger_size=ledger_size,
            authority=authority,
            allowed_states=allowed_states,
            transform=transform,
        )

    def _transition(
        self,
        authority: CaptureWriteAuthority,
        *,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        return _transactions._transition(
            self,
            authority,
            allowed_states=allowed_states,
            transform=transform,
        )

    def _transition_current(
        self,
        capture_id: str,
        incarnation: str,
        *,
        allowed_states: set[CaptureState],
        transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
    ) -> CaptureLifecycleRecord:
        return _transactions._transition_current(
            self,
            capture_id,
            incarnation,
            allowed_states=allowed_states,
            transform=transform,
        )

    def _with_capacity_rescue(
        self,
        attempt: Callable[[], object],
        *,
        rescuable_reasons: frozenset[CaptureCapacityReason] | None = None,
    ) -> object:
        return _transactions._with_capacity_rescue(
            self,
            attempt,
            rescuable_reasons=rescuable_reasons,
        )

    def reserve_capture(self, capture_id: str) -> CaptureLifecycleRecord:
        return _transactions.reserve_capture(
            self,
            capture_id,
            capture_id_re=_CAPTURE_ID_RE,
            retention_seconds=_RETENTION_SECONDS,
        )

    def mark_staged(
        self,
        authority: CaptureWriteAuthority,
        artifact_identity: tuple[int, int],
    ) -> CaptureLifecycleRecord:
        return _transactions.mark_staged(self, authority, artifact_identity)

    def mark_published(
        self,
        authority: CaptureWriteAuthority,
    ) -> CaptureLifecycleRecord:
        return _transactions.mark_published(self, authority)

    def create_artifact(
        self,
        capture_id: str,
    ) -> tuple[int, int, str, tuple[int, int], CaptureWriteAuthority]:
        return _transactions.create_artifact(
            self,
            capture_id,
            artifact_flags=_ARTIFACT_FLAGS,
            untrusted_write_bits=_UNTRUSTED_WRITE_BITS,
        )

    def commit_capture_failure(
        self,
        authority: CaptureWriteAuthority,
        evidence: CaptureFailureEvidence,
        *,
        observed_size: int,
    ) -> CaptureLifecycleRecord:
        return _transactions.commit_capture_failure(
            self,
            authority,
            evidence,
            observed_size=observed_size,
            retention_seconds=_RETENTION_SECONDS,
        )

    def commit_verified_snapshot(
        self,
        verified: VerifiedCaptureSnapshot,
        *,
        issue_reference: bool,
    ) -> FinalizedCapture:
        if type(verified) is not VerifiedCaptureSnapshot or not isinstance(issue_reference, bool):
            raise CaptureLifecycleError("invalid verified finalization request")
        base = verified.manifest
        # Mutable state shared between attempt closure and post-attempt recovery.
        candidate_holder: list[CaptureLifecycleRecord | None] = [None]
        finalized_holder: list[FinalizedCapture | None] = [None]

        def _attempt() -> FinalizedCapture:
            """One complete lock-load-transition cycle; safe to call twice."""
            candidate_holder[0] = None
            finalized_holder[0] = None
            with self._locked():
                records, compaction_epoch, size = self._load_locked()
                previous = records.get(base.capture_id)
                candidate, finalized = _transactions.prepare_verified_finalization(
                    previous,
                    verified,
                    issue_reference=issue_reference,
                    reference_lifetime_seconds=_REFERENCE_LIFETIME_SECONDS,
                )
                candidate_holder[0] = candidate
                finalized_holder[0] = finalized
                self._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=self._authority_for(cast(CaptureLifecycleRecord, previous)),
                    allowed_states={CaptureState.PUBLISHED_WRITING},
                    transform=lambda _current: candidate,
                )
            return finalized

        try:
            result = self._with_capacity_rescue(_attempt)
        except CaptureTransitionCommittedError as commit_error:
            candidate = candidate_holder[0]
            finalized = finalized_holder[0]
            if candidate is None or finalized is None:
                raise
            try:
                current = self.get_record(base.capture_id)
            except (RuntimeError, OSError) as recovery_error:
                commit_error.add_note(
                    "FINAL reconciliation failed: "
                    f"{type(recovery_error).__name__}: {recovery_error}"
                )
            else:
                if (
                    current is not None
                    and replace(
                        current,
                        compaction_epoch=candidate.compaction_epoch,
                    )
                    == candidate
                ):
                    return finalized
            raise
        if not isinstance(result, FinalizedCapture):
            raise CaptureLifecycleError("verified finalization produced no authority")
        return result

    def publish_reference(self, finalized: FinalizedCapture) -> PublishedCaptureReference:
        return _capture_delivery.publish_reference(
            self,
            finalized,
            lifecycle_error=CaptureLifecycleError,
        )

    def mark_reference_unavailable(
        self,
        finalized: FinalizedCapture,
        *,
        reason_code: str,
    ) -> UnavailableCaptureReference:
        return _capture_delivery.mark_reference_unavailable(
            self,
            finalized,
            reason_code=reason_code,
            lifecycle_error=CaptureLifecycleError,
        )

    def revoke_reference(self, finalized: FinalizedCapture) -> CaptureLifecycleRecord:
        return _capture_delivery.revoke_reference(
            self,
            finalized,
            lifecycle_error=CaptureLifecycleError,
        )

    def transition_delivery(
        self,
        value: _capture_delivery.DeliveryValue,
        *,
        expected: CaptureDeliveryStatus,
        target: CaptureDeliveryStatus,
    ) -> CaptureLifecycleRecord:
        return _capture_delivery.transition_delivery(
            self,
            value,
            expected=expected,
            target=target,
            lifecycle_error=CaptureLifecycleError,
        )

    def mark_delivery_unknown(
        self, value: _capture_delivery.DeliveryValue
    ) -> CaptureLifecycleRecord:
        return _capture_delivery.mark_delivery_unknown(
            self,
            value,
            lifecycle_error=CaptureLifecycleError,
        )

    def recover_interrupted_delivery(self, capture_id: str) -> CaptureLifecycleRecord:
        return _capture_delivery.recover_interrupted_delivery(
            self, capture_id, lifecycle_error=CaptureLifecycleError
        )

    def get_record(self, capture_id: str) -> CaptureLifecycleRecord | None:
        with self._locked():
            records, _compaction_epoch, _size = self._load_locked()
            return records.get(capture_id)

    def open_verified_capture(self, token: str) -> _capture_reader.VerifiedCaptureReader:
        return _capture_resolver.open_verified_capture(
            self,
            token,
            lifecycle_error=CaptureLifecycleError,
        )

    def _adopt_verified_capture(
        self, finalized: FinalizedCapture, fd: int
    ) -> _capture_reader.VerifiedCaptureReader:
        return _capture_resolver.adopt_verified_capture(
            self,
            finalized,
            fd,
            lifecycle_error=CaptureLifecycleError,
        )

    @staticmethod
    def acquire_writer_lease(artifact_fd: int) -> int:
        return _capture_resolver.acquire_writer_lease(
            artifact_fd,
            lifecycle_error=CaptureLifecycleError,
        )

    def _observe(
        self,
        name: str,
        expected: tuple[int, int] | None,
        *,
        valid_name: re.Pattern[str],
    ) -> _ObservedArtifact | None:
        return _capture_sweep.observe_artifact(
            root_fd=self._root_fd,
            name=name,
            expected=expected,
            valid_name=valid_name,
            open_flags=_OBSERVE_FLAGS,
            untrusted_write_bits=_UNTRUSTED_WRITE_BITS,
            lifecycle_error=CaptureLifecycleError,
        )

    @staticmethod
    def _try_artifact_lease(observed: _ObservedArtifact) -> None:
        try:
            fcntl.flock(observed.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise _CarrierLeaseLive from exc
            raise CaptureLifecycleError("artifact lease capability failure") from exc

    def _normalize_abandoned(
        self,
        record: CaptureLifecycleRecord,
        *,
        preleased: _ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> tuple[CaptureLifecycleRecord, _ObservedArtifact | None]:
        return _capture_sweep.normalize_abandoned(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            staging_name_pattern=_capture_syntax.STAGING_NAME_RE,
            public_name_pattern=_capture_syntax.PUBLIC_NAME_RE,
            wall_clock=self._wall_clock,
            preleased=preleased,
            lease_checked=lease_checked,
        )

    def _acquire_cleanup_lease(
        self,
        record: CaptureLifecycleRecord,
    ) -> _ObservedArtifact | None:
        return _capture_sweep.acquire_cleanup_lease(
            record,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            staging_name_pattern=_capture_syntax.STAGING_NAME_RE,
            public_name_pattern=_capture_syntax.PUBLIC_NAME_RE,
            quarantine_name_pattern=_capture_syntax.QUARANTINE_NAME_RE,
        )

    def _deleting_record(
        self,
        record: CaptureLifecycleRecord,
    ) -> CaptureLifecycleRecord:
        return _capture_sweep.deleting_record(
            record,
            nonce=secrets.token_hex(8),
        )

    def _quarantine_delete(
        self,
        record: CaptureLifecycleRecord,
        authorize_delete: Callable[[], None] | None = None,
        *,
        preleased: _ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> int:
        return _capture_sweep.quarantine_delete(
            record,
            root_fd=self._root_fd,
            observe=self._observe,
            try_lease=self._try_artifact_lease,
            authorize_delete=authorize_delete,
            public_name_pattern=_capture_syntax.PUBLIC_NAME_RE,
            quarantine_name_pattern=_capture_syntax.QUARANTINE_NAME_RE,
            preleased=preleased,
            lease_checked=lease_checked,
        )

    def _sweep_one(self, capture_id: str) -> tuple[SweepAttempt, int, int]:
        return _capture_sweep.sweep_one(
            self,
            capture_id,
            lifecycle_error=CaptureLifecycleError,
            max_retry_seconds=_MAX_RETRY_SECONDS,
        )

    def _due_keys(
        self,
        now: float,
        max_records: int,
    ) -> tuple[list[DueKey], bool, bool]:
        return _capture_sweep_cursor.select_due_keys(
            self,
            now,
            max_records,
            TERMINAL_STATES,
        )

    def _advance_sweep_cursor(self, due_key: DueKey) -> None:
        assert self._sweep_budget is not None
        _capture_sweep_cursor.advance_cursor(self, due_key, self._sweep_budget)

    def _admission_reason(
        self,
        records: Mapping[str, CaptureLifecycleRecord],
        candidate: CaptureLifecycleRecord,
        compaction_epoch: int,
        now: float,
    ) -> _capture_capacity.AdmissionDecision:
        """Thin wrapper — delegates to ``_admission._admission_reason``."""
        return _admission._admission_reason(self, records, candidate, compaction_epoch, now)

    def _admit_new_record(
        self,
        record: CaptureLifecycleRecord,
        records: dict[str, CaptureLifecycleRecord],
        compaction_epoch: int,
        size: int,
        now: float,
    ) -> bool:
        """Thin wrapper — delegates to ``_admission._admit_new_record``.

        Preserves the ``#4440`` one-record-cannot-starve invariant: the
        active-record cap, the ``_append_locked`` call, and the
        ``_sweep_transitions`` budget increment all move with the body to
        ``_admission._admit_new_record`` so the wrapper is a 1-line
        delegation. ``tests/cli/test_capture_store.py:247`` relies on this
        exact signature via ``real_admit = CaptureLifecycleStore._admit_new_record``
        and ``monkeypatch.setattr``.
        """
        return _admission._admit_new_record(self, record, records, compaction_epoch, size, now)

    def _scan_and_adopt_orphans(self) -> _capture_orphan_scan.OrphanAdoptionOutcome:
        return _admission._scan_and_adopt_orphans(self, lifecycle_error=CaptureLifecycleError)

    def sweep(
        self,
        budget: SweepBudgetSpec,
    ) -> CaptureCleanupOutcome:
        if type(budget) is not SweepBudgetSpec:
            raise CaptureLifecycleError("cleanup requires one SweepBudgetSpec")
        self._sweep_budget = budget
        self._sweep_started_monotonic = self._monotonic()
        self._sweep_records_inspected = self._sweep_replay_bytes = 0
        self._sweep_transitions = self._sweep_cursor_writes = 0
        try:
            return _capture_sweep.run_bounded_sweep(
                budget=budget,
                monotonic=self._monotonic,
                wall_clock=self._wall_clock,
                due_keys=self._due_keys,
                before_attempt=self._advance_sweep_cursor,
                sweep_one=self._sweep_one,
                work_counters=lambda: _capture_orphan_scan.sweep_work_counters(self),
                scan_and_adopt_orphans=self._scan_and_adopt_orphans,
            )
        finally:
            self._sweep_budget = None
            self._sweep_started_monotonic = None
