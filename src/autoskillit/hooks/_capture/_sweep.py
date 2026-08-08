"""Bounded lifecycle sweep orchestration shared by lifecycle module identities."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable, Collection, Iterable
from dataclasses import replace
from typing import NamedTuple, Protocol

from . import _lifecycle_policy, _orphan_scan, _store_port, _sweep_cursor
from ._cleanup import close_preserving_primary
from ._ledger import (
    CaptureLifecycleRecord,
    CaptureReferenceStatus,
    CaptureRetentionPhase,
    CaptureState,
    LedgerCodecError,
    adopted_orphan_record,
    same_record,
)
from ._module_identity import register_module_aliases
from ._types import (
    CaptureCleanupOutcome,
    CarrierLeaseLive,
    CleanupBlocker,
    CleanupProgress,
    DueKey,
    LockContended,
    ObservedArtifact,
    SweepAttempt,
    SweepBudgetExceeded,
    SweepBudgetSpec,
    Tampered,
)

register_module_aliases(__name__)

# A sweep attempt can expire a reference, mark the record abandoned, enter
# deletion, and finally mark it deleted. Orphan admissions reserve the same
# per-item ceiling because they share the sweep transition budget.
_MAX_TRANSITIONS_PER_SWEEP_ITEM = 4


class OrphanAdoptionOutcome(NamedTuple):
    """Directory scan state that must participate in sweep convergence."""

    examined: int
    adopted: int
    directory_complete: bool
    pending_candidates: int


class SweepRecord(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def next_attempt_at(self) -> float: ...


class NamePattern(Protocol):
    def fullmatch(self, value: str) -> object | None: ...


def bounded_due_keys(
    records: Iterable[SweepRecord],
    now: float,
    terminal_states: Collection[object],
    max_records: int,
) -> tuple[list[DueKey], bool, int, DueKey | None]:
    due: list[DueKey] = []
    inspected = 0
    complete = True
    rebuild_key: DueKey | None = None
    for record in records:
        if inspected >= max_records:
            complete = False
            break
        inspected += 1
        if record.state in terminal_states:
            continue
        key = DueKey(record.next_attempt_at, record.capture_id)
        rebuild_key = key if rebuild_key is None else max(rebuild_key, key)
        if record.next_attempt_at <= now:
            due.append(key)
    due.sort()
    return due, complete, inspected, rebuild_key


def select_due_keys(
    store: _store_port.SweepStorePort,
    now: float,
    max_records: int,
    terminal_states: Collection[object],
) -> tuple[list[DueKey], bool, bool]:
    with store._locked(blocking=False):
        records, compaction_epoch, _size = store._load_locked()
        due, complete, inspected, rebuild_key = bounded_due_keys(
            records.values(),
            now,
            terminal_states,
            max_records,
        )
        cursor = _sweep_cursor.load_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=compaction_epoch,
        )
        repair_needed = cursor.status is not _sweep_cursor.CursorStatus.VALID
        repaired = False
        if repair_needed and complete and not due:
            budget = store._sweep_budget
            if budget is None:
                raise RuntimeError("cursor repair requires an active sweep budget")
            if store._sweep_cursor_writes >= budget.max_cursor_writes:
                raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
            if rebuild_key is None:
                repaired = _sweep_cursor.clear_cursor(store._root_fd)
            else:
                _sweep_cursor.write_cursor(
                    store._root_fd,
                    project_identity=store._project_identity,
                    root_identity=store._root_identity,
                    compaction_epoch=compaction_epoch,
                    due_key=rebuild_key,
                )
                repaired = True
            if repaired:
                store._sweep_cursor_writes += 1
    store._sweep_records_inspected += inspected
    return (
        _sweep_cursor.rotate_after(due, cursor.due_key),
        complete,
        repaired or (repair_needed and bool(due)),
    )


def advance_cursor(
    store: _store_port.SweepStorePort,
    due_key: DueKey,
    budget: SweepBudgetSpec,
) -> None:
    if store._sweep_cursor_writes >= budget.max_cursor_writes:
        raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
    with store._locked(blocking=False):
        _records, compaction_epoch, _size = store._load_locked()
        _sweep_cursor.write_cursor(
            store._root_fd,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            compaction_epoch=compaction_epoch,
            due_key=due_key,
        )
    store._sweep_cursor_writes += 1


def sweep_work_counters(
    store: _store_port.SweepStorePort,
) -> tuple[int, int, int, int]:
    return (
        store._sweep_records_inspected,
        store._sweep_replay_bytes,
        store._sweep_transitions,
        store._sweep_cursor_writes,
    )


def account_replay_bytes(store: _store_port.SweepStorePort, amount: int) -> None:
    budget = store._sweep_budget
    if budget is not None and store._sweep_replay_bytes + amount > budget.max_replay_bytes:
        raise SweepBudgetExceeded(CleanupBlocker.REPLAY_BYTE_BUDGET)
    if budget is not None:
        store._sweep_replay_bytes += amount


def write_cursor_accounted(
    store: _store_port.SweepStorePort,
    *,
    compaction_epoch: int,
    due_key: DueKey,
) -> None:
    budget = store._sweep_budget
    if budget is not None and store._sweep_cursor_writes >= budget.max_cursor_writes:
        raise SweepBudgetExceeded(CleanupBlocker.CURSOR_WRITE_BUDGET)
    _sweep_cursor.write_cursor(
        store._root_fd,
        project_identity=store._project_identity,
        root_identity=store._root_identity,
        compaction_epoch=compaction_epoch,
        due_key=due_key,
    )
    if budget is not None:
        store._sweep_cursor_writes += 1


def validate_store_root(
    store: _store_port.SweepStorePort,
    lifecycle_error: type[RuntimeError],
) -> None:
    value = os.fstat(store._root_fd)
    if (value.st_dev, value.st_ino) != store._root_identity:
        raise lifecycle_error("capture root identity changed")


def create_verified_recovery_link(
    *,
    link: Callable[[], None],
    observe: Callable[[], ObservedArtifact | None],
    rollback: Callable[[], None],
    sync: Callable[[], None],
) -> ObservedArtifact:
    link()
    try:
        linked = observe()
        if linked is None or linked.nlink != 2:
            if linked is not None:
                os.close(linked.fd)
            raise Tampered
        return linked
    except BaseException:
        try:
            rollback()
            sync()
        except FileNotFoundError:
            pass
        raise


def observe_artifact(
    *,
    root_fd: int,
    name: str,
    expected: tuple[int, int] | None,
    valid_name: NamePattern,
    open_flags: int,
    untrusted_write_bits: int,
    lifecycle_error: type[RuntimeError],
) -> ObservedArtifact | None:
    if valid_name.fullmatch(name) is None:
        raise Tampered
    try:
        observed = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise lifecycle_error("cannot inspect managed capture") from exc
    if (
        not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or observed.st_mode & untrusted_write_bits
        or observed.st_nlink not in (1, 2)
    ):
        raise Tampered
    try:
        fd = os.open(name, open_flags, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Tampered from exc
        raise lifecycle_error("cannot open managed capture") from exc
    try:
        value = os.fstat(fd)
        identity = (value.st_dev, value.st_ino)
        if (
            identity != (observed.st_dev, observed.st_ino)
            or (expected is not None and identity != expected)
            or not stat.S_ISREG(value.st_mode)
            or value.st_uid != os.geteuid()
            or value.st_mode & untrusted_write_bits
            or value.st_nlink not in (1, 2)
        ):
            raise Tampered
        return ObservedArtifact(
            fd=fd,
            identity=identity,
            nlink=value.st_nlink,
            size=value.st_size,
        )
    except BaseException as primary_error:
        close_preserving_primary(
            fd,
            primary_error,
            context="managed capture observation cleanup",
        )
        raise


def acquire_cleanup_lease(
    record: CaptureLifecycleRecord,
    *,
    observe: Callable[..., ObservedArtifact | None],
    try_lease: Callable[[ObservedArtifact], None],
    staging_name_pattern: object,
    public_name_pattern: object,
    quarantine_name_pattern: object,
) -> ObservedArtifact | None:
    names = [
        (record.public_name, public_name_pattern),
    ]
    if record.state in {
        CaptureState.RESERVED,
        CaptureState.STAGED,
        CaptureState.PUBLISHED_WRITING,
    }:
        names.insert(0, (record.staging_name, staging_name_pattern))
    if record.state is CaptureState.DELETING and record.quarantine_name:
        names.append((record.quarantine_name, quarantine_name_pattern))

    observed: list[ObservedArtifact] = []
    retained: ObservedArtifact | None = None
    try:
        for name, pattern in names:
            value = observe(
                name,
                record.artifact_identity,
                valid_name=pattern,
            )
            if value is not None:
                observed.append(value)
        if not observed:
            return None
        if record.artifact_identity is None:
            raise Tampered
        if any(value.identity != observed[0].identity for value in observed[1:]):
            raise Tampered
        candidate = observed[0]
        try_lease(candidate)
        retained = candidate
        return retained
    finally:
        for value in observed:
            if value is not retained:
                os.close(value.fd)


def normalize_abandoned(
    record: CaptureLifecycleRecord,
    *,
    root_fd: int,
    observe: Callable[..., ObservedArtifact | None],
    try_lease: Callable[[ObservedArtifact], None],
    staging_name_pattern: object,
    public_name_pattern: object,
    wall_clock: Callable[[], float],
    preleased: ObservedArtifact | None = None,
    lease_checked: bool = False,
) -> tuple[CaptureLifecycleRecord, ObservedArtifact | None]:
    staging: ObservedArtifact | None = None
    public: ObservedArtifact | None = None
    lease_target: ObservedArtifact | None = None
    lease_transferred = False
    try:
        staging = observe(
            record.staging_name,
            record.artifact_identity,
            valid_name=staging_name_pattern,
        )
        public = observe(
            record.public_name,
            record.artifact_identity,
            valid_name=public_name_pattern,
        )
        if record.artifact_identity is None and (staging is not None or public is not None):
            raise Tampered
        lease_target = public or staging
        if lease_target is None:
            if preleased is not None:
                raise Tampered
            return (
                replace(
                    record,
                    state=CaptureState.DELETED,
                    retention_phase=CaptureRetentionPhase.DELETED,
                    revision=record.revision + 1,
                ),
                None,
            )
        if preleased is None:
            if lease_checked:
                raise CarrierLeaseLive
            try_lease(lease_target)
            returned_lease = lease_target
        else:
            if preleased.identity != lease_target.identity:
                raise Tampered
            returned_lease = preleased
        identity = lease_target.identity
        if staging is not None and public is not None:
            if staging.identity != public.identity or staging.nlink != 2 or public.nlink != 2:
                raise Tampered
            os.unlink(record.staging_name, dir_fd=root_fd)
            os.fsync(root_fd)
        elif staging is not None:
            try:
                linked = create_verified_recovery_link(
                    link=lambda: os.link(
                        record.staging_name,
                        record.public_name,
                        src_dir_fd=root_fd,
                        dst_dir_fd=root_fd,
                        follow_symlinks=False,
                    ),
                    observe=lambda: observe(
                        record.public_name,
                        identity,
                        valid_name=public_name_pattern,
                    ),
                    rollback=lambda: os.unlink(
                        record.public_name,
                        dir_fd=root_fd,
                    ),
                    sync=lambda: os.fsync(root_fd),
                )
            except FileExistsError as exc:
                raise Tampered from exc
            os.close(linked.fd)
            os.unlink(record.staging_name, dir_fd=root_fd)
            os.fsync(root_fd)
        result = (
            replace(
                record,
                state=CaptureState.ABANDONED,
                artifact_identity=identity,
                retention_at=record.created_at,
                next_attempt_at=wall_clock(),
                observed_size=lease_target.size,
                retention_phase=CaptureRetentionPhase.ELIGIBLE,
                revision=record.revision + 1,
            ),
            returned_lease,
        )
        lease_transferred = preleased is None
        return result
    finally:
        for observed in (staging, public):
            if observed is not None and (observed is not lease_target or not lease_transferred):
                os.close(observed.fd)


def deleting_record(
    record: CaptureLifecycleRecord,
    *,
    nonce: str,
) -> CaptureLifecycleRecord:
    if record.state is CaptureState.DELETING:
        return record
    return replace(
        record,
        state=CaptureState.DELETING,
        deletion_nonce=nonce,
        quarantine_name=f".capture-quarantine-{record.capture_id}-{nonce}",
        retention_phase=CaptureRetentionPhase.DELETING,
        revision=record.revision + 1,
    )


def quarantine_delete(
    record: CaptureLifecycleRecord,
    *,
    root_fd: int,
    observe: Callable[..., ObservedArtifact | None],
    try_lease: Callable[[ObservedArtifact], None],
    authorize_delete: Callable[[], None] | None = None,
    public_name_pattern: object,
    quarantine_name_pattern: object,
    preleased: ObservedArtifact | None = None,
    lease_checked: bool = False,
) -> int:
    expected = record.artifact_identity
    if expected is None:
        raise Tampered

    def unlink_quarantine() -> None:
        os.unlink(record.quarantine_name, dir_fd=root_fd)

    public: ObservedArtifact | None = None
    quarantine: ObservedArtifact | None = None
    try:
        public = observe(
            record.public_name,
            expected,
            valid_name=public_name_pattern,
        )
        quarantine = observe(
            record.quarantine_name,
            expected,
            valid_name=quarantine_name_pattern,
        )
        if public is None and quarantine is None:
            if preleased is not None:
                raise Tampered
            if authorize_delete is not None:
                authorize_delete()
            return record.size
        lease_target = public or quarantine
        if preleased is None and lease_target is not None:
            if lease_checked:
                raise Tampered
            try_lease(lease_target)
        elif (
            preleased is not None
            and lease_target is not None
            and preleased.identity != lease_target.identity
        ):
            raise Tampered
        if public is not None and quarantine is not None:
            if (
                public.identity != quarantine.identity
                or public.nlink != 2
                or quarantine.nlink != 2
            ):
                raise Tampered
        if authorize_delete is not None:
            authorize_delete()
        if public is not None and quarantine is None:
            linked = create_verified_recovery_link(
                link=lambda: os.link(
                    record.public_name,
                    record.quarantine_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                ),
                observe=lambda: observe(
                    record.quarantine_name,
                    expected,
                    valid_name=quarantine_name_pattern,
                ),
                rollback=unlink_quarantine,
                sync=lambda: os.fsync(root_fd),
            )
            os.close(linked.fd)
        if public is not None:
            os.unlink(record.public_name, dir_fd=root_fd)
            os.fsync(root_fd)
        verified = observe(
            record.quarantine_name,
            expected,
            valid_name=quarantine_name_pattern,
        )
        if verified is None or verified.nlink != 1:
            if verified is not None:
                os.close(verified.fd)
            raise Tampered
        os.close(verified.fd)
        unlink_quarantine()
        os.fsync(root_fd)
        return record.size
    finally:
        if public is not None:
            os.close(public.fd)
        if quarantine is not None:
            os.close(quarantine.fd)


def retry_record(
    record: CaptureLifecycleRecord,
    *,
    now: float,
    max_retry_seconds: float,
) -> CaptureLifecycleRecord:
    retry_count = record.retry_count + 1
    delay = min(float(2 ** min(retry_count, 12)), max_retry_seconds)
    return replace(
        record,
        retry_count=retry_count,
        next_attempt_at=now + delay,
        revision=record.revision + 1,
    )


def _transition_if_current(
    store: _store_port.TransitionStorePort,
    expected: CaptureLifecycleRecord,
    transform: Callable[[CaptureLifecycleRecord], CaptureLifecycleRecord],
) -> CaptureLifecycleRecord | None:
    with store._locked(blocking=False):
        records, compaction_epoch, size = store._load_locked()
        current = records.get(expected.capture_id)
        if not same_record(expected, current):
            return None
        return store._transition_locked(
            records=records,
            compaction_epoch=compaction_epoch,
            ledger_size=size,
            authority=store._authority_for(expected),
            allowed_states={expected.state},
            transform=transform,
        )


def sweep_one(
    store: _store_port.SweepStorePort,
    capture_id: str,
    *,
    lifecycle_error: type[RuntimeError],
    max_retry_seconds: float,
) -> tuple[SweepAttempt, int, int]:
    expected: CaptureLifecycleRecord | None = None
    lease: ObservedArtifact | None = None
    try:
        with store._locked(blocking=False):
            records, compaction_epoch, size = store._load_locked()
            record = records.get(capture_id)
            now = store._wall_clock()
            if (
                record is None
                or record.state is CaptureState.DELETED
                or record.next_attempt_at > now
            ):
                return (SweepAttempt.NOT_DUE, 0, 0)
            expected = record
            if record.reference_status in {
                CaptureReferenceStatus.ISSUED,
                CaptureReferenceStatus.PUBLISHED,
            }:
                if (
                    record.manifest is None
                    or record.manifest.reference_expiry is None
                    or now < record.manifest.reference_expiry
                ):
                    return (SweepAttempt.NOT_DUE, 0, 0)
                expired = replace(
                    record,
                    reference_status=CaptureReferenceStatus.EXPIRED,
                    revision=record.revision + 1,
                )
                expected = store._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=store._authority_for(record),
                    allowed_states={record.state},
                    transform=lambda _current: expired,
                )
        if (
            expected.project_identity != store._project_identity
            or expected.root_identity != store._root_identity
        ):
            raise Tampered
        lease = store._acquire_cleanup_lease(expected)
        with store._locked(blocking=False):
            records, compaction_epoch, size = store._load_locked()
            record = records.get(capture_id)
            now = store._wall_clock()
            if record is None or not same_record(expected, record) or record.next_attempt_at > now:
                return (SweepAttempt.NOT_DUE, 0, 0)
            if record.state in {
                CaptureState.RESERVED,
                CaptureState.STAGED,
                CaptureState.PUBLISHED_WRITING,
            }:
                normalized, normalized_lease = store._normalize_abandoned(
                    record,
                    preleased=lease,
                    lease_checked=True,
                )
                if normalized_lease is not lease:
                    raise lifecycle_error("abandoned normalization changed lease ownership")
                record = store._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=store._authority_for(record),
                    allowed_states={record.state},
                    transform=lambda _current: normalized,
                )
                expected = record
                if record.state is CaptureState.DELETED:
                    return (SweepAttempt.DELETED, 0, 0)
                records, compaction_epoch, size = store._load_locked()
                record = records[capture_id]
            deleting = store._deleting_record(record)
            if deleting is not record:
                record = store._transition_locked(
                    records=records,
                    compaction_epoch=compaction_epoch,
                    ledger_size=size,
                    authority=store._authority_for(record),
                    allowed_states={record.state},
                    transform=lambda _current: deleting,
                )
                expected = record
            deleting = record
        deleted_bytes = store._quarantine_delete(
            deleting,
            preleased=lease,
            lease_checked=True,
        )
        with store._locked(blocking=False):
            records, compaction_epoch, size = store._load_locked()
            current = records.get(capture_id)
            if not same_record(deleting, current):
                raise lifecycle_error("cleanup authority changed during deletion")
            deleted = replace(
                deleting,
                state=CaptureState.DELETED,
                next_attempt_at=now,
                retention_phase=CaptureRetentionPhase.DELETED,
                revision=deleting.revision + 1,
            )
            store._transition_locked(
                records=records,
                compaction_epoch=compaction_epoch,
                ledger_size=size,
                authority=store._authority_for(deleting),
                allowed_states={CaptureState.DELETING},
                transform=lambda _current: deleted,
            )
        return (SweepAttempt.DELETED, deleted_bytes, 0)
    except CarrierLeaseLive:
        return (SweepAttempt.CARRIER_LEASE_LIVE, 0, 0)
    except Tampered:
        if expected is not None:
            # Set next_attempt_at to hold-expiry time so the record
            # becomes sweep-eligible after the forensic window.
            _tampered_hold = _lifecycle_policy.STATE_RECLAIMABILITY[
                CaptureState.TAMPERED
            ].duration_seconds
            if _tampered_hold is None:
                raise lifecycle_error("tampered state requires a forensic hold duration")
            _tampered_expiry = store._wall_clock() + _tampered_hold
            _transition_if_current(
                store,
                expected,
                lambda record: replace(
                    record,
                    state=CaptureState.TAMPERED,
                    retention_phase=CaptureRetentionPhase.TAMPERED,
                    next_attempt_at=_tampered_expiry,
                    revision=record.revision + 1,
                ),
            )
        return (SweepAttempt.TAMPERED, 0, 0)
    except (lifecycle_error, OSError):
        retries = 0
        if expected is not None:
            retry = _transition_if_current(
                store,
                expected,
                lambda record: retry_record(
                    record,
                    now=store._wall_clock(),
                    max_retry_seconds=max_retry_seconds,
                ),
            )
            retries = int(retry is not None)
        return (SweepAttempt.ERROR, 0, retries)
    finally:
        if lease is not None:
            os.close(lease.fd)


def adopt_orphan(
    store: _store_port.SweepStorePort,
    public_name: str,
    *,
    lifecycle_error: type[RuntimeError],
) -> bool:
    """Adopt one directory-reconciliation orphan candidate under lock.

    Re-verifies every gate the unlocked scan already checked — tracked-name
    exclusion, regular-file-with-no-symlink-traversal (:issue:`4319`) — so a
    race between the scan and this locked adoption can never admit a
    duplicate record for a name a real reservation or an earlier adoption
    claimed in between. Capacity-exhausted candidates are silently skipped
    (see ``CaptureLifecycleStore._admit_new_record``), not errored.
    """
    with store._locked(blocking=False):
        records, compaction_epoch, size = store._load_locked()
        tracked = {
            record.public_name
            for record in records.values()
            if record.retention_phase is not CaptureRetentionPhase.DELETED
        }
        if public_name in tracked:
            return False
        try:
            value = os.stat(public_name, dir_fd=store._root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise lifecycle_error("cannot inspect orphan-adoption candidate") from exc
        if not stat.S_ISREG(value.st_mode):
            return False
        if store._wall_clock() - value.st_mtime < _orphan_scan.ADOPTION_AGE_SECONDS:
            return False
        try:
            candidate = adopted_orphan_record(
                public_name=public_name,
                project_identity=store._project_identity,
                root_identity=store._root_identity,
                artifact_identity=(value.st_dev, value.st_ino),
                observed_size=value.st_size,
                now=store._wall_clock(),
            )
        except LedgerCodecError:
            return False
        return store._admit_new_record(candidate, records, compaction_epoch, size)


def scan_and_adopt_orphans(
    store: _store_port.SweepStorePort,
    *,
    lifecycle_error: type[RuntimeError],
) -> OrphanAdoptionOutcome:
    """Scan for unledgered capture files and adopt eligible orphans.

    Returns scan/adoption state used by the outer sweep's convergence test. A
    zero-cost no-op — no lock taken, no
    directory listed — when the active sweep budget disables the phase
    (``max_directory_entries_scanned == 0``, the ``RUNNER_TAIL_BUDGET``
    default), so per-command runner-tail latency is unaffected.
    """
    budget = store._sweep_budget
    if budget is None or budget.max_directory_entries_scanned <= 0:
        return OrphanAdoptionOutcome(0, 0, True, 0)
    now = store._wall_clock()
    with store._locked(blocking=False):
        records, _compaction_epoch, _size = store._load_locked()
        tracked = frozenset(
            record.public_name
            for record in records.values()
            if record.retention_phase is not CaptureRetentionPhase.DELETED
        )
    scan = _orphan_scan.scan_for_orphans(store._root_fd, tracked, budget, now=now)
    adopted = 0
    for name in scan.candidates:
        _inspected, _replay, transitions, _cursor_writes = sweep_work_counters(store)
        if transitions + _MAX_TRANSITIONS_PER_SWEEP_ITEM > budget.max_transitions:
            break
        if adopt_orphan(store, name, lifecycle_error=lifecycle_error):
            adopted += 1
    return OrphanAdoptionOutcome(
        examined=scan.examined,
        adopted=adopted,
        directory_complete=scan.directory_complete,
        pending_candidates=len(scan.candidates) - adopted,
    )


def run_bounded_sweep(
    *,
    budget: SweepBudgetSpec,
    monotonic: Callable[[], float],
    wall_clock: Callable[[], float],
    due_keys: Callable[[float, int], tuple[list[DueKey], bool, bool]],
    before_attempt: Callable[[DueKey], None],
    sweep_one: Callable[[str], tuple[SweepAttempt, int, int]],
    work_counters: Callable[[], tuple[int, int, int, int]],
    scan_and_adopt_orphans: Callable[[], OrphanAdoptionOutcome],
) -> CaptureCleanupOutcome:
    started = monotonic()
    examined = deleted = deleted_bytes = carrier_lease_live = 0
    not_due = tampered = errors = retry_count = 0
    blocker = CleanupBlocker.NONE
    orphan_outcome = OrphanAdoptionOutcome(0, 0, True, 0)
    try:
        pending, discovery_complete, cursor_repair = due_keys(
            wall_clock(),
            budget.max_records_inspected,
        )
    except LockContended:
        return CaptureCleanupOutcome(
            remaining_due=1,
            blocker=CleanupBlocker.LOCK_CONTENDED,
            duration=max(0.0, monotonic() - started),
        )
    except SweepBudgetExceeded as exc:
        records_inspected, replay_bytes, transitions, cursor_writes = work_counters()
        return CaptureCleanupOutcome(
            remaining_due=1,
            records_inspected=records_inspected,
            replay_bytes=replay_bytes,
            transitions=transitions,
            cursor_writes=cursor_writes,
            blocker=exc.blocker,
            duration=max(0.0, monotonic() - started),
        )
    if not discovery_complete:
        blocker = CleanupBlocker.RECORD_BUDGET
    lock_contended = False
    for due_key in pending:
        if examined >= budget.max_attempts:
            if blocker is CleanupBlocker.NONE:
                blocker = CleanupBlocker.ATTEMPT_BUDGET
            break
        _records, _bytes, transitions, _cursor_writes = work_counters()
        if transitions + _MAX_TRANSITIONS_PER_SWEEP_ITEM > budget.max_transitions:
            if blocker is CleanupBlocker.NONE:
                blocker = CleanupBlocker.TRANSITION_BUDGET
            break
        if examined > 0 and monotonic() - started >= budget.max_duration_seconds:
            if blocker is CleanupBlocker.NONE:
                blocker = CleanupBlocker.ELAPSED_DEADLINE
            break
        try:
            before_attempt(due_key)
            result, logical_bytes, retries = sweep_one(due_key.capture_id)
        except LockContended:
            lock_contended = True
            blocker = CleanupBlocker.LOCK_CONTENDED
            break
        except SweepBudgetExceeded as exc:
            blocker = exc.blocker
            break
        examined += 1
        deleted_bytes += logical_bytes
        retry_count += retries
        if result is SweepAttempt.DELETED:
            deleted += 1
        elif result is SweepAttempt.CARRIER_LEASE_LIVE:
            carrier_lease_live += 1
        elif result is SweepAttempt.TAMPERED:
            tampered += 1
        elif result is SweepAttempt.ERROR:
            errors += 1
        else:
            not_due += 1
    # Directory-reconciliation scan phase: budget-bounded (dimension = 0
    # disables it entirely, the RUNNER_TAIL_BUDGET default) and only
    # attempted while duration budget remains from the record-sweep work
    # above. Adopted-orphan admissions fold into the shared transitions
    # counter (`work_counters()` below), so they participate in the same
    # TRANSITIONED progress signal a real state transition does.
    if monotonic() - started < budget.max_duration_seconds:
        try:
            orphan_outcome = scan_and_adopt_orphans()
        except LockContended:
            if blocker is CleanupBlocker.NONE:
                blocker = CleanupBlocker.LOCK_CONTENDED
    remaining_due = max(0, len(pending) - examined)
    orphan_work_remains = (
        not orphan_outcome.directory_complete or orphan_outcome.pending_candidates > 0
    )
    if lock_contended or not discovery_complete or orphan_work_remains:
        remaining_due = max(1, remaining_due)
    if orphan_work_remains and blocker is CleanupBlocker.NONE:
        blocker = CleanupBlocker.RECORD_BUDGET
    records_inspected, replay_bytes, transitions, cursor_writes = work_counters()
    if deleted:
        progress = CleanupProgress.RETIRED
    elif transitions:
        progress = CleanupProgress.TRANSITIONED
    elif cursor_repair and cursor_writes:
        progress = CleanupProgress.CURSOR_REPAIRED
    elif cursor_writes:
        progress = CleanupProgress.CURSOR_ADVANCED
    else:
        progress = CleanupProgress.NONE
    return CaptureCleanupOutcome(
        examined=examined,
        deleted=deleted,
        deleted_bytes=deleted_bytes,
        carrier_lease_live=carrier_lease_live,
        not_due=not_due,
        tampered=tampered,
        errors=errors,
        retry_count=retry_count,
        remaining_due=remaining_due,
        records_inspected=records_inspected,
        replay_bytes=replay_bytes,
        transitions=transitions,
        cursor_writes=cursor_writes,
        progress=progress,
        blocker=blocker,
        duration=max(0.0, monotonic() - started),
    )
