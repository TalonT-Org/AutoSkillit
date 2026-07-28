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


def test_sweep_is_bounded_and_repeated_calls_make_progress(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store = _open_store(project, clock)
    artifacts = []
    try:
        for index in range(5):
            capture_id = f"{index + 1:016x}"
            artifact = create_capture_artifact(root, capture_id, store)
            store.finalize_capture(capture_id, size=index + 1, sha256=_DIGEST, failed=False)
            artifact.close()
            artifact.release_lease()
            artifacts.append(artifact)

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
        assert not any((_capture_dir(project) / artifact.name).exists() for artifact in artifacts)
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
        with ledger.open("ab") as stream:
            stream.write(capture_lifecycle.FRAME_MAGIC + b"\x00\x00")
        assert store.get_record(_CAPTURE_ID).state is CaptureState.RESERVED
        assert ledger.stat().st_size < capture_lifecycle.MAX_LEDGER_BYTES
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
