"""Verified reference reading and carrier-lease handoff tests."""

from __future__ import annotations

import copy
import errno
import fcntl
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import autoskillit.hooks._capture._cleanup as capture_cleanup
import autoskillit.hooks._capture._descriptor as capture_descriptor
import autoskillit.hooks._capture._reader as capture_reader
import autoskillit.hooks._capture_artifacts as capture_artifacts
import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture._reader import (
    MAX_VERIFIED_READ_BYTES,
    VerifiedCaptureReader,
)
from autoskillit.hooks._capture._snapshot import (
    CaptureAuthorityError,
    CaptureFinalManifest,
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    CaptureSetupError,
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import (
    CaptureDeliveryStatus,
    CaptureLifecycleError,
    CaptureLifecycleStore,
    CaptureReferenceStatus,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"


class _Clock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def wall(self) -> float:
        return self.value

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _capture_dir(project: Path) -> Path:
    return project.joinpath(*CAPTURE_PATH_COMPONENTS)


def _open_store(project: Path, clock: _Clock):
    project.mkdir(exist_ok=True)
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    store = CaptureLifecycleStore.from_open_authorities(
        anchor,
        root,
        wall_clock=clock.wall,
        monotonic=clock.monotonic,
    )
    return anchor, root, store


def _finalize(
    project: Path,
    clock: _Clock,
    data: bytes,
    *,
    issue_reference: bool = True,
):
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    os.write(artifact.fd, data)
    verified = verify_capture_snapshot(
        fd=artifact.fd,
        capture_id=artifact.authority.capture_id,
        incarnation=artifact.authority.incarnation,
        project_identity=(anchor.identity.device, anchor.identity.inode),
        root_identity=(root.identity.device, root.identity.inode),
        carrier_name=artifact.name,
        carrier_identity=(artifact.identity.device, artifact.identity.inode),
        measurement=CaptureMeasurement.from_bytes(
            data,
            inline_bytes=max(1, min(len(data), 32)),
        ),
        command_outcome=CommandOutcome.exited(0),
        expected_revision=artifact.authority.expected_revision,
        finalized_at=clock.wall(),
        retention_deadline=clock.wall() + 3600.0,
    )
    finalized = store.commit_verified_snapshot(
        verified,
        issue_reference=issue_reference,
    )
    return anchor, root, store, artifact, finalized


def _publish(project: Path, clock: _Clock, data: bytes):
    anchor, root, store, artifact, finalized = _finalize(project, clock, data)
    published = store.publish_reference(finalized)
    return anchor, root, store, artifact, finalized, published


def _close_artifact(artifact) -> None:
    artifact.close_artifact_fd()
    artifact.release_lease()


def test_published_reference_returns_self_contained_bounded_reader(
    tmp_path: Path,
) -> None:
    data = b"0123456789abcdefghijklmnopqrstuvwxyz"
    clock = _Clock()
    anchor, root, store, artifact, finalized, published = _publish(
        tmp_path / "project",
        clock,
        data,
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    root.close()
    anchor.close()
    try:
        assert reader.read(0, 5) == data[:5]
        assert reader.read(7, 11) == data[7:18]
        assert reader.read(len(data) - 2, 10) == data[-2:]
        assert not hasattr(reader, "fileno")
        assert not hasattr(reader, "path")
        for offset, length in (
            (True, 1),
            (-1, 1),
            (0, True),
            (0, -1),
            (0, MAX_VERIFIED_READ_BYTES + 1),
            (2**63, 1),
        ):
            with pytest.raises(CaptureAuthorityError):
                reader.read(offset, length)
    finally:
        reader.close()
    with pytest.raises(CaptureAuthorityError, match="closed"):
        reader.read(0, 1)


def test_verified_reader_is_factory_only_and_nontransferable(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project",
        clock,
        b"factory",
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    try:
        with pytest.raises(CaptureAuthorityError, match="factory-created"):
            VerifiedCaptureReader(
                manifest=reader.manifest,
                revision=reader.revision,
                _descriptor=0,
            )
        for operation in (
            lambda: copy.copy(reader),
            lambda: copy.deepcopy(reader),
            lambda: pickle.dumps(reader),
        ):
            with pytest.raises(CaptureAuthorityError):
                operation()
        assert "_descriptor" not in repr(reader)
    finally:
        reader.close()
        root.close()
        anchor.close()


def test_reader_context_preserves_body_error_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project",
        _Clock(),
        b"context",
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    root.close()
    anchor.close()
    reader_fd = reader._descriptor
    real_close = os.close

    def close_then_report_failure(fd: int) -> None:
        real_close(fd)
        if fd == reader_fd:
            raise OSError("injected reader cleanup failure")

    monkeypatch.setattr(capture_cleanup.os, "close", close_then_report_failure)
    with pytest.raises(RuntimeError, match="primary body failure") as captured:
        with reader:
            raise RuntimeError("primary body failure")

    assert any(
        "verified capture reader cleanup also failed" in note for note in captured.value.__notes__
    )


def test_verified_reader_loops_over_short_preads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"short pread loop"
    clock = _Clock()
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project",
        clock,
        data,
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    real_pread = capture_reader.os.pread

    def short_pread(fd: int, length: int, offset: int) -> bytes:
        return real_pread(fd, min(length, 2), offset)

    try:
        monkeypatch.setattr(capture_reader.os, "pread", short_pread)
        assert reader.read(1, 10) == data[1:11]
        assert reader.read(len(data), 10) == b""
    finally:
        reader.close()
        root.close()
        anchor.close()


@pytest.mark.parametrize(
    "invalid_reference",
    (
        f"shell_{_CAPTURE_ID}.log",
        "0" * 64,
        "ascr2:not-a-capture",
        "",
    ),
)
def test_reference_resolver_rejects_non_capabilities(
    tmp_path: Path,
    invalid_reference: str,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact, _finalized, _published = _publish(
        tmp_path / "project",
        clock,
        b"authority",
    )
    _close_artifact(artifact)
    try:
        with pytest.raises((CaptureAuthorityError, CaptureLifecycleError)):
            store.open_verified_capture(invalid_reference)
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize("field", ("incarnation", "secret"))
def test_reference_resolver_authenticates_the_complete_token(
    tmp_path: Path,
    field: str,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project",
        clock,
        b"authenticated",
    )
    _close_artifact(artifact)
    version, capture_id, incarnation, secret = published.token.split(":")
    if field == "incarnation":
        incarnation = ("0" if incarnation[0] != "0" else "1") + incarnation[1:]
    else:
        secret = ("0" if secret[0] != "0" else "1") + secret[1:]
    forged = ":".join((version, capture_id, incarnation, secret))
    try:
        with pytest.raises(CaptureLifecycleError):
            store.open_verified_capture(forged)
    finally:
        root.close()
        anchor.close()


def test_reference_resolver_rejects_nonfinal_record(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store = _open_store(tmp_path / "project", clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    forged = f"ascr2:{_CAPTURE_ID}:{artifact.authority.incarnation}:{'0' * 64}"
    try:
        with pytest.raises(CaptureLifecycleError, match="not finalized"):
            store.open_verified_capture(forged)
        with pytest.raises(CaptureLifecycleError, match="unavailable"):
            store.open_verified_capture(f"ascr2:{'f' * 16}:{'0' * 32}:{'0' * 64}")
    finally:
        _close_artifact(artifact)
        root.close()
        anchor.close()


def test_expired_reference_is_rejected_before_cleanup(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project",
        clock,
        b"expired",
    )
    _close_artifact(artifact)
    clock.advance(1801)
    try:
        with pytest.raises(CaptureLifecycleError, match="reference"):
            store.open_verified_capture(published.token)
        assert (_capture_dir(tmp_path / "project") / artifact.name).exists()
    finally:
        root.close()
        anchor.close()


def test_reference_is_bound_to_physical_project(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store, artifact, _finalized, published = _publish(
        tmp_path / "project-a",
        clock,
        b"bound",
    )
    _close_artifact(artifact)
    other_anchor, other_root, other_store = _open_store(tmp_path / "project-b", clock)
    try:
        with pytest.raises(CaptureLifecycleError):
            other_store.open_verified_capture(published.token)
    finally:
        other_root.close()
        other_anchor.close()
        root.close()
        anchor.close()


def test_cleanup_defers_while_verified_reader_holds_shared_lease(
    tmp_path: Path,
) -> None:
    data = b"lease protected"
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, _finalized, published = _publish(
        project,
        clock,
        data,
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    try:
        clock.advance(3601)
        first = store.sweep(max_items=8, max_duration_seconds=1)
        assert first.carrier_lease_live == 1
        deferred = store.get_record(_CAPTURE_ID)
        assert deferred is not None
        assert deferred.state.value == "FINALIZED"
        assert deferred.retention_phase.value == "active"
        assert deferred.reference_status is CaptureReferenceStatus.EXPIRED
        assert reader.read(0, len(data)) == data
        assert (_capture_dir(project) / f"shell_{_CAPTURE_ID}.log").exists()
    finally:
        reader.close()
    clock.advance(31)
    second = store.sweep(max_items=8, max_duration_seconds=1)
    assert second.deleted == 1
    assert not (_capture_dir(project) / f"shell_{_CAPTURE_ID}.log").exists()
    root.close()
    anchor.close()


def test_mutation_before_resolution_fails_closed(tmp_path: Path) -> None:
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, _finalized, published = _publish(
        project,
        clock,
        b"immutable",
    )
    _close_artifact(artifact)
    carrier = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    carrier.write_bytes(b"MUTATED!!")
    carrier.chmod(0o600)
    try:
        with pytest.raises(CaptureAuthorityError, match="content changed"):
            store.open_verified_capture(published.token)
    finally:
        root.close()
        anchor.close()


def test_fifo_substitution_is_rejected_without_blocking(tmp_path: Path) -> None:
    project = tmp_path / "project"
    anchor, root, _store, artifact, _finalized, published = _publish(
        project,
        _Clock(time.time()),
        b"fifo",
    )
    _close_artifact(artifact)
    carrier = _capture_dir(project) / artifact.name
    carrier.unlink()
    os.mkfifo(carrier, 0o600)
    code = (
        "import sys;"
        "from autoskillit.hooks._capture_artifacts import open_capture_lifecycle;"
        "from autoskillit.hooks._capture._snapshot import CaptureAuthorityError;"
        "\ntry:\n"
        "  with open_capture_lifecycle(sys.argv[1],create=False) as lifecycle:\n"
        "    lifecycle.open_verified_capture(sys.argv[2])\n"
        "except CaptureAuthorityError as exc:\n"
        "  print(f'{type(exc).__name__}:{exc}',file=sys.stderr)\n"
        "  raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code, str(project), published.token],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stderr == ("CaptureAuthorityError:capture carrier metadata changed\n")
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize("unsafe_metadata", ("link_count", "mode", "owner"))
def test_resolver_rejects_unsafe_carrier_metadata(
    unsafe_metadata: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    anchor, root, store, artifact, _finalized, published = _publish(
        project,
        _Clock(),
        b"unsafe",
    )
    _close_artifact(artifact)
    carrier = _capture_dir(project) / artifact.name
    extra_link = carrier.with_suffix(".link")
    if unsafe_metadata == "link_count":
        os.link(carrier, extra_link)
    elif unsafe_metadata == "mode":
        carrier.chmod(0o640)
    else:
        real_fstat = os.fstat
        carrier_value = carrier.stat()
        carrier_identity = (carrier_value.st_dev, carrier_value.st_ino)

        def report_wrong_owner(fd: int) -> os.stat_result:
            value = real_fstat(fd)
            if (value.st_dev, value.st_ino) == carrier_identity:
                fields = list(value)
                fields[4] = value.st_uid + 1
                return os.stat_result(fields)
            return value

        monkeypatch.setattr(capture_descriptor.os, "fstat", report_wrong_owner)
    try:
        with pytest.raises(CaptureAuthorityError, match="metadata changed"):
            store.open_verified_capture(published.token)
    finally:
        root.close()
        anchor.close()


def test_reference_transition_during_verification_rejects_stale_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact, finalized, published = _publish(
        tmp_path / "project",
        clock,
        b"linearized",
    )
    _close_artifact(artifact)
    resolver_snapshot = capture_lifecycle._capture_resolver._snapshot
    real_verify = resolver_snapshot.verify_capture_descriptor

    def verify_then_revoke(fd: int, manifest: CaptureFinalManifest) -> None:
        real_verify(fd, manifest)
        store.revoke_reference(finalized)

    monkeypatch.setattr(
        resolver_snapshot,
        "verify_capture_descriptor",
        verify_then_revoke,
    )
    try:
        with pytest.raises(CaptureLifecycleError, match="reference"):
            store.open_verified_capture(published.token)
        current = store.get_record(_CAPTURE_ID)
        assert current is not None
        assert current.reference_status is CaptureReferenceStatus.REVOKED
        contender = os.open(
            _capture_dir(tmp_path / "project") / artifact.name,
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(contender)
    finally:
        root.close()
        anchor.close()


def test_path_rebinding_after_open_cannot_switch_reader(tmp_path: Path) -> None:
    data = b"original inode"
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, _finalized, published = _publish(
        project,
        clock,
        data,
    )
    _close_artifact(artifact)
    reader = store.open_verified_capture(published.token)
    carrier = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    displaced = carrier.with_suffix(".old")
    carrier.rename(displaced)
    carrier.write_bytes(b"replacement")
    carrier.chmod(0o600)
    try:
        assert reader.read(0, len(data)) == data
    finally:
        reader.close()
        root.close()
        anchor.close()


def test_producer_transfer_retains_exclusive_lease_until_reader_close(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, finalized = _finalize(
        project,
        clock,
        b"producer",
    )
    reader = artifact.transfer_to_reader(store, finalized)
    assert artifact.fd == artifact.lease_fd == artifact.drain_writer_fd == -1
    published = store.publish_reference(finalized)
    contender = os.open(
        _capture_dir(project) / artifact.name,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        for operation in (fcntl.LOCK_SH, fcntl.LOCK_EX):
            with pytest.raises(OSError) as raised:
                fcntl.flock(contender, operation | fcntl.LOCK_NB)
            assert raised.value.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
        assert reader.read(0, 8) == b"producer"
    finally:
        reader.close()
    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(contender)
    with store.open_verified_capture(published.token) as resolved:
        assert resolved.read(0, 8) == b"producer"
    root.close()
    anchor.close()


def test_queued_exclusive_waiter_stays_blocked_through_delivery_commit(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, finalized = _finalize(
        project,
        clock,
        b"producer",
    )
    waiter_fd = os.open(
        _capture_dir(project) / artifact.name,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    waiter_started = threading.Event()
    waiter_acquired = threading.Event()
    release_waiter = threading.Event()
    reader = None

    def wait_for_exclusive_lease() -> None:
        try:
            waiter_started.set()
            fcntl.flock(waiter_fd, fcntl.LOCK_EX)
            waiter_acquired.set()
            release_waiter.wait(timeout=5)
        finally:
            os.close(waiter_fd)

    waiter = threading.Thread(target=wait_for_exclusive_lease)
    waiter.start()
    try:
        assert waiter_started.wait(timeout=5)
        assert not waiter_acquired.wait(timeout=0.05)
        reader = artifact.transfer_to_reader(store, finalized)
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
        assert not waiter_acquired.wait(timeout=0.05)
        reader.close()
        reader = None
        assert waiter_acquired.wait(timeout=5)
    finally:
        if reader is not None:
            reader.close()
        artifact.close_artifact_fd()
        artifact.release_lease()
        release_waiter.set()
        waiter.join(timeout=5)
        root.close()
        anchor.close()
    assert not waiter.is_alive()


def test_store_reopen_does_not_normalize_live_producer_reference(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, _store, artifact, finalized = _finalize(
        tmp_path / "project",
        clock,
        b"producer",
    )
    try:
        reopened = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=clock.wall,
            monotonic=clock.monotonic,
        )
        current = reopened.get_record(_CAPTURE_ID)

        assert finalized.issuance is not None
        assert current is not None
        assert current.reference_status is CaptureReferenceStatus.ISSUED
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_producer_transfer_accepts_not_requested_reference_state(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        clock,
        b"inline",
        issue_reference=False,
    )
    try:
        with artifact.transfer_to_reader(store, finalized) as reader:
            assert reader.read(0, 6) == b"inline"
    finally:
        root.close()
        anchor.close()


def test_transfer_requires_artifact_owned_drain_writer_to_be_closed(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        clock,
        b"ownership",
    )
    capture_artifacts._duplicate_artifact_writer(artifact)
    try:
        with pytest.raises(CaptureSetupError, match="drain writer"):
            artifact.transfer_to_reader(store, finalized)
        artifact.close_drain_writer()
        with artifact.transfer_to_reader(store, finalized) as reader:
            assert reader.read(0, 9) == b"ownership"
    finally:
        artifact.close_drain_writer()
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_transfer_invalidates_descriptors_before_ambiguous_lease_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        _Clock(),
        b"ownership",
    )
    carrier_fd = artifact.fd
    lease_fd = artifact.lease_fd
    real_close = os.close
    injected = False

    def close_then_report_failure(fd: int) -> None:
        nonlocal injected
        if fd == lease_fd and not injected:
            injected = True
            real_close(fd)
            raise OSError("injected ambiguous lease close")
        real_close(fd)

    monkeypatch.setattr(capture_artifacts.os, "close", close_then_report_failure)
    try:
        with pytest.raises(CaptureSetupError, match="transfer capture carrier lease"):
            artifact.transfer_to_reader(store, finalized)

        assert artifact.fd == artifact.lease_fd == -1
        with pytest.raises(OSError):
            os.fstat(carrier_fd)
        with pytest.raises(OSError):
            os.fstat(lease_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_transfer_adoption_failure_closes_all_transferred_descriptors(
    tmp_path: Path,
) -> None:
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        _Clock(),
        b"ownership",
    )
    carrier_fd = artifact.fd
    lease_fd = artifact.lease_fd
    os.pwrite(carrier_fd, b"MUTATION!", 0)
    os.fsync(carrier_fd)
    try:
        with pytest.raises(CaptureAuthorityError, match="content changed"):
            artifact.transfer_to_reader(store, finalized)

        assert artifact.fd == artifact.lease_fd == -1
        with pytest.raises(OSError):
            os.fstat(carrier_fd)
        with pytest.raises(OSError):
            os.fstat(lease_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_transfer_preserves_authority_error_when_carrier_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        _Clock(),
        b"ownership",
    )
    carrier_fd = artifact.fd
    os.pwrite(carrier_fd, b"MUTATION!", 0)
    os.fsync(carrier_fd)
    real_close = os.close
    injected = False

    def close_then_report_failure(fd: int) -> None:
        nonlocal injected
        real_close(fd)
        if fd == carrier_fd and not injected:
            injected = True
            raise OSError("injected carrier cleanup failure")

    monkeypatch.setattr(capture_cleanup.os, "close", close_then_report_failure)
    try:
        with pytest.raises(CaptureAuthorityError, match="content changed") as captured:
            artifact.transfer_to_reader(store, finalized)

        assert injected
        assert any(
            "capture carrier cleanup also failed" in note for note in captured.value.__notes__
        )
        with pytest.raises(OSError):
            os.fstat(carrier_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda fd: os.pwrite(fd, b"MUTATION", 0), "content changed"),
        (lambda fd: os.write(fd, b"!"), "metadata changed"),
        (lambda fd: os.ftruncate(fd, 1), "metadata changed"),
    ),
)
def test_post_transfer_mutation_is_rejected_by_later_resolver(
    mutation,
    message: str,
    tmp_path: Path,
) -> None:
    clock = _Clock()
    project = tmp_path / "project"
    anchor, root, store, artifact, finalized = _finalize(
        project,
        clock,
        b"original",
    )
    with artifact.transfer_to_reader(store, finalized):
        published = store.publish_reference(finalized)
    contender = os.open(
        _capture_dir(project) / artifact.name,
        os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.lseek(contender, 0, os.SEEK_END)
        mutation(contender)
        os.fsync(contender)
    finally:
        os.close(contender)
    try:
        with pytest.raises(CaptureAuthorityError, match=message):
            store.open_verified_capture(published.token)
    finally:
        root.close()
        anchor.close()


def test_unavailable_reference_cannot_be_resolved(tmp_path: Path) -> None:
    clock = _Clock()
    anchor, root, store, artifact, finalized = _finalize(
        tmp_path / "project",
        clock,
        b"unavailable",
    )
    assert finalized.issuance is not None
    token = finalized.issuance.token
    unavailable = store.mark_reference_unavailable(
        finalized,
        reason_code="PUBLICATION_FAILED",
    )
    _close_artifact(artifact)
    try:
        assert unavailable.reason_code == "PUBLICATION_FAILED"
        assert store.get_record(_CAPTURE_ID).reference_status is CaptureReferenceStatus.UNAVAILABLE
        with pytest.raises(CaptureLifecycleError):
            store.open_verified_capture(token)
    finally:
        root.close()
        anchor.close()
