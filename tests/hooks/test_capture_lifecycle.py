"""Durable lifecycle, carrier-liveness, and reclamation tests."""

from __future__ import annotations

import concurrent.futures
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from typing import cast

import pytest

import autoskillit.hooks._capture._capacity as capture_capacity
import autoskillit.hooks._capture._reconcile as capture_reconcile
import autoskillit.hooks._capture._sweep_cursor as sweep_cursor
import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture._failure_policy import (
    CaptureFailureReason,
    runtime_failure_reason,
)
from autoskillit.hooks._capture._lifecycle_policy import CaptureStatus
from autoskillit.hooks._capture._snapshot import (
    CaptureAuthorityError,
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture._syntax import PUBLIC_NAME_RE
from autoskillit.hooks._capture._types import (
    CaptureCapacitySpec,
    CaptureFailureEvidence,
    CleanupBlocker,
    CleanupProgress,
    SweepBudgetSpec,
)
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    CaptureRoot,
    CaptureSetupError,
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import (
    CaptureCapacityError,
    CaptureCapacityReason,
    CaptureDeliveryStatus,
    CaptureLedgerError,
    CaptureLifecycleError,
    CaptureLifecycleRecord,
    CaptureLifecycleStore,
    CaptureReferenceStatus,
    CaptureRetentionPhase,
    CaptureSnapshotStatus,
    CaptureState,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"


def _sweep_budget(max_attempts: int, max_duration_seconds: float) -> SweepBudgetSpec:
    return SweepBudgetSpec(
        max_records_inspected=capture_lifecycle.MAX_ACTIVE_RECORDS,
        max_replay_bytes=capture_lifecycle.MAX_LEDGER_BYTES,
        max_attempts=max_attempts,
        max_transitions=max_attempts * 4,
        max_cursor_writes=max_attempts,
        max_duration_seconds=max_duration_seconds,
    )


class _Clock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def wall(self) -> float:
        return self.value

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_capacity_failure_reason_mapping_is_exhaustive_and_enum_keyed() -> None:
    mapping = capture_capacity._FAILURE_REASONS

    assert set(mapping) == set(CaptureCapacityReason)
    assert all(type(reason) is CaptureCapacityReason for reason in mapping)
    assert all(type(reason) is CaptureFailureReason for reason in mapping.values())
    assert (
        mapping[CaptureCapacityReason.PROJECTED_COMPACTED_BYTES]
        is CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED
    )
    assert (
        mapping[CaptureCapacityReason.HARD_LEDGER_CAPACITY]
        is CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED
    )


@pytest.mark.parametrize("reason", tuple(CaptureCapacityReason))
def test_capacity_error_preserves_internal_and_transported_reasons(
    reason: CaptureCapacityReason,
) -> None:
    failure = CaptureCapacityError(reason)

    assert failure.reason is reason
    assert failure.failure_reason is capture_capacity.failure_reason(reason)


def test_runtime_failure_reason_prefers_exact_transported_reason() -> None:
    class ConflictingFailure(RuntimeError):
        reason = CaptureFailureReason.UNKNOWN_SETUP
        failure_reason = CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED

    assert (
        runtime_failure_reason(ConflictingFailure())
        is CaptureFailureReason.HARD_LEDGER_CAPACITY_EXHAUSTED
    )


def test_migration_phase_persisted_values_remain_uppercase_and_pinned() -> None:
    assert {
        phase.name: phase.value for phase in capture_lifecycle._capture_migration.MigrationPhase
    } == {
        "PLANNED": "PLANNED",
        "QUARANTINED": "QUARANTINED",
        "RETIRED": "RETIRED",
    }


def test_lifecycle_record_rejects_invalid_identity_at_construction() -> None:
    with pytest.raises(
        capture_lifecycle._capture_ledger.LedgerCodecError,
        match="invalid lifecycle record fields",
    ):
        CaptureLifecycleRecord(
            capture_id="invalid",
            state=CaptureState.RESERVED,
            staging_name=".capture-staging-invalid-0000000000000000",
            public_name="shell_invalid.log",
            project_identity=(1, 2),
            root_identity=(3, 4),
            created_at=1.0,
            next_attempt_at=2.0,
            incarnation="1" * 32,
            revision=1,
        )


@pytest.mark.parametrize("field_name", ("created_at", "next_attempt_at", "retention_at"))
@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_ledger_rejects_nonfinite_timestamps(field_name: str, value: float) -> None:
    record = CaptureLifecycleRecord(
        capture_id=_CAPTURE_ID,
        state=CaptureState.RESERVED,
        staging_name=f".capture-staging-{_CAPTURE_ID}-0000000000000000",
        public_name=f"shell_{_CAPTURE_ID}.log",
        project_identity=(1, 2),
        root_identity=(3, 4),
        created_at=1.0,
        next_attempt_at=2.0,
        incarnation="1" * 32,
        revision=1,
    )
    serialized = capture_lifecycle._record_to_dict(record)
    serialized[field_name] = value

    with pytest.raises(CaptureLedgerError, match="invalid lifecycle record fields"):
        capture_lifecycle._record_from_dict(serialized)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"capture_status": CaptureStatus.COMPLETE}, "complete capture"),
        ({"retention_phase": CaptureRetentionPhase.DELETED}, "deleted retention"),
        ({"state": CaptureState.DELETING}, "deleting retention"),
        ({"snapshot_status": CaptureSnapshotStatus.VERIFIED}, "snapshot status"),
    ),
)
def test_ledger_rejects_invalid_outcome_projection_combinations(
    changes: dict[str, object],
    message: str,
) -> None:
    record = CaptureLifecycleRecord(
        capture_id=_CAPTURE_ID,
        state=CaptureState.RESERVED,
        staging_name=f".capture-staging-{_CAPTURE_ID}-0000000000000000",
        public_name=f"shell_{_CAPTURE_ID}.log",
        project_identity=(1, 2),
        root_identity=(3, 4),
        created_at=1.0,
        next_attempt_at=2.0,
        incarnation="1" * 32,
        revision=1,
    )

    with pytest.raises(
        capture_lifecycle._capture_ledger.LedgerCodecError,
        match=message,
    ):
        replace(record, **changes)


def _open_store(project: Path, clock: _Clock):
    project.mkdir(exist_ok=True)
    anchor = open_project_anchor(str(project))
    try:
        root = open_capture_root(anchor, create=True)
    except BaseException:
        anchor.close()
        raise
    store = CaptureLifecycleStore.from_open_authorities(
        anchor,
        root,
        wall_clock=clock.wall,
        monotonic=clock.monotonic,
    )
    return anchor, root, store


def _capture_dir(project: Path) -> Path:
    return project.joinpath(*CAPTURE_PATH_COMPONENTS)


def _frame_from_payload(payload: bytes) -> bytes:
    return (
        capture_lifecycle.FRAME_MAGIC
        + len(payload).to_bytes(4, "big")
        + payload
        + hashlib.sha256(payload).digest()
    )


def _legacy_frame(record: dict[str, object], *, generation: int = 1) -> bytes:
    payload = capture_lifecycle._capture_ledger.canonical_json(
        {
            "format_version": 1,
            "generation": generation,
            "record": record,
        }
    )
    return _frame_from_payload(payload)


def _legacy_record(
    *,
    capture_id: str,
    state: CaptureState,
    project_identity: tuple[int, int],
    root_identity: tuple[int, int],
    artifact_identity: tuple[int, int] | None,
    observed_size: int,
) -> dict[str, object]:
    return {
        "artifact_identity": (list(artifact_identity) if artifact_identity is not None else None),
        "capture_id": capture_id,
        "created_at": 1_000_000.0,
        "deletion_nonce": "",
        "next_attempt_at": 2_000_000.0,
        "project_identity": list(project_identity),
        "public_name": f"shell_{capture_id}.log",
        "quarantine_name": "",
        "retention_at": None,
        "retry_count": 0,
        "root_identity": list(root_identity),
        "sha256": hashlib.sha256(b"captured").hexdigest(),
        "size": observed_size,
        "staging_name": f".capture-staging-{capture_id}-{'1' * 16}",
        "state": state.value,
    }


def _race_calls(
    *calls: Callable[[], object],
    after_start: Callable[[], None] | None = None,
) -> list[object]:
    barrier = threading.Barrier(len(calls) + 1)

    def invoke(call: Callable[[], object]) -> object:
        barrier.wait(timeout=5)
        try:
            return call()
        except CaptureLifecycleError as exc:
            return exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(invoke, call) for call in calls]
        barrier.wait(timeout=5)
        if after_start is not None:
            after_start()
        return [future.result(timeout=5) for future in futures]


def _coordinate_transition_race(
    store: CaptureLifecycleStore,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], None]:
    real_locked = store._locked
    real_load = store._load_locked
    entrants = threading.Barrier(3)
    first_loaded = threading.Event()
    release_first = threading.Event()
    counter_lock = threading.Lock()
    lock_entries = 0
    load_entries = 0

    @contextmanager
    def coordinated_locked(*, blocking: bool = True):
        nonlocal lock_entries
        with counter_lock:
            lock_entries += 1
            coordinate = lock_entries <= 2
        if coordinate:
            entrants.wait(timeout=5)
        with real_locked(blocking=blocking):
            yield

    def coordinated_load():
        nonlocal load_entries
        result = real_load()
        with counter_lock:
            load_entries += 1
            first = load_entries == 1
        if first:
            first_loaded.set()
            assert release_first.wait(timeout=5)
        return result

    def after_start() -> None:
        entrants.wait(timeout=5)
        assert first_loaded.wait(timeout=5)
        with counter_lock:
            assert load_entries == 1
        release_first.set()

    monkeypatch.setattr(store, "_locked", coordinated_locked)
    monkeypatch.setattr(store, "_load_locked", coordinated_load)
    return after_start


def _assert_one_lifecycle_race_loser(
    results: list[object],
    *allowed_messages: str,
) -> None:
    losers = [result for result in results if isinstance(result, Exception)]
    assert len(losers) == 1
    assert type(losers[0]) is CaptureLifecycleError
    assert str(losers[0]) in allowed_messages


def test_lifecycle_store_rejects_direct_construction(tmp_path: Path) -> None:
    anchor, root, _store = _open_store(tmp_path / "project", _Clock())
    try:
        with pytest.raises(CaptureLifecycleError, match="factory-created"):
            CaptureLifecycleStore(
                root.fd,
                project_identity=(anchor.identity.device, anchor.identity.inode),
                root_identity=(root.identity.device, root.identity.inode),
            )
    finally:
        root.close()
        anchor.close()


def test_mixed_legacy_history_migrates_once_without_manufacturing_final_authority(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    project_identity = (anchor.identity.device, anchor.identity.inode)
    root_identity = (root.identity.device, root.identity.inode)
    carrier = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    carrier.write_bytes(b"captured")
    carrier.chmod(0o600)
    carrier_value = carrier.stat()
    artifact_identity = (carrier_value.st_dev, carrier_value.st_ino)
    other_id = "1111111111111111"
    current = CaptureLifecycleRecord(
        capture_id=other_id,
        state=CaptureState.RESERVED,
        staging_name=f".capture-staging-{other_id}-{'2' * 16}",
        public_name=f"shell_{other_id}.log",
        project_identity=project_identity,
        root_identity=root_identity,
        created_at=1_000_000.0,
        next_attempt_at=2_000_000.0,
        incarnation="2" * 32,
        revision=1,
    )
    frames = [
        _legacy_frame(
            _legacy_record(
                capture_id=_CAPTURE_ID,
                state=CaptureState.RESERVED,
                project_identity=project_identity,
                root_identity=root_identity,
                artifact_identity=None,
                observed_size=0,
            )
        ),
        _legacy_frame(
            _legacy_record(
                capture_id=_CAPTURE_ID,
                state=CaptureState.STAGED,
                project_identity=project_identity,
                root_identity=root_identity,
                artifact_identity=artifact_identity,
                observed_size=len(b"captured"),
            )
        ),
        _legacy_frame(
            _legacy_record(
                capture_id=_CAPTURE_ID,
                state=CaptureState.FINALIZED,
                project_identity=project_identity,
                root_identity=root_identity,
                artifact_identity=artifact_identity,
                observed_size=len(b"captured"),
            )
        ),
        capture_lifecycle._capture_ledger.encode_frame(
            capture_lifecycle._record_to_dict(current),
            compaction_epoch=1,
        ),
    ]
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    ledger.write_bytes(b"".join(frames))
    ledger.chmod(0o600)
    try:
        store = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=lambda: 1_000_000.0,
        )
        migrated = store.get_record(_CAPTURE_ID)
        preserved = store.get_record(other_id)
        first_bytes = ledger.read_bytes()
        reopened = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=lambda: 1_000_000.0,
        )

        assert migrated is not None
        assert migrated.state is CaptureState.ABANDONED
        assert migrated.capture_status is CaptureStatus.LEGACY_CLEANUP_ONLY
        assert migrated.snapshot_status is CaptureSnapshotStatus.ABSENT
        assert migrated.manifest is None
        assert migrated.legacy_cleanup is not None
        assert migrated.legacy_cleanup.observed_size == len(b"captured")
        assert migrated.revision == 3
        assert preserved == reopened.get_record(other_id)
        assert ledger.read_bytes() == first_bytes
        decoded = capture_lifecycle._capture_ledger.decode_ledger(first_bytes)
        assert decoded.frames
        assert {frame.format_version for frame in decoded.frames} == {2}
        assert {frame.compaction_epoch for frame in decoded.frames} == {2}
        assert (
            not _capture_dir(project)
            .joinpath(capture_lifecycle._capture_migration.MIGRATION_NAME)
            .exists()
        )
        assert _capture_dir(project).joinpath(sweep_cursor.CURSOR_NAME).exists()
    finally:
        root.close()
        anchor.close()


def test_legacy_migration_retires_until_reduced_publication_capacity_fits(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    project_identity = (anchor.identity.device, anchor.identity.inode)
    root_identity = (root.identity.device, root.identity.inode)
    capture_ids = (_CAPTURE_ID, "1111111111111111", "2222222222222222")
    frames: list[bytes] = []
    normalized: list[CaptureLifecycleRecord] = []
    carriers: list[Path] = []
    for capture_id in capture_ids:
        carrier = _capture_dir(project) / f"shell_{capture_id}.log"
        carrier.write_bytes(b"captured")
        carrier.chmod(0o600)
        carriers.append(carrier)
        value = carrier.stat()
        legacy = _legacy_record(
            capture_id=capture_id,
            state=CaptureState.FINALIZED,
            project_identity=project_identity,
            root_identity=root_identity,
            artifact_identity=(value.st_dev, value.st_ino),
            observed_size=len(b"captured"),
        )
        frames.append(_legacy_frame(legacy))
        normalized.append(
            capture_lifecycle._capture_ledger.legacy_record_from_dict(
                legacy,
                revision=1,
                compaction_epoch=2,
            )
        )
    frame_bytes = max(
        len(
            capture_lifecycle._capture_ledger.encode_frame(
                capture_lifecycle._capture_ledger.record_to_dict(record),
                compaction_epoch=2,
            )
        )
        for record in normalized
    )
    low = frame_bytes * 2 + 64
    high = low + 2048
    store = CaptureLifecycleStore(
        root.fd,
        project_identity=project_identity,
        root_identity=root_identity,
        wall_clock=lambda: 3_000_000.0,
        _factory_token=capture_lifecycle._STORE_FACTORY_TOKEN,
    )
    store._capacity = CaptureCapacitySpec(
        max_operational_records=8,
        max_retained_records=8,
        max_forensic_records=8,
        max_tombstones=1,
        compaction_low_bytes=low,
        compaction_high_bytes=high,
        hard_ledger_bytes=high + 5120,
        cursor_headroom_bytes=1024,
        tamper_headroom_bytes=1024,
        reclamation_headroom_bytes=1024,
    )
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    ledger.write_bytes(b"".join(frames))
    ledger.chmod(0o600)
    try:
        store.get_record(_CAPTURE_ID)
        decoded = capture_lifecycle._capture_ledger.decode_ledger(ledger.read_bytes())
        assert {frame.format_version for frame in decoded.frames} == {2}
        assert sum(not carrier.exists() for carrier in carriers) >= 1
        assert (
            not _capture_dir(project)
            .joinpath(capture_lifecycle._capture_migration.MIGRATION_NAME)
            .exists()
        )
    finally:
        root.close()
        anchor.close()


def _verified_snapshot(
    store: CaptureLifecycleStore,
    artifact,
    data: bytes,
    clock: _Clock,
):
    measurement = CaptureMeasurement.from_bytes(
        data,
        inline_bytes=max(1, len(data)),
    )
    return verify_capture_snapshot(
        fd=artifact.fd,
        capture_id=artifact.authority.capture_id,
        incarnation=artifact.authority.incarnation,
        project_identity=store._project_identity,
        root_identity=store._root_identity,
        carrier_name=artifact.name,
        carrier_identity=(artifact.identity.device, artifact.identity.inode),
        measurement=measurement,
        command_outcome=CommandOutcome.exited(0),
        expected_revision=artifact.authority.expected_revision,
        finalized_at=clock.wall(),
        retention_deadline=clock.wall() + 3600.0,
    )


def _commit_verified(
    store: CaptureLifecycleStore,
    artifact,
    data: bytes,
    clock: _Clock,
):
    return store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, data, clock),
        issue_reference=False,
    )


def _finalized_capture(project: Path, clock: _Clock, capture_id: str = _CAPTURE_ID):
    with ExitStack() as cleanup:
        anchor, root, store = _open_store(project, clock)
        cleanup.callback(anchor.close)
        cleanup.callback(root.close)
        artifact = create_capture_artifact(root, capture_id, store)
        cleanup.callback(artifact.release_lease)
        cleanup.callback(artifact.close_artifact_fd)
        os.write(artifact.fd, b"captured")
        _commit_verified(store, artifact, b"captured", clock)
        cleanup.pop_all()
        return anchor, root, store, artifact


def test_final_commit_reconciles_durable_append_after_reported_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    snapshot = _verified_snapshot(store, artifact, b"captured", clock)
    real_append = store._append_locked
    injected = False

    def append_then_fail(record, records, compaction_epoch, size):
        nonlocal injected
        real_append(record, records, compaction_epoch, size)
        if record.state is CaptureState.FINALIZED and not injected:
            injected = True
            raise capture_lifecycle.CaptureTransitionCommittedError("post-FINAL append fault")

    monkeypatch.setattr(store, "_append_locked", append_then_fail)
    try:
        finalized = store.commit_verified_snapshot(snapshot, issue_reference=True)
        durable = store.get_record(_CAPTURE_ID)

        assert injected
        assert finalized.issuance is not None
        assert durable is not None
        assert durable.state is CaptureState.FINALIZED
        assert durable.manifest == finalized.snapshot.manifest
        assert durable.revision == finalized.finalized_at_revision
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_final_commit_does_not_reconcile_reported_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    snapshot = _verified_snapshot(store, artifact, b"captured", clock)
    real_fsync = os.fsync

    def sync_then_report_failure(fd: int) -> None:
        real_fsync(fd)
        raise OSError("reported lifecycle fsync failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(capture_lifecycle.os, "fsync", sync_then_report_failure)
            with pytest.raises(OSError, match="reported lifecycle fsync failure"):
                store.commit_verified_snapshot(snapshot, issue_reference=True)

        readable = store.get_record(_CAPTURE_ID)
        assert readable is not None
        assert readable.state is CaptureState.FINALIZED
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_verified_final_and_failure_commits_have_one_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    snapshot = _verified_snapshot(store, artifact, b"captured", clock)
    try:
        results = _race_calls(
            lambda: store.commit_verified_snapshot(snapshot, issue_reference=False),
            lambda: store.commit_capture_failure(
                artifact.authority,
                CaptureFailureEvidence(stage="race", detail="failure won"),
                observed_size=8,
            ),
            after_start=_coordinate_transition_race(store, monkeypatch),
        )
        durable = store.get_record(_CAPTURE_ID)

        _assert_one_lifecycle_race_loser(
            results,
            "stale or invalid lifecycle transition",
            "verified snapshot does not match write authority",
        )
        assert durable is not None
        assert durable.state in {CaptureState.FINALIZED, CaptureState.FAILED}
        assert durable.revision == artifact.authority.expected_revision + 1
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_conflicting_final_commits_do_not_issue_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    snapshot = _verified_snapshot(store, artifact, b"captured", clock)
    try:
        results = _race_calls(
            lambda: store.commit_verified_snapshot(snapshot, issue_reference=False),
            lambda: store.commit_verified_snapshot(snapshot, issue_reference=True),
            after_start=_coordinate_transition_race(store, monkeypatch),
        )
        durable = store.get_record(_CAPTURE_ID)

        _assert_one_lifecycle_race_loser(
            results,
            "verified snapshot does not match write authority",
        )
        assert durable is not None
        assert durable.state is CaptureState.FINALIZED
        assert durable.reference_status in {
            CaptureReferenceStatus.NOT_REQUESTED,
            CaptureReferenceStatus.ISSUED,
        }
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_reference_publication_and_revocation_race_ends_revoked(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, b"captured", clock),
        issue_reference=True,
    )
    try:
        results = _race_calls(
            lambda: store.publish_reference(finalized),
            lambda: store.revoke_reference(finalized),
        )
        durable = store.get_record(_CAPTURE_ID)

        assert not isinstance(results[1], Exception)
        if isinstance(results[0], Exception):
            assert type(results[0]) is CaptureLifecycleError
            assert str(results[0]) == "capture reference transition predecessor changed"
        assert durable is not None
        assert durable.reference_status is CaptureReferenceStatus.REVOKED
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_delivery_transition_rejects_illegal_predecessor_target_pair(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = _commit_verified(store, artifact, b"captured", clock)
    try:
        with pytest.raises(CaptureLifecycleError, match="not allowed"):
            store.transition_delivery(
                finalized,
                expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
                target=CaptureDeliveryStatus.DELIVERED,
            )
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_delivered_reference_cannot_be_reclassified_as_unavailable(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, b"captured", clock),
        issue_reference=True,
    )
    published = store.publish_reference(finalized)
    store.transition_delivery(
        published,
        expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
        target=CaptureDeliveryStatus.ATTEMPTING,
    )
    store.transition_delivery(
        published,
        expected=CaptureDeliveryStatus.ATTEMPTING,
        target=CaptureDeliveryStatus.DELIVERED,
    )
    try:
        with pytest.raises(CaptureLifecycleError, match="invalid lifecycle successor"):
            store.mark_reference_unavailable(
                finalized,
                reason_code="TOO_LATE",
            )
        durable = store.get_record(_CAPTURE_ID)
        assert durable is not None
        assert durable.reference_status is CaptureReferenceStatus.PUBLISHED
        assert durable.delivery_status is CaptureDeliveryStatus.DELIVERED
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_invalid_unavailable_reason_does_not_commit_transition(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, b"captured", clock),
        issue_reference=True,
    )
    before = store.get_record(_CAPTURE_ID)
    try:
        with pytest.raises(CaptureAuthorityError, match="invalid unavailable capture reference"):
            store.mark_reference_unavailable(finalized, reason_code="not-valid")

        assert store.get_record(_CAPTURE_ID) == before
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_restart_normalization_surfaces_unexpected_lease_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, b"captured", clock),
        issue_reference=True,
    )
    published = store.publish_reference(finalized)
    store.transition_delivery(
        published,
        expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
        target=CaptureDeliveryStatus.ATTEMPTING,
    )

    def fail_cleanup_lease(
        _store: CaptureLifecycleStore,
        _record: CaptureLifecycleRecord,
    ) -> None:
        raise OSError("unexpected cleanup lease failure")

    monkeypatch.setattr(
        CaptureLifecycleStore,
        "_acquire_cleanup_lease",
        fail_cleanup_lease,
    )
    try:
        with pytest.raises(OSError, match="unexpected cleanup lease failure"):
            CaptureLifecycleStore.from_open_authorities(
                anchor,
                root,
                wall_clock=clock.wall,
                monotonic=clock.monotonic,
            )
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_restart_normalization_closes_lease_when_record_turns_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    finalized = store.commit_verified_snapshot(
        _verified_snapshot(store, artifact, b"captured", clock),
        issue_reference=True,
    )
    published = store.publish_reference(finalized)
    store.transition_delivery(
        published,
        expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
        target=CaptureDeliveryStatus.ATTEMPTING,
    )
    lease_fd = os.open(os.devnull, os.O_RDONLY)
    lease = capture_lifecycle._ObservedArtifact(
        fd=lease_fd,
        identity=(0, 0),
        nlink=1,
        size=0,
    )
    original_load_locked = CaptureLifecycleStore._load_locked
    load_count = 0

    def load_with_stale_record(
        current_store: CaptureLifecycleStore,
    ) -> tuple[dict[str, CaptureLifecycleRecord], int, int]:
        nonlocal load_count
        records, compaction_epoch, size = original_load_locked(current_store)
        load_count += 1
        if load_count == 2:
            records = dict(records)
            records[_CAPTURE_ID] = replace(
                records[_CAPTURE_ID],
                revision=records[_CAPTURE_ID].revision + 1,
            )
        return records, compaction_epoch, size

    monkeypatch.setattr(CaptureLifecycleStore, "_load_locked", load_with_stale_record)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "_acquire_cleanup_lease",
        lambda _store, _record: lease,
    )
    try:
        store._normalize_interrupted_deliveries()

        with pytest.raises(OSError) as closed:
            os.fstat(lease_fd)
        assert closed.value.errno == errno.EBADF
    finally:
        try:
            os.close(lease_fd)
        except OSError as exc:
            assert exc.errno == errno.EBADF
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_carrier_fsync_precedes_final_ledger_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    carrier_identity = (artifact.identity.device, artifact.identity.inode)
    events: list[str] = []
    real_fsync = os.fsync
    real_write_all = capture_lifecycle._capture_ledger.write_all

    def recording_fsync(fd: int) -> None:
        value = os.fstat(fd)
        if (value.st_dev, value.st_ino) == carrier_identity:
            events.append("carrier_fsync")
        real_fsync(fd)

    def recording_write_all(fd: int, payload: bytes) -> None:
        events.append("ledger_append")
        real_write_all(fd, payload)

    monkeypatch.setattr(capture_lifecycle._capture_snapshot.os, "fsync", recording_fsync)
    monkeypatch.setattr(
        capture_lifecycle._capture_ledger,
        "write_all",
        recording_write_all,
    )
    try:
        verified = _verified_snapshot(store, artifact, b"captured", clock)
        store.commit_verified_snapshot(verified, issue_reference=False)

        assert events.count("carrier_fsync") == 1
        assert events.index("carrier_fsync") < events.index("ledger_append")
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_deleted_capture_id_starts_a_new_incarnation_at_revision_one(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(
        tmp_path / "project",
        clock,
    )
    old = store.get_record(_CAPTURE_ID)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    try:
        outcome = store.sweep(_sweep_budget(8, 1))
        tombstone = store.get_record(_CAPTURE_ID)
        replacement = store.reserve_capture(_CAPTURE_ID)

        assert outcome.deleted == 1
        assert old is not None
        assert tombstone is not None
        assert tombstone.state is CaptureState.DELETED
        assert replacement.state is CaptureState.RESERVED
        assert replacement.revision == 1
        assert replacement.incarnation != old.incarnation
        assert store.get_record(_CAPTURE_ID) == replacement
    finally:
        root.close()
        anchor.close()


def _seed_finalized_captures(
    root: CaptureRoot,
    store: CaptureLifecycleStore,
    *,
    count: int,
) -> list[str]:
    names = []
    for index in range(count):
        capture_id = f"{index + 1:016x}"
        artifact = create_capture_artifact(root, capture_id, store)
        data = bytes([index % 251]) * (index + 1)
        os.write(artifact.fd, data)
        _commit_verified(store, artifact, data, _Clock(store._wall_clock()))
        names.append(artifact.name)
        artifact.close_artifact_fd()
        artifact.release_lease()
    return names


def test_managed_artifact_is_published_only_after_durable_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    try:
        record = store.get_record(_CAPTURE_ID)
        assert record is not None
        assert record.state is CaptureState.PUBLISHED_WRITING
        assert record.artifact_identity == (
            artifact.identity.device,
            artifact.identity.inode,
        )
        assert artifact.name == f"shell_{_CAPTURE_ID}.log"
        assert (_capture_dir(project) / artifact.name).is_file()
        assert not (_capture_dir(project) / record.staging_name).exists()
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


@pytest.mark.parametrize(
    "identity",
    (
        (1,),
        (1, 2, 3),
        (True, 2),
        (-1, 2),
        ("1", 2),
        [1, 2],
    ),
)
def test_mark_staged_rejects_invalid_artifact_identity(
    tmp_path: Path,
    identity: object,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        reserved = store.reserve_capture(_CAPTURE_ID)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="invalid staged artifact identity",
        ):
            store.mark_staged(
                store._authority_for(reserved),
                cast(tuple[int, int], identity),
            )

        record = store.get_record(_CAPTURE_ID)
        assert record is not None
        assert record.state is CaptureState.RESERVED
        assert record.artifact_identity is None
    finally:
        root.close()
        anchor.close()


def test_staged_identity_is_committed_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    staged_record: capture_lifecycle.CaptureLifecycleRecord | None = None
    staged_identity: tuple[int, int] | None = None

    def interrupt_publication(
        src: str,
        _dst: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del dst_dir_fd, follow_symlinks
        nonlocal staged_record, staged_identity
        staged_record = store.get_record(_CAPTURE_ID)
        value = os.stat(src, dir_fd=src_dir_fd, follow_symlinks=False)
        staged_identity = (value.st_dev, value.st_ino)
        raise OSError("injected publication interruption")

    try:
        monkeypatch.setattr(capture_lifecycle.os, "link", interrupt_publication)
        with pytest.raises(OSError, match="publication interruption"):
            store.create_artifact(_CAPTURE_ID)

        assert staged_record is not None
        assert staged_record.state is CaptureState.STAGED
        assert staged_record.artifact_identity == staged_identity
        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        assert failed.state is CaptureState.FAILED
        assert failed.artifact_identity == staged_identity
        assert (_capture_dir(project) / failed.staging_name).exists()
        assert not (_capture_dir(project) / failed.public_name).exists()
    finally:
        root.close()
        anchor.close()


def test_creation_failure_preserves_failed_state_recovery_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)

    def fail_mark_staged(_authority, _identity: tuple[int, int]) -> None:
        raise capture_lifecycle.CaptureLifecycleError("primary creation failure")

    def fail_recovery(
        _authority,
        _evidence,
        *,
        observed_size: int,
    ) -> None:
        del observed_size
        raise capture_lifecycle.CaptureLifecycleError("secondary recovery failure")

    try:
        monkeypatch.setattr(store, "mark_staged", fail_mark_staged)
        monkeypatch.setattr(store, "commit_capture_failure", fail_recovery)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="primary creation failure",
        ) as raised:
            store.create_artifact(_CAPTURE_ID)

        assert any(
            "secondary recovery failure" in note for note in getattr(raised.value, "__notes__", ())
        )
    finally:
        root.close()
        anchor.close()


def test_creation_committed_error_still_cleans_artifact_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    real_mark_staged = store.mark_staged
    real_close = os.close
    closed: list[int] = []

    def mark_staged_then_fail(authority, identity):
        real_mark_staged(authority, identity)
        raise capture_lifecycle.CaptureTransitionCommittedError("post-staged append fault")

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(store, "mark_staged", mark_staged_then_fail)
    monkeypatch.setattr(capture_lifecycle.os, "close", record_close)
    try:
        with pytest.raises(CaptureSetupError, match="cannot create managed capture"):
            create_capture_artifact(root, _CAPTURE_ID, store)

        assert len(set(closed)) >= 2
        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        assert failed.state is CaptureState.FAILED
    finally:
        root.close()
        anchor.close()


def test_failed_recovery_without_identity_preserves_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)

    def fail_mark_staged(_capture_id: str, _identity: tuple[int, int]) -> None:
        raise capture_lifecycle.CaptureLifecycleError("primary creation failure")

    try:
        monkeypatch.setattr(store, "mark_staged", fail_mark_staged)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="primary creation failure",
        ):
            store.create_artifact(_CAPTURE_ID)

        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        assert failed.state is CaptureState.FAILED
        assert failed.artifact_identity is None
        staging = _capture_dir(project) / failed.staging_name
        staging.unlink()
        staging.write_bytes(b"replacement")

        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))

        assert outcome.tampered == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
        assert staging.read_bytes() == b"replacement"
    finally:
        root.close()
        anchor.close()


def test_interrupted_publication_fsync_recovers_public_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    real_fsync = os.fsync
    fail_once = True

    def interrupted_fsync(fd: int) -> None:
        nonlocal fail_once
        if fail_once and fd == root.fd:
            fail_once = False
            raise OSError("injected publication fsync interruption")
        real_fsync(fd)

    try:
        monkeypatch.setattr(capture_lifecycle.os, "fsync", interrupted_fsync)
        with pytest.raises(OSError, match="publication fsync interruption"):
            store.create_artifact(_CAPTURE_ID)

        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        assert failed.state is CaptureState.FAILED
        assert failed.artifact_identity is not None
        assert not (_capture_dir(project) / failed.staging_name).exists()
        assert (_capture_dir(project) / failed.public_name).exists()

        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))
        assert outcome.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
    finally:
        root.close()
        anchor.close()


def test_failed_published_commit_recovers_public_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)

    def interrupt_published_commit(
        _capture_id: str,
    ) -> capture_lifecycle.CaptureLifecycleRecord:
        raise OSError("injected published commit interruption")

    try:
        monkeypatch.setattr(store, "mark_published", interrupt_published_commit)
        with pytest.raises(OSError, match="published commit interruption"):
            store.create_artifact(_CAPTURE_ID)

        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        assert failed.state is CaptureState.FAILED
        assert failed.artifact_identity is not None
        assert (_capture_dir(project) / failed.public_name).exists()

        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))
        assert outcome.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
    finally:
        root.close()
        anchor.close()


def test_staged_identity_replacement_is_preserved_as_tampered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    real_link = os.link

    def replace_before_link(
        src: str,
        dst: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        staging = _capture_dir(project) / src
        staging.unlink()
        staging.write_bytes(b"replacement")
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    try:
        monkeypatch.setattr(capture_lifecycle.os, "link", replace_before_link)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="publication identity changed",
        ):
            store.create_artifact(_CAPTURE_ID)

        failed = store.get_record(_CAPTURE_ID)
        assert failed is not None
        staging = _capture_dir(project) / failed.staging_name
        public = _capture_dir(project) / failed.public_name
        assert failed.artifact_identity is not None
        assert (staging.stat().st_dev, staging.stat().st_ino) != failed.artifact_identity

        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))
        assert outcome.tampered == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
        assert staging.read_bytes() == b"replacement"
        assert public.read_bytes() == b"replacement"
    finally:
        root.close()
        anchor.close()


def test_quarantine_replacement_is_preserved_as_tampered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    real_unlink = os.unlink
    fail_once = True

    def interrupt_quarantine_unlink(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal fail_once
        if fail_once and path.startswith(".capture-quarantine-"):
            fail_once = False
            raise OSError("injected quarantine interruption")
        real_unlink(path, dir_fd=dir_fd)

    try:
        monkeypatch.setattr(
            capture_lifecycle.os,
            "unlink",
            interrupt_quarantine_unlink,
        )
        first = store.sweep(_sweep_budget(8, 1))
        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.DELETING
        assert retry.retry_count == 1

        public = _capture_dir(project) / retry.public_name
        quarantine = _capture_dir(project) / retry.quarantine_name
        assert not public.exists()
        assert quarantine.exists()
        quarantine.unlink()
        quarantine.write_bytes(b"replacement")

        clock.advance(3)
        second = store.sweep(_sweep_budget(8, 1))
        assert second.tampered == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
        assert not public.exists()
        assert quarantine.read_bytes() == b"replacement"
    finally:
        root.close()
        anchor.close()


def test_normalization_rolls_back_unverified_public_link(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    record = store.reserve_capture(_CAPTURE_ID)
    staging = _capture_dir(project) / record.staging_name
    staging.write_bytes(b"staged")
    identity = staging.stat()
    store.mark_staged(
        store._authority_for(record),
        (identity.st_dev, identity.st_ino),
    )
    external = tmp_path / "external-link"
    os.link(staging, external)

    try:
        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))

        current = store.get_record(_CAPTURE_ID)
        assert outcome.tampered == 1
        assert current is not None
        assert current.state is CaptureState.TAMPERED
        assert not (_capture_dir(project) / record.public_name).exists()
        assert staging.read_bytes() == b"staged"
        assert external.read_bytes() == b"staged"
    finally:
        root.close()
        anchor.close()


def test_quarantine_rolls_back_unverified_recovery_link(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    public = _capture_dir(project) / artifact.name
    external = tmp_path / "external-link"
    os.link(public, external)

    try:
        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))

        current = store.get_record(_CAPTURE_ID)
        assert outcome.tampered == 1
        assert current is not None
        assert current.state is CaptureState.TAMPERED
        assert current.quarantine_name
        assert not (_capture_dir(project) / current.quarantine_name).exists()
        assert public.read_bytes() == b"captured"
        assert external.read_bytes() == b"captured"
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize(
    "substitute_kind",
    ("symlink", "fifo", "hardlink", "world-writable"),
)
def test_unsafe_public_substitutes_survive_as_tampered(
    tmp_path: Path,
    substitute_kind: str,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    public = _capture_dir(project) / artifact.name
    public.unlink()
    external = tmp_path / "external"
    try:
        if substitute_kind == "symlink":
            external.write_bytes(b"external")
            public.symlink_to(external)
        elif substitute_kind == "fifo":
            os.mkfifo(public)
        elif substitute_kind == "hardlink":
            external.write_bytes(b"external")
            try:
                os.link(external, public)
            except OSError:
                pytest.skip("hardlinks unavailable")
        else:
            public.write_bytes(b"replacement")
            public.chmod(0o666)

        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))

        record = store.get_record(_CAPTURE_ID)
        assert outcome.tampered == 1
        assert record is not None
        assert record.state is CaptureState.TAMPERED
        assert os.path.lexists(public)
        if substitute_kind == "fifo":
            assert stat.S_ISFIFO(public.lstat().st_mode)
        elif substitute_kind == "symlink":
            assert public.is_symlink()
            assert external.read_bytes() == b"external"
        elif substitute_kind == "hardlink":
            assert public.samefile(external)
            assert external.read_bytes() == b"external"
        else:
            assert public.read_bytes() == b"replacement"
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize("binding", ("project_identity", "root_identity"))
def test_foreign_ledger_authority_preserves_artifact_as_tampered(
    tmp_path: Path,
    binding: str,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    record = store.get_record(_CAPTURE_ID)
    assert record is not None

    try:
        with pytest.raises(
            capture_lifecycle._capture_ledger.LedgerCodecError,
            match="FINAL manifest",
        ):
            store._transition(
                store._authority_for(record),
                allowed_states={CaptureState.FINALIZED},
                transform=lambda current: replace(
                    current,
                    **{
                        binding: (current.root_identity[0] + 1, 1),
                        "revision": current.revision + 1,
                    },
                ),
            )

        current = store.get_record(_CAPTURE_ID)
        assert current is not None
        assert current == record
        assert (_capture_dir(project) / artifact.name).read_bytes() == b"captured"
    finally:
        root.close()
        anchor.close()


def test_quiet_live_writer_survives_past_abandonment_deadline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    try:
        clock.advance(7200)
        outcome = store.sweep(_sweep_budget(8, 1))
        assert outcome.carrier_lease_live == 1
        assert outcome.deleted == 0
        assert (_capture_dir(project) / artifact.name).exists()
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_live_writer_sweep_closes_observation_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    observed_fd = -1

    def carrier_lease_live(observed: capture_lifecycle._ObservedArtifact) -> None:
        nonlocal observed_fd
        observed_fd = observed.fd
        raise capture_lifecycle._CarrierLeaseLive

    try:
        clock.advance(7200)
        monkeypatch.setattr(
            CaptureLifecycleStore,
            "_try_artifact_lease",
            staticmethod(carrier_lease_live),
        )
        outcome = store.sweep(_sweep_budget(8, 1))

        assert outcome.carrier_lease_live == 1
        assert observed_fd >= 0
        with pytest.raises(OSError):
            os.fstat(observed_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_cleanup_revalidates_revision_after_carrier_lease_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    real_acquire = store._acquire_cleanup_lease
    observed_fd = -1

    def acquire_then_advance(record: CaptureLifecycleRecord):
        nonlocal observed_fd
        lease = real_acquire(record)
        assert lease is not None
        observed_fd = lease.fd
        current = store.get_record(record.capture_id)
        assert current is not None
        store._transition(
            store._authority_for(current),
            allowed_states={current.state},
            transform=lambda value: replace(
                value,
                next_attempt_at=clock.wall() + 60,
                revision=value.revision + 1,
            ),
        )
        return lease

    monkeypatch.setattr(store, "_acquire_cleanup_lease", acquire_then_advance)
    try:
        outcome = store.sweep(_sweep_budget(8, 1))
        current = store.get_record(_CAPTURE_ID)

        assert outcome.not_due == 1
        assert outcome.deleted == 0
        assert current is not None
        assert current.state is CaptureState.FINALIZED
        assert (_capture_dir(project) / artifact.name).exists()
        with pytest.raises(OSError):
            os.fstat(observed_fd)
    finally:
        root.close()
        anchor.close()


def test_cleanup_handoff_loses_to_verified_final_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, b"captured")
    verified = _verified_snapshot(store, artifact, b"captured", clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    real_acquire = store._acquire_cleanup_lease
    lease_acquired = threading.Event()
    continue_cleanup = threading.Event()

    def pause_after_lease(record: CaptureLifecycleRecord):
        lease = real_acquire(record)
        lease_acquired.set()
        if not continue_cleanup.wait(timeout=5):
            if lease is not None:
                os.close(lease.fd)
            raise TimeoutError("cleanup handoff was not released")
        return lease

    monkeypatch.setattr(store, "_acquire_cleanup_lease", pause_after_lease)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            store.sweep,
            _sweep_budget(8, 1),
        )
        try:
            assert lease_acquired.wait(timeout=5)
            finalized = store.commit_verified_snapshot(
                verified,
                issue_reference=False,
            )
        finally:
            continue_cleanup.set()
        outcome = future.result(timeout=5)

    try:
        durable = store.get_record(_CAPTURE_ID)
        assert outcome.not_due == 1
        assert durable is not None
        assert durable.state is CaptureState.FINALIZED
        assert durable.manifest == finalized.snapshot.manifest
    finally:
        root.close()
        anchor.close()


def test_observe_closes_descriptor_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    real_open = os.open
    real_fstat = os.fstat
    observed_fd = -1

    def record_open(
        name: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal observed_fd
        fd = real_open(name, flags, mode, dir_fd=dir_fd)
        if name == artifact.name:
            observed_fd = fd
        return fd

    def fail_observed_fstat(fd: int) -> os.stat_result:
        if fd == observed_fd:
            raise OSError("injected observation failure")
        return real_fstat(fd)

    try:
        monkeypatch.setattr(capture_lifecycle.os, "open", record_open)
        monkeypatch.setattr(capture_lifecycle.os, "fstat", fail_observed_fstat)
        with pytest.raises(OSError, match="observation failure"):
            store._observe(
                artifact.name,
                (artifact.identity.device, artifact.identity.inode),
                valid_name=PUBLIC_NAME_RE,
            )

        assert observed_fd >= 0
        with pytest.raises(OSError):
            real_fstat(observed_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_observe_open_operational_failure_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    real_open = os.open

    def fail_artifact_open(
        name: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if name == artifact.name:
            raise OSError(errno.EMFILE, "descriptor limit")
        return real_open(name, flags, mode, dir_fd=dir_fd)

    try:
        clock.advance(3601)
        monkeypatch.setattr(capture_lifecycle.os, "open", fail_artifact_open)

        outcome = store.sweep(_sweep_budget(8, 1))

        record = store.get_record(_CAPTURE_ID)
        assert outcome.errors == 1
        assert outcome.tampered == 0
        assert record is not None
        assert record.state is CaptureState.FINALIZED
        assert record.manifest is not None
        assert record.manifest.sha256 == hashlib.sha256(b"captured").hexdigest()
        assert record.retry_count == 1
        assert (_capture_dir(project) / artifact.name).exists()
    finally:
        root.close()
        anchor.close()


def test_normalize_closes_first_observation_when_second_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    record = store.reserve_capture(_CAPTURE_ID)
    staging = _capture_dir(project) / record.staging_name
    staging.write_bytes(b"staged")
    identity = staging.stat()
    store.mark_staged(
        store._authority_for(record),
        (identity.st_dev, identity.st_ino),
    )
    staged = store.get_record(_CAPTURE_ID)
    assert staged is not None
    real_observe = store._observe
    first_fd = -1
    calls = 0

    def fail_second_observation(*args, **kwargs):
        nonlocal calls, first_fd
        calls += 1
        if calls == 2:
            raise capture_lifecycle.CaptureLifecycleError("injected second observation failure")
        observed = real_observe(*args, **kwargs)
        assert observed is not None
        first_fd = observed.fd
        return observed

    try:
        monkeypatch.setattr(store, "_observe", fail_second_observation)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="second observation failure",
        ):
            store._normalize_abandoned(staged)

        assert first_fd >= 0
        with pytest.raises(OSError):
            os.fstat(first_fd)
    finally:
        root.close()
        anchor.close()


def test_quarantine_closes_first_observation_when_second_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    record = store.get_record(_CAPTURE_ID)
    assert record is not None
    deleting = store._deleting_record(record)
    real_observe = store._observe
    first_fd = -1
    calls = 0

    def fail_second_observation(*args, **kwargs):
        nonlocal calls, first_fd
        calls += 1
        if calls == 2:
            raise capture_lifecycle.CaptureLifecycleError("injected second observation failure")
        observed = real_observe(*args, **kwargs)
        assert observed is not None
        first_fd = observed.fd
        return observed

    try:
        monkeypatch.setattr(store, "_observe", fail_second_observation)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="second observation failure",
        ):
            store._quarantine_delete(deleting)

        assert first_fd >= 0
        with pytest.raises(OSError):
            os.fstat(first_fd)
    finally:
        root.close()
        anchor.close()


def test_writer_lease_is_visible_to_an_independent_process(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    script = (
        "import errno, fcntl, os, sys\n"
        "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
        "try:\n"
        "    try:\n"
        "        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except OSError as exc:\n"
        "        raise SystemExit(2 if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else 3)\n"
        "    raise SystemExit(0)\n"
        "finally:\n"
        "    os.close(fd)\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(_capture_dir(project) / artifact.name)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert completed.returncode == 2, completed.stderr
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_terminated_producer_is_recovered_by_independent_store(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    producer_script = (
        "import os, sys, time\n"
        "from autoskillit.hooks._capture._authority import "
        "open_capture_root, open_project_anchor\n"
        "from autoskillit.hooks._capture_artifacts import create_capture_artifact\n"
        "from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore\n"
        "anchor = open_project_anchor(sys.argv[1])\n"
        "root = open_capture_root(anchor, create=True)\n"
        "store = CaptureLifecycleStore.from_open_authorities(anchor, root)\n"
        "artifact = create_capture_artifact(root, sys.argv[2], store)\n"
        "os.write(artifact.fd, b'abandoned')\n"
        "os.fsync(artifact.fd)\n"
        "print(artifact.name, flush=True)\n"
        "time.sleep(30)\n"
    )
    producer = subprocess.Popen(
        [sys.executable, "-c", producer_script, str(project), _CAPTURE_ID],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project,
    )
    try:
        assert producer.stdout is not None
        artifact_name = producer.stdout.readline().strip()
        assert artifact_name == f"shell_{_CAPTURE_ID}.log"
        producer.terminate()
        producer.wait(timeout=5)
    finally:
        if producer.poll() is None:
            producer.kill()
            producer.wait(timeout=5)

    clock = _Clock(time.time() + 7200)
    anchor, root, store = _open_store(project, clock)
    try:
        artifact = _capture_dir(project) / artifact_name
        assert artifact.read_bytes() == b"abandoned"

        outcome = store.sweep(_sweep_budget(8, 1))

        assert outcome.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
        assert not artifact.exists()
    finally:
        root.close()
        anchor.close()


def test_finalized_capture_ttl_begins_at_terminal_commit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    try:
        clock.advance(3599)
        before = store.sweep(_sweep_budget(8, 1))
        assert before.deleted == 0
        assert (_capture_dir(project) / artifact.name).exists()

        clock.advance(2)
        after = store.sweep(_sweep_budget(8, 1))
        assert after.deleted == 1
        assert after.deleted_bytes == 8
        assert not (_capture_dir(project) / artifact.name).exists()
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
    finally:
        root.close()
        anchor.close()


def test_raw_integrity_finalization_api_is_absent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        assert not hasattr(store, "finalize_capture")
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize("mark_staged", (False, True))
def test_successful_finalize_requires_published_identity(
    tmp_path: Path,
    *,
    mark_staged: bool,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    fd = -1
    try:
        record = store.reserve_capture(_CAPTURE_ID)
        staging = _capture_dir(project) / record.staging_name
        fd = os.open(staging, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b"staged")
        value = os.fstat(fd)
        if mark_staged:
            staged = store.mark_staged(
                store._authority_for(record),
                (value.st_dev, value.st_ino),
            )
            record = staged

        snapshot = verify_capture_snapshot(
            fd=fd,
            capture_id=_CAPTURE_ID,
            incarnation=record.incarnation,
            project_identity=store._project_identity,
            root_identity=store._root_identity,
            carrier_name=f"shell_{_CAPTURE_ID}.log",
            carrier_identity=(value.st_dev, value.st_ino),
            measurement=CaptureMeasurement.from_bytes(
                b"staged",
                inline_bytes=6,
            ),
            command_outcome=CommandOutcome.exited(0),
            expected_revision=record.revision,
            finalized_at=clock.wall(),
            retention_deadline=clock.wall() + 3600.0,
        )

        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="does not match write authority",
        ):
            store.commit_verified_snapshot(snapshot, issue_reference=False)
    finally:
        if fd >= 0:
            os.close(fd)
        root.close()
        anchor.close()


def test_unlocked_abandoned_writer_is_recovered_and_deleted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    artifact.close_artifact_fd()
    artifact.release_lease()
    try:
        clock.advance(3601)
        outcome = store.sweep(_sweep_budget(8, 1))
        assert outcome.deleted == 1
        assert not (_capture_dir(project) / artifact.name).exists()
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
    finally:
        root.close()
        anchor.close()


def test_staging_normalization_retry_preserves_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    record = store.reserve_capture(_CAPTURE_ID)
    staging = _capture_dir(project) / record.staging_name
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        identity = os.fstat(fd)
        store.mark_staged(
            store._authority_for(record),
            (identity.st_dev, identity.st_ino),
        )
    finally:
        os.close(fd)

    real_link = os.link
    fail_once = True

    def interrupted_link(
        src: str,
        dst: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError("injected publication interruption")
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    try:
        clock.advance(3601)
        monkeypatch.setattr(capture_lifecycle.os, "link", interrupted_link)
        first = store.sweep(_sweep_budget(8, 1))

        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.STAGED
        assert retry.retry_count == 1
        assert staging.exists()

        clock.advance(3)
        second = store.sweep(_sweep_budget(8, 1))
        assert second.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
        assert not staging.exists()
    finally:
        root.close()
        anchor.close()


def test_quarantine_retry_reuses_committed_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    real_unlink = os.unlink
    fail_once = True

    def interrupted_unlink(path: str, *, dir_fd: int | None = None) -> None:
        nonlocal fail_once
        if fail_once and path.startswith(".capture-quarantine-"):
            fail_once = False
            raise OSError("injected quarantine interruption")
        real_unlink(path, dir_fd=dir_fd)

    try:
        monkeypatch.setattr(capture_lifecycle.os, "unlink", interrupted_unlink)
        first = store.sweep(_sweep_budget(8, 1))

        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.DELETING
        assert retry.retry_count == 1
        assert retry.quarantine_name
        assert (_capture_dir(project) / retry.quarantine_name).exists()
        assert not (_capture_dir(project) / artifact.name).exists()

        clock.advance(3)
        second = store.sweep(_sweep_budget(8, 1))
        assert second.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
        assert not (_capture_dir(project) / retry.quarantine_name).exists()
    finally:
        root.close()
        anchor.close()


def test_cleanup_outcome_counts_retries_per_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)

    def fail_delete(
        _record: CaptureLifecycleRecord,
        authorize_delete: Callable[[], None] | None = None,
        *,
        preleased: capture_lifecycle._ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> int:
        del preleased, lease_checked
        if authorize_delete is not None:
            authorize_delete()
        raise OSError("injected deletion failure")

    try:
        monkeypatch.setattr(store, "_quarantine_delete", fail_delete)
        first = store.sweep(_sweep_budget(8, 1))
        clock.advance(3)
        second = store.sweep(_sweep_budget(8, 1))

        record = store.get_record(_CAPTURE_ID)
        assert first.errors == first.retry_count == 1
        assert second.errors == second.retry_count == 1
        assert record is not None
        assert record.retry_count == 2
    finally:
        root.close()
        anchor.close()


def test_sweep_is_bounded_and_repeated_calls_make_progress(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        artifact_names = _seed_finalized_captures(root, store, count=5)

        clock.advance(3601)
        first = store.sweep(_sweep_budget(2, 1))
        assert first.examined == 2
        assert first.deleted == 2
        assert first.remaining_due == 3

        second = store.sweep(_sweep_budget(2, 1))
        assert second.deleted == 2
        assert second.remaining_due == 1

        third = store.sweep(_sweep_budget(2, 1))
        assert third.deleted == 1
        assert third.remaining_due == 0
        assert not any((_capture_dir(project) / name).exists() for name in artifact_names)
    finally:
        root.close()
        anchor.close()


def test_sweep_continues_after_failed_due_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    failed_id = f"{1:016x}"
    completed_id = f"{2:016x}"
    real_delete = store._quarantine_delete

    def fail_first(
        record: CaptureLifecycleRecord,
        authorize_delete: Callable[[], None] | None = None,
        *,
        preleased: capture_lifecycle._ObservedArtifact | None = None,
        lease_checked: bool = False,
    ) -> int:
        if record.capture_id == failed_id:
            if authorize_delete is not None:
                authorize_delete()
            raise OSError("injected first-row failure")
        return real_delete(
            record,
            authorize_delete,
            preleased=preleased,
            lease_checked=lease_checked,
        )

    try:
        artifact_names = _seed_finalized_captures(root, store, count=2)
        clock.advance(3601)
        monkeypatch.setattr(store, "_quarantine_delete", fail_first)

        outcome = store.sweep(_sweep_budget(2, 1))

        failed = store.get_record(failed_id)
        completed = store.get_record(completed_id)
        assert outcome.examined == 2
        assert outcome.errors == outcome.retry_count == 1
        assert outcome.deleted == 1
        assert failed is not None
        assert failed.state is CaptureState.DELETING
        assert failed.retry_count == 1
        assert completed is not None
        assert completed.state is CaptureState.DELETED
        assert (_capture_dir(project) / artifact_names[0]).exists()
        assert not (_capture_dir(project) / artifact_names[1]).exists()
    finally:
        root.close()
        anchor.close()


def test_sweep_defers_without_blocking_on_contended_ledger_lock(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    lock_path = _capture_dir(project) / capture_lifecycle.LOCK_NAME
    holder_script = (
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_RDWR)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('ready', flush=True)\n"
        "time.sleep(1.0)\n"
        "os.close(fd)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline() == "ready\n"

        started = time.monotonic()
        outcome = store.sweep(_sweep_budget(8, 0.05))
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert outcome.examined == 0
        assert outcome.remaining_due >= 1
        assert (_capture_dir(project) / artifact.name).exists()
    finally:
        try:
            holder.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder.communicate(timeout=3)
        root.close()
        anchor.close()


def test_sweep_is_bounded_by_elapsed_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    real_sweep_one = store._sweep_one

    def advancing_sweep(capture_id: str) -> tuple[str, int, int]:
        result = real_sweep_one(capture_id)
        clock.advance(0.6)
        return result

    try:
        _seed_finalized_captures(root, store, count=5)
        clock.advance(3601)
        monkeypatch.setattr(store, "_sweep_one", advancing_sweep)

        outcome = store.sweep(_sweep_budget(5, 1))

        assert outcome.examined == 2
        assert outcome.deleted == 2
        assert outcome.remaining_due == 3
        assert outcome.duration >= 1
    finally:
        root.close()
        anchor.close()


def test_sweep_reserves_one_attempt_after_discovery_consumes_elapsed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    real_due_keys = store._due_keys

    def slow_discovery(now: float, max_records: int):
        result = real_due_keys(now, max_records)
        clock.advance(0.051)
        return result

    budget = SweepBudgetSpec(
        max_records_inspected=8,
        max_replay_bytes=capture_lifecycle.MAX_LEDGER_BYTES,
        max_attempts=1,
        max_transitions=4,
        max_cursor_writes=1,
        max_duration_seconds=0.05,
    )
    try:
        _seed_finalized_captures(root, store, count=1)
        clock.advance(3601)
        monkeypatch.setattr(store, "_due_keys", slow_discovery)

        outcome = store.sweep(budget)

        assert outcome.examined == 1
        assert outcome.deleted == 1
        assert outcome.duration >= budget.max_duration_seconds
        assert outcome.records_inspected == 1
        assert outcome.blocker is CleanupBlocker.NONE
    finally:
        root.close()
        anchor.close()


def test_sweep_hard_attempt_budget_reports_typed_blocker(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    budget = SweepBudgetSpec(
        max_records_inspected=8,
        max_replay_bytes=capture_lifecycle.MAX_LEDGER_BYTES,
        max_attempts=1,
        max_transitions=4,
        max_cursor_writes=1,
        max_duration_seconds=1,
    )
    try:
        _seed_finalized_captures(root, store, count=3)
        clock.advance(3601)

        outcome = store.sweep(budget)

        assert outcome.examined == 1
        assert outcome.deleted == 1
        assert outcome.remaining_due == 2
        assert outcome.blocker is CleanupBlocker.ATTEMPT_BUDGET
        assert outcome.records_inspected <= budget.max_records_inspected
        assert outcome.replay_bytes <= budget.max_replay_bytes
        assert outcome.transitions <= budget.max_transitions
        assert outcome.cursor_writes <= budget.max_cursor_writes
    finally:
        root.close()
        anchor.close()


def test_sweep_record_budget_is_hard_and_content_free(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    budget = SweepBudgetSpec(
        max_records_inspected=2,
        max_replay_bytes=capture_lifecycle.MAX_LEDGER_BYTES,
        max_attempts=2,
        max_transitions=8,
        max_cursor_writes=2,
        max_duration_seconds=1,
    )
    try:
        _seed_finalized_captures(root, store, count=5)
        clock.advance(3601)

        outcome = store.sweep(budget)

        assert outcome.records_inspected == budget.max_records_inspected
        assert outcome.examined == 2
        assert outcome.blocker is CleanupBlocker.RECORD_BUDGET
        assert outcome.remaining_due >= 1
        serialized = json.dumps(asdict(outcome), sort_keys=True)
        assert _CAPTURE_ID not in serialized
        assert str(project) not in serialized
    finally:
        root.close()
        anchor.close()


def test_persisted_cursor_reaches_reclaimable_work_behind_live_prefix(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    live = [create_capture_artifact(root, f"{index:016x}", store) for index in (1, 2)]
    reclaimable = create_capture_artifact(root, f"{3:016x}", store)
    os.write(reclaimable.fd, b"reclaimable")
    _commit_verified(store, reclaimable, b"reclaimable", clock)
    reclaimable.close_artifact_fd()
    reclaimable.release_lease()
    clock.advance(7200)
    budget = _sweep_budget(2, 1)
    try:
        first = store.sweep(budget)
        reopened = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=clock.wall,
            monotonic=clock.monotonic,
        )
        second = reopened.sweep(budget)

        assert first.carrier_lease_live == 2
        assert first.deleted == 0
        assert second.deleted == 1
        assert all((_capture_dir(project) / artifact.name).exists() for artifact in live)
        assert not (_capture_dir(project) / reclaimable.name).exists()
        cursor = _capture_dir(project) / sweep_cursor.CURSOR_NAME
        assert stat.S_IMODE(cursor.stat().st_mode) == 0o600
    finally:
        for artifact in live:
            artifact.close_artifact_fd()
            artifact.release_lease()
        root.close()
        anchor.close()


def test_missing_cursor_is_rebuilt_from_future_ledger_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    cursor = _capture_dir(project) / sweep_cursor.CURSOR_NAME
    try:
        assert not cursor.exists()

        outcome = store.sweep(_sweep_budget(1, 1))

        assert outcome.examined == 0
        assert outcome.cursor_writes == 1
        assert outcome.progress is CleanupProgress.CURSOR_REPAIRED
        assert json.loads(cursor.read_text())["capture_id"] == _CAPTURE_ID
        assert stat.S_IMODE(cursor.stat().st_mode) == 0o600
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_content_invalid_cursor_is_removed_for_empty_ledger(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    cursor = _capture_dir(project) / sweep_cursor.CURSOR_NAME
    cursor.write_bytes(b"not canonical json\n")
    cursor.chmod(0o600)
    try:
        outcome = store.sweep(_sweep_budget(1, 1))

        assert outcome.examined == 0
        assert outcome.cursor_writes == 1
        assert outcome.progress is CleanupProgress.CURSOR_REPAIRED
        assert not cursor.exists()
    finally:
        root.close()
        anchor.close()


def test_sweep_cursor_symlink_is_fail_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    clock.advance(3601)
    capture_dir = _capture_dir(project)
    target = capture_dir / "cursor-target"
    target.write_text("untrusted")
    (capture_dir / sweep_cursor.CURSOR_NAME).symlink_to(target.name)
    try:
        with pytest.raises(sweep_cursor.CursorAuthorityError):
            store.sweep(_sweep_budget(1, 1))
        assert (_capture_dir(project) / artifact.name).exists()
    finally:
        root.close()
        anchor.close()


def test_cleanup_outcome_is_frozen_and_contains_no_identifiers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        outcome = store.sweep(_sweep_budget(1, 1))
        with pytest.raises(FrozenInstanceError):
            outcome.deleted = 10
        serialized = json.dumps(asdict(outcome), sort_keys=True)
        assert _CAPTURE_ID not in serialized
        assert str(project) not in serialized
    finally:
        root.close()
        anchor.close()


def test_reconcile_adapter_opens_existing_store_without_creation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, _store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
    artifact.release_lease()
    root.close()
    anchor.close()

    outcome = capture_reconcile.reconcile_capture_store(
        str(project),
        capture_reconcile.RUNNER_TAIL_BUDGET,
    )

    assert outcome.deleted == 1
    assert outcome.errors == 0
    assert outcome.remaining_due == 0


def test_incomplete_final_ledger_frame_is_recovered(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        store.reserve_capture(_CAPTURE_ID)
        ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
        valid_size = ledger.stat().st_size
        with ledger.open("ab") as stream:
            stream.write(capture_lifecycle.FRAME_MAGIC + b"\x00\x00")
        assert ledger.stat().st_size > valid_size
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
        assert ledger.stat().st_size == valid_size
    finally:
        root.close()
        anchor.close()


def test_active_record_bound_preserves_valid_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    try:
        monkeypatch.setattr(capture_lifecycle, "MAX_ACTIVE_RECORDS", 1)
        store.reserve_capture(_CAPTURE_ID)
        valid_ledger = ledger.read_bytes()

        with pytest.raises(CaptureLedgerError, match="active lifecycle record bound"):
            store.reserve_capture("1" * 16)

        assert ledger.read_bytes() == valid_ledger
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
        assert store.get_record("1" * 16) is None
    finally:
        root.close()
        anchor.close()


def test_terminal_evidence_uses_combined_not_operational_capacity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    store._capacity = replace(
        CaptureCapacitySpec(),
        max_operational_records=2,
        max_retained_records=2,
        max_forensic_records=2,
    )
    first = store.reserve_capture(_CAPTURE_ID)
    store._transition(
        store._authority_for(first),
        allowed_states={CaptureState.RESERVED},
        transform=lambda record: replace(
            record,
            state=CaptureState.TAMPERED,
            retention_phase=CaptureRetentionPhase.TAMPERED,
            revision=record.revision + 1,
        ),
    )
    try:
        admitted = store.reserve_capture("1" * 16)
        assert admitted.state is CaptureState.RESERVED
        with pytest.raises(CaptureCapacityError) as failure:
            store.reserve_capture("2" * 16)
        assert failure.value.reason is CaptureCapacityReason.FORENSIC_EVIDENCE
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
    finally:
        root.close()
        anchor.close()


def test_retention_occupancy_has_distinct_capacity_reason(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    store._capacity = replace(
        CaptureCapacitySpec(),
        max_operational_records=2,
        max_retained_records=1,
        max_forensic_records=3,
    )
    try:
        store.reserve_capture(_CAPTURE_ID)
        with pytest.raises(CaptureCapacityError) as failure:
            store.reserve_capture("1" * 16)
        assert failure.value.reason is CaptureCapacityReason.RETENTION_CAPACITY
    finally:
        root.close()
        anchor.close()


def test_projected_compacted_bytes_preserve_recovery_headroom(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    try:
        store.reserve_capture(_CAPTURE_ID)
        valid = ledger.read_bytes()
        hard_bound = len(valid) * 2
        store._capacity = CaptureCapacitySpec(
            max_operational_records=8,
            max_retained_records=8,
            max_forensic_records=8,
            max_tombstones=2,
            compaction_low_bytes=hard_bound // 3,
            compaction_high_bytes=hard_bound // 2,
            hard_ledger_bytes=hard_bound,
            cursor_headroom_bytes=32,
            tamper_headroom_bytes=32,
            reclamation_headroom_bytes=32,
        )
        with pytest.raises(CaptureCapacityError) as failure:
            store.reserve_capture("1" * 16)
        assert failure.value.reason is CaptureCapacityReason.HARD_LEDGER_CAPACITY
        assert ledger.read_bytes() == valid
    finally:
        root.close()
        anchor.close()


def test_ledger_size_bound_rejects_without_mutating_valid_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    try:
        store.reserve_capture(_CAPTURE_ID)
        valid_ledger = ledger.read_bytes()

        with monkeypatch.context() as bound:
            bound.setattr(capture_lifecycle, "MAX_LEDGER_BYTES", len(valid_ledger) - 1)
            with pytest.raises(CaptureLedgerError, match="ledger exceeds bound"):
                store.get_record(_CAPTURE_ID)

        assert ledger.read_bytes() == valid_ledger
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
    finally:
        root.close()
        anchor.close()


def test_compaction_size_bound_preserves_valid_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
    try:
        store.reserve_capture(_CAPTURE_ID)
        valid_ledger = ledger.read_bytes()

        with monkeypatch.context() as bound:
            bound.setattr(capture_lifecycle, "_COMPACTION_THRESHOLD_BYTES", 0)
            bound.setattr(capture_lifecycle, "_MAX_COMPACTION_BYTES", 1)
            with pytest.raises(CaptureLedgerError, match="compaction exceeds bound"):
                store.reserve_capture("1" * 16)

        assert ledger.read_bytes() == valid_ledger
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
        assert store.get_record("1" * 16) is None
        assert not list(_capture_dir(project).glob(".capture-lifecycle-compact-*"))
    finally:
        root.close()
        anchor.close()


def test_compaction_replace_failure_removes_temporary_control_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        store.reserve_capture(_CAPTURE_ID)
        monkeypatch.setattr(capture_lifecycle, "_COMPACTION_THRESHOLD_BYTES", 1)

        def fail_replace(
            _src: str,
            _dst: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
        ) -> None:
            del src_dir_fd, dst_dir_fd
            raise OSError("injected replacement failure")

        monkeypatch.setattr(capture_lifecycle.os, "replace", fail_replace)
        with pytest.raises(OSError, match="replacement failure"):
            store.reserve_capture("1" * 16)

        assert not list(_capture_dir(project).glob(".capture-lifecycle-compact-*"))
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize("force_compaction", [False, True])
def test_ledger_writes_retry_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    force_compaction: bool,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    real_write = os.write
    write_calls = 0

    def short_write(fd: int, payload: bytes | memoryview) -> int:
        nonlocal write_calls
        write_calls += 1
        limit = max(1, len(payload) // 2)
        return real_write(fd, payload[:limit])

    try:
        if force_compaction:
            monkeypatch.setattr(capture_lifecycle, "_COMPACTION_THRESHOLD_BYTES", 1)
        monkeypatch.setattr(capture_lifecycle.os, "write", short_write)
        store.reserve_capture(_CAPTURE_ID)

        record = store.get_record(_CAPTURE_ID)
        assert record is not None
        assert record.state is CaptureState.RESERVED
        assert write_calls > 1
    finally:
        root.close()
        anchor.close()


def test_zero_byte_ledger_write_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        monkeypatch.setattr(capture_lifecycle.os, "write", lambda _fd, _payload: 0)
        with pytest.raises(CaptureLedgerError, match="write made no progress"):
            store.reserve_capture(_CAPTURE_ID)
    finally:
        root.close()
        anchor.close()


def test_bad_ledger_checksum_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        store.reserve_capture(_CAPTURE_ID)
        ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
        payload = bytearray(ledger.read_bytes())
        payload[-1] ^= 0x01
        ledger.write_bytes(payload)
        with pytest.raises(CaptureLedgerError, match="checksum"):
            store.get_record(_CAPTURE_ID)
    finally:
        root.close()
        anchor.close()


def test_ledger_decoder_rejects_strict_json_and_version_violations() -> None:
    nested = b"[" * 20 + b"0" + b"]" * 20
    cases = (
        (
            b'{"compaction_epoch":1,"format_version":99,"record":{}}',
            "metadata",
        ),
        (
            b'{"compaction_epoch":1, "format_version":2,"record":{}}',
            "noncanonical",
        ),
        (
            b'{"compaction_epoch":1,"format_version":2,"format_version":2,"record":{}}',
            "duplicate",
        ),
        (
            b'{"compaction_epoch":1,"format_version":2,"record":{"value":NaN}}',
            "payload",
        ),
        (
            b'{"compaction_epoch":1,"extra":0,"format_version":2,"record":{}}',
            "schema",
        ),
        (
            b'{"compaction_epoch":1,"format_version":2,"record":' + nested + b"}",
            "structural bound",
        ),
    )
    for payload, message in cases:
        with pytest.raises(
            capture_lifecycle._capture_ledger.LedgerCodecError,
            match=message,
        ):
            capture_lifecycle._capture_ledger.decode_ledger(_frame_from_payload(payload))


def test_invalid_complete_middle_frame_is_not_treated_as_a_crash_tail() -> None:
    record = CaptureLifecycleRecord(
        capture_id=_CAPTURE_ID,
        state=CaptureState.RESERVED,
        staging_name=f".capture-staging-{_CAPTURE_ID}-{'1' * 16}",
        public_name=f"shell_{_CAPTURE_ID}.log",
        project_identity=(1, 2),
        root_identity=(3, 4),
        created_at=1.0,
        next_attempt_at=2.0,
        incarnation="1" * 32,
        revision=1,
    )
    valid = capture_lifecycle._capture_ledger.encode_frame(
        capture_lifecycle._record_to_dict(record),
        compaction_epoch=1,
    )
    invalid = bytearray(valid)
    invalid[-1] ^= 0x01

    with pytest.raises(
        capture_lifecycle._capture_ledger.LedgerCodecError,
        match="checksum",
    ):
        capture_lifecycle._capture_ledger.decode_ledger(valid + bytes(invalid) + valid)


def test_ledger_reload_rejects_revision_gap(tmp_path: Path) -> None:
    project = tmp_path / "project"
    anchor, root, store = _open_store(project, _Clock())
    try:
        record = store.reserve_capture(_CAPTURE_ID)
        forged = replace(record, revision=record.revision + 2)
        frame = capture_lifecycle._capture_ledger.encode_frame(
            capture_lifecycle._record_to_dict(forged),
            compaction_epoch=record.compaction_epoch,
        )
        ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
        with ledger.open("ab") as stream:
            stream.write(frame)

        with pytest.raises(CaptureLedgerError, match="invalid lifecycle successor"):
            store.get_record(_CAPTURE_ID)
    finally:
        root.close()
        anchor.close()


def test_ledger_reload_rejects_conflicting_final_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    try:
        record = store.get_record(_CAPTURE_ID)
        assert record is not None and record.manifest is not None
        primitive = json.loads(record.manifest_bytes)
        primitive["sha256"] = "f" * 64
        conflicting_bytes = capture_lifecycle._capture_ledger.canonical_json(primitive)
        wire = capture_lifecycle._capture_snapshot.decode_capture_manifest_wire(conflicting_bytes)
        conflicting = capture_lifecycle._capture_snapshot._restore_capture_final_manifest(wire)
        forged = replace(
            record,
            revision=record.revision + 1,
            manifest=conflicting,
            manifest_bytes=conflicting_bytes,
        )
        frame = capture_lifecycle._capture_ledger.encode_frame(
            capture_lifecycle._record_to_dict(forged),
            compaction_epoch=record.compaction_epoch,
        )
        ledger = _capture_dir(project) / capture_lifecycle.LEDGER_NAME
        with ledger.open("ab") as stream:
            stream.write(frame)

        with pytest.raises(CaptureLedgerError, match="immutable FINAL authority"):
            store.get_record(_CAPTURE_ID)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()
