"""Durable lifecycle, writer-liveness, and reclamation tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

import autoskillit.hooks._capture_lifecycle as capture_lifecycle
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    CaptureRoot,
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import (
    CaptureLedgerError,
    CaptureLifecycleStore,
    CaptureState,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"
_DIGEST = "0" * 64


class _Clock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def wall(self) -> float:
        return self.value

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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


def _finalized_capture(project: Path, clock: _Clock, capture_id: str = _CAPTURE_ID):
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, capture_id, store)
    os.write(artifact.fd, b"captured")
    os.fsync(artifact.fd)
    store.finalize_capture(capture_id, size=8, sha256=_DIGEST, failed=False)
    return anchor, root, store, artifact


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
        store.finalize_capture(capture_id, size=index + 1, sha256=_DIGEST, failed=False)
        names.append(artifact.name)
        artifact.close()
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
        artifact.close()
        artifact.release_lease()
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
        outcome = store.sweep(max_items=8, max_duration_seconds=1)
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
        outcome = store.sweep(max_items=8, max_duration_seconds=1)
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
        outcome = store.sweep(max_items=8, max_duration_seconds=1)
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
    artifact.close()
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
        first = store.sweep(max_items=8, max_duration_seconds=1)
        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.DELETING
        assert retry.retry_count == 1

        public = _capture_dir(project) / retry.public_name
        quarantine = _capture_dir(project) / retry.quarantine_name
        public.write_bytes(b"replacement")
        assert quarantine.exists()

        clock.advance(3)
        second = store.sweep(max_items=8, max_duration_seconds=1)
        assert second.tampered == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
        assert public.read_bytes() == b"replacement"
        assert quarantine.exists()
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
        outcome = store.sweep(max_items=8, max_duration_seconds=1)
        assert outcome.writer_live == 1
        assert outcome.deleted == 0
        assert (_capture_dir(project) / artifact.name).exists()
    finally:
        artifact.close()
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

    def writer_live(observed: capture_lifecycle._ObservedArtifact) -> None:
        nonlocal observed_fd
        observed_fd = observed.fd
        raise capture_lifecycle._WriterLive

    try:
        clock.advance(7200)
        monkeypatch.setattr(
            CaptureLifecycleStore,
            "_try_artifact_lease",
            staticmethod(writer_live),
        )
        outcome = store.sweep(max_items=8, max_duration_seconds=1)

        assert outcome.writer_live == 1
        assert observed_fd >= 0
        with pytest.raises(OSError):
            os.fstat(observed_fd)
    finally:
        artifact.close()
        artifact.release_lease()
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
        artifact.close()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_finalized_capture_ttl_begins_at_terminal_commit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close()
    artifact.release_lease()
    try:
        clock.advance(3599)
        before = store.sweep(max_items=8, max_duration_seconds=1)
        assert before.deleted == 0
        assert (_capture_dir(project) / artifact.name).exists()

        clock.advance(2)
        after = store.sweep(max_items=8, max_duration_seconds=1)
        assert after.deleted == 1
        assert after.deleted_bytes == 8
        assert not (_capture_dir(project) / artifact.name).exists()
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
    finally:
        root.close()
        anchor.close()


@pytest.mark.parametrize(
    ("size", "sha256", "failed"),
    [
        (-1, _DIGEST, False),
        (True, _DIGEST, False),
        (1.5, _DIGEST, False),
        (0, "", False),
        (0, "0" * 63, False),
        (0, "G" * 64, False),
        (0, "invalid", True),
    ],
)
def test_finalize_rejects_invalid_integrity_metadata(
    tmp_path: Path,
    size: int,
    sha256: str,
    *,
    failed: bool,
) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        store.reserve_capture(_CAPTURE_ID)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="invalid terminal capture",
        ):
            store.finalize_capture(
                _CAPTURE_ID,
                size=size,
                sha256=sha256,
                failed=failed,
            )
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
    finally:
        root.close()
        anchor.close()


def test_unlocked_abandoned_writer_is_recovered_and_deleted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifact = create_capture_artifact(root, _CAPTURE_ID, store)
    artifact.close()
    artifact.release_lease()
    try:
        clock.advance(3601)
        outcome = store.sweep(max_items=8, max_duration_seconds=1)
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
        store.mark_staged(_CAPTURE_ID, (identity.st_dev, identity.st_ino))
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
        first = store.sweep(max_items=8, max_duration_seconds=1)

        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.STAGED
        assert retry.retry_count == 1
        assert staging.exists()

        clock.advance(3)
        second = store.sweep(max_items=8, max_duration_seconds=1)
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
    artifact.close()
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
        first = store.sweep(max_items=8, max_duration_seconds=1)

        retry = store.get_record(_CAPTURE_ID)
        assert first.errors == 1
        assert retry is not None
        assert retry.state is CaptureState.DELETING
        assert retry.retry_count == 1
        assert retry.quarantine_name
        assert (_capture_dir(project) / retry.quarantine_name).exists()
        assert not (_capture_dir(project) / artifact.name).exists()

        clock.advance(3)
        second = store.sweep(max_items=8, max_duration_seconds=1)
        assert second.deleted == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.DELETED
        assert not (_capture_dir(project) / retry.quarantine_name).exists()
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
        first = store.sweep(max_items=2, max_duration_seconds=1)
        assert first.examined == 2
        assert first.deleted == 2
        assert first.remaining_due == 3

        second = store.sweep(max_items=2, max_duration_seconds=1)
        assert second.deleted == 2
        assert second.remaining_due == 1

        third = store.sweep(max_items=2, max_duration_seconds=1)
        assert third.deleted == 1
        assert third.remaining_due == 0
        assert not any((_capture_dir(project) / name).exists() for name in artifact_names)
    finally:
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

        outcome = store.sweep(max_items=5, max_duration_seconds=1)

        assert outcome.examined == 2
        assert outcome.deleted == 2
        assert outcome.remaining_due == 3
        assert outcome.duration >= 1
    finally:
        root.close()
        anchor.close()


def test_cleanup_outcome_is_frozen_and_contains_no_identifiers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    try:
        outcome = store.sweep(max_items=1, max_duration_seconds=1)
        with pytest.raises(FrozenInstanceError):
            outcome.deleted = 10
        serialized = json.dumps(asdict(outcome), sort_keys=True)
        assert _CAPTURE_ID not in serialized
        assert str(project) not in serialized
    finally:
        root.close()
        anchor.close()


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
