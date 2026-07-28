"""Durable lifecycle, writer-liveness, and reclamation tests."""

from __future__ import annotations

import json
import os
import stat
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
    CaptureLifecycleRecord,
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
    )
    serialized = capture_lifecycle._record_to_dict(record)
    serialized[field_name] = value

    with pytest.raises(CaptureLedgerError, match="invalid lifecycle record fields"):
        capture_lifecycle._record_from_dict(serialized)


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
        store.reserve_capture(_CAPTURE_ID)
        with pytest.raises(
            capture_lifecycle.CaptureLifecycleError,
            match="invalid staged artifact identity",
        ):
            store.mark_staged(_CAPTURE_ID, identity)  # type: ignore[arg-type]

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

    def fail_mark_staged(_capture_id: str, _identity: tuple[int, int]) -> None:
        raise capture_lifecycle.CaptureLifecycleError("primary creation failure")

    def fail_recovery(
        _capture_id: str,
        *,
        size: int,
        sha256: str,
        failed: bool,
    ) -> None:
        del size, sha256, failed
        raise capture_lifecycle.CaptureLifecycleError("secondary recovery failure")

    try:
        monkeypatch.setattr(store, "mark_staged", fail_mark_staged)
        monkeypatch.setattr(store, "finalize_capture", fail_recovery)
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
        first = store.sweep(max_items=8, max_duration_seconds=1)
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
        second = store.sweep(max_items=8, max_duration_seconds=1)
        assert second.tampered == 1
        assert store.get_record(_CAPTURE_ID).state is CaptureState.TAMPERED
        assert not public.exists()
        assert quarantine.read_bytes() == b"replacement"
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
        outcome = store.sweep(max_items=8, max_duration_seconds=1)

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
        artifact.close_artifact_fd()
        artifact.release_lease()
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
                valid_name=capture_lifecycle._PUBLIC_NAME_RE,
            )

        assert observed_fd >= 0
        with pytest.raises(OSError):
            real_fstat(observed_fd)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
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
    store.mark_staged(_CAPTURE_ID, (identity.st_dev, identity.st_ino))
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


def test_finalized_capture_ttl_begins_at_terminal_commit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    clock = _Clock()
    anchor, root, store, artifact = _finalized_capture(project, clock)
    artifact.close_artifact_fd()
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
        (0, _DIGEST, False),
        (0, _DIGEST, 1),
        (0, "", 0),
    ],
)
def test_finalize_rejects_invalid_integrity_metadata(
    tmp_path: Path,
    size: int,
    sha256: str,
    *,
    failed: object,
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
                failed=failed,  # type: ignore[arg-type]
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
    artifact.close_artifact_fd()
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

    def fail_delete(_record: CaptureLifecycleRecord) -> int:
        raise OSError("injected deletion failure")

    try:
        monkeypatch.setattr(store, "_quarantine_delete", fail_delete)
        first = store.sweep(max_items=8, max_duration_seconds=1)
        clock.advance(3)
        second = store.sweep(max_items=8, max_duration_seconds=1)

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

    def fail_first(record: CaptureLifecycleRecord) -> int:
        if record.capture_id == failed_id:
            raise OSError("injected first-row failure")
        return real_delete(record)

    try:
        artifact_names = _seed_finalized_captures(root, store, count=2)
        clock.advance(3601)
        monkeypatch.setattr(store, "_quarantine_delete", fail_first)

        outcome = store.sweep(max_items=2, max_duration_seconds=1)

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
