"""Bounded lifecycle sweep orchestration shared by lifecycle module identities."""

from __future__ import annotations

import errno
import os
import stat
import sys
from collections.abc import Callable, Collection, Iterable
from dataclasses import replace
from typing import Any, Protocol

from ._ledger import (
    CaptureLifecycleRecord,
    CaptureReferenceStatus,
    CaptureRetentionPhase,
    CaptureState,
    same_record,
)
from ._types import (
    CaptureCleanupOutcome,
    CarrierLeaseLive,
    LockContended,
    ObservedArtifact,
    Tampered,
)

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._sweep", "autoskillit.hooks._capture._sweep"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture sweep module identity")


class SweepRecord(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def state(self) -> object: ...

    @property
    def next_attempt_at(self) -> float: ...


class NamePattern(Protocol):
    def fullmatch(self, value: str) -> object | None: ...


def due_capture_ids(
    records: Iterable[SweepRecord],
    now: float,
    terminal_states: Collection[object],
) -> list[str]:
    due = [
        (record.next_attempt_at, record.capture_id)
        for record in records
        if record.state not in terminal_states and record.next_attempt_at <= now
    ]
    due.sort()
    return [capture_id for _next_attempt_at, capture_id in due]


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
    except BaseException:
        os.close(fd)
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
    store: Any,
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
    store: Any,
    capture_id: str,
    *,
    lifecycle_error: type[RuntimeError],
    max_retry_seconds: float,
) -> tuple[str, int, int]:
    expected: CaptureLifecycleRecord | None = None
    lease: ObservedArtifact | None = None
    try:
        with store._locked(blocking=False):
            records, compaction_epoch, size = store._load_locked()
            record = records.get(capture_id)
            now = store._wall_clock()
            if (
                record is None
                or record.state in {CaptureState.DELETED, CaptureState.TAMPERED}
                or record.next_attempt_at > now
            ):
                return ("not_due", 0, 0)
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
                    return ("not_due", 0, 0)
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
                return ("not_due", 0, 0)
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
                    return ("deleted", 0, 0)
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
        return ("deleted", deleted_bytes, 0)
    except CarrierLeaseLive:
        return ("carrier_lease_live", 0, 0)
    except Tampered:
        if expected is not None:
            _transition_if_current(
                store,
                expected,
                lambda record: replace(
                    record,
                    state=CaptureState.TAMPERED,
                    retention_phase=CaptureRetentionPhase.TAMPERED,
                    revision=record.revision + 1,
                ),
            )
        return ("tampered", 0, 0)
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
        return ("error", 0, retries)
    finally:
        if lease is not None:
            os.close(lease.fd)


def run_bounded_sweep(
    *,
    max_items: int,
    max_duration_seconds: float,
    monotonic: Callable[[], float],
    wall_clock: Callable[[], float],
    due_ids: Callable[[float], list[str]],
    sweep_one: Callable[[str], tuple[str, int, int]],
) -> CaptureCleanupOutcome:
    started = monotonic()
    examined = deleted = deleted_bytes = carrier_lease_live = 0
    not_due = tampered = errors = retry_count = 0
    try:
        pending = due_ids(wall_clock())
    except LockContended:
        return CaptureCleanupOutcome(
            remaining_due=1,
            duration=max(0.0, monotonic() - started),
        )
    lock_contended = False
    for capture_id in pending[:max_items]:
        if monotonic() - started >= max_duration_seconds:
            break
        try:
            result, logical_bytes, retries = sweep_one(capture_id)
        except LockContended:
            lock_contended = True
            break
        examined += 1
        deleted_bytes += logical_bytes
        retry_count += retries
        if result == "deleted":
            deleted += 1
        elif result == "carrier_lease_live":
            carrier_lease_live += 1
        elif result == "tampered":
            tampered += 1
        elif result == "error":
            errors += 1
        else:
            not_due += 1
    if lock_contended:
        remaining_due = max(1, len(pending) - examined)
    else:
        try:
            remaining_due = len(due_ids(wall_clock()))
        except LockContended:
            remaining_due = max(1, len(pending) - examined)
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
        duration=max(0.0, monotonic() - started),
    )
