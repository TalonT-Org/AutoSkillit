"""Tests for plugin artifact identity and cross-process lease ownership."""

from __future__ import annotations

import errno
import os
import select
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactContentionError,
    PluginArtifactIdentity,
    PluginArtifactPublicationError,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    PluginLoadMode,
)

pytestmark = [
    pytest.mark.layer("core"),
    pytest.mark.medium,
    pytest.mark.skipif(os.name != "posix", reason="artifact leases require POSIX flock"),
]


def _identity(tmp_path: Path) -> PluginArtifactIdentity:
    return PluginArtifactIdentity(
        semantic_key="semantic-key",
        incarnation_id="00000000000040008000000000000001",
        manifest_schema_version=1,
        artifact_digest="a" * 64,
        managed_path=tmp_path / "projection",
        manifest_path=tmp_path / "projection.json",
    )


def test_raw_constructor_cannot_claim_an_unrelated_descriptor(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(TypeError, match="created by an acquire method"):
            ArtifactLease(
                path=tmp_path / "projection.lock",
                fd=read_fd,
                shared=True,
            )

        os.fstat(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_independent_shared_readers_own_distinct_descriptors(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"

    with ArtifactLease.acquire_shared(lock_path) as first:
        with ArtifactLease.acquire_shared(lock_path) as second:
            assert first.path == lock_path
            assert second.path == lock_path
            assert first.shared is True
            assert second.shared is True
            assert first.fileno() != second.fileno()
            assert first.inherited_fds == (first.fileno(),)
            assert second.inherited_fds == (second.fileno(),)


def test_existing_shared_requires_existing_parent_and_sidecar(tmp_path: Path) -> None:
    missing_parent_lock = tmp_path / "missing" / "projection.lock"
    with pytest.raises(FileNotFoundError):
        ArtifactLease.acquire_existing_shared(missing_parent_lock)
    assert not missing_parent_lock.parent.exists()

    lock_path = tmp_path / "projection.lock"
    with pytest.raises(FileNotFoundError):
        ArtifactLease.acquire_existing_shared(lock_path)
    assert not lock_path.exists()


def test_existing_shared_is_read_only_and_preserves_modes(tmp_path: Path) -> None:
    import fcntl

    lock_path = tmp_path / "projection.lock"
    lock_path.touch(mode=0o640)
    tmp_path.chmod(0o750)
    lock_path.chmod(0o640)

    with ArtifactLease.acquire_existing_shared(lock_path) as reader:
        descriptor_flags = fcntl.fcntl(reader.fileno(), fcntl.F_GETFL)
        assert descriptor_flags & os.O_ACCMODE == os.O_RDONLY
        assert reader.path == lock_path
        assert reader.shared is True
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o640

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o640


def test_existing_shared_blocks_exclusive_until_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    with ArtifactLease.acquire_exclusive(lock_path, blocking=False):
        pass
    reader = ArtifactLease.acquire_existing_shared(lock_path)

    try:
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lock_path, blocking=False)
    finally:
        reader.close()

    with ArtifactLease.acquire_exclusive(lock_path, blocking=False):
        pass


def test_existing_shared_child_blocks_writer_without_mutating_sidecar(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "projection.lock"
    missing_lock = tmp_path / "missing" / "projection.lock"
    lock_path.touch()
    tmp_path.chmod(0o750)
    lock_path.chmod(0o640)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import errno, fcntl, os, stat, sys\n"
                "from pathlib import Path\n"
                "from autoskillit.core import ArtifactLease\n"
                "missing_lock = Path(sys.argv[1])\n"
                "lock_path = Path(sys.argv[2])\n"
                "try:\n"
                "    ArtifactLease.acquire_existing_shared(missing_lock)\n"
                "except FileNotFoundError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('missing lease sidecar was created')\n"
                "assert not missing_lock.parent.exists()\n"
                "reader = ArtifactLease.acquire_existing_shared(lock_path)\n"
                "fd = reader.fileno()\n"
                "flags = fcntl.fcntl(fd, fcntl.F_GETFL)\n"
                "assert reader.shared is True\n"
                "assert reader.inherited_fds == (fd,)\n"
                "print(\n"
                "    f'acquired:{flags & os.O_ACCMODE}:'\n"
                "    f'{stat.S_IMODE(lock_path.parent.stat().st_mode)}:'\n"
                "    f'{stat.S_IMODE(lock_path.stat().st_mode)}',\n"
                "    flush=True,\n"
                ")\n"
                "if sys.stdin.buffer.read(1) != b'x':\n"
                "    raise RuntimeError('parent closed coordination pipe')\n"
                "reader.close()\n"
                "assert reader.closed is True\n"
                "try:\n"
                "    os.fstat(fd)\n"
                "except OSError as exc:\n"
                "    assert exc.errno == errno.EBADF\n"
                "else:\n"
                "    raise AssertionError('reader descriptor remained open')\n"
                "print('released', flush=True)\n"
            ),
            str(missing_lock),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert child.stdout is not None
        readable, _, _ = select.select([child.stdout], [], [], 5)
        assert readable, "child did not confirm existing shared lease acquisition"
        assert child.stdout.readline() == f"acquired:{os.O_RDONLY}:{0o750}:{0o640}\n"

        assert not missing_lock.parent.exists()
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o750
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o640
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lock_path, blocking=False)

        assert child.stdin is not None
        child.stdin.write("x")
        child.stdin.flush()
        readable, _, _ = select.select([child.stdout], [], [], 5)
        assert readable, "child did not confirm reader descriptor close"
        assert child.stdout.readline() == "released\n"
        child.stdin.close()
        assert child.wait(timeout=5) == 0
        assert child.stderr is not None
        assert child.stderr.read() == ""

        with ArtifactLease.acquire_exclusive(lock_path, blocking=False):
            pass
    finally:
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_existing_shared_rejects_symlink_and_non_regular_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "target.lock"
    target.touch()
    symlink = tmp_path / "symlink.lock"
    symlink.symlink_to(target)

    with pytest.raises(OSError):
        ArtifactLease.acquire_existing_shared(symlink)
    assert symlink.is_symlink()

    fifo = tmp_path / "fifo.lock"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeError, match="regular file"):
        ArtifactLease.acquire_existing_shared(fifo)
    assert stat.S_ISFIFO(fifo.stat().st_mode)


def test_artifact_lease_context_preserves_body_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    real_close = ArtifactLease.close

    def fail_after_close(owner: ArtifactLease) -> None:
        real_close(owner)
        raise OSError("injected lease close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    with pytest.raises(RuntimeError, match="primary body failure") as caught:
        with lease:
            raise RuntimeError("primary body failure")

    assert lease.closed is True
    assert any(
        "injected lease close failure" in note for note in getattr(caught.value, "__notes__", ())
    )


def test_exclusive_lease_waits_for_final_shared_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    first = ArtifactLease.acquire_shared(lock_path)
    second = ArtifactLease.acquire_shared(lock_path)

    try:
        with pytest.raises(ArtifactLeaseContention) as caught:
            ArtifactLease.acquire_exclusive(lock_path, blocking=False)
        assert caught.value.path == lock_path

        first.close()
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lock_path, blocking=False)

        second.close()
        with ArtifactLease.acquire_exclusive(lock_path, blocking=False) as writer:
            assert writer.path == lock_path
            assert writer.shared is False
    finally:
        first.close()
        second.close()


def test_blocking_exclusive_lease_waits_then_acquires(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    reader = ArtifactLease.acquire_shared(lock_path)
    writer_started = threading.Event()
    writer_acquired = threading.Event()
    failures: list[BaseException] = []

    def acquire_writer() -> None:
        writer_started.set()
        try:
            with ArtifactLease.acquire_exclusive(lock_path, blocking=True):
                writer_acquired.set()
        except BaseException as exc:
            failures.append(exc)

    writer = threading.Thread(
        target=acquire_writer,
        name="artifact-lease-blocking-writer",
        daemon=True,
    )
    writer.start()
    try:
        assert writer_started.wait(timeout=5)
        assert not writer_acquired.wait(timeout=0.1)

        reader.close()

        assert writer_acquired.wait(timeout=5)
        writer.join(timeout=5)
        assert not writer.is_alive()
        assert failures == []
    finally:
        reader.close()
        writer.join(timeout=5)


def test_inherited_descriptor_keeps_lease_after_parent_close(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    lease = ArtifactLease.acquire_shared(lock_path)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                "os.fstat(int(sys.argv[1]))\n"
                "sys.stdout.write('ready\\n')\n"
                "sys.stdout.flush()\n"
                "sys.stdin.buffer.read(1)\n"
            ),
            str(lease.fileno()),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=lease.inherited_fds,
        text=True,
    )

    try:
        assert child.stdout is not None
        readable, _, _ = select.select([child.stdout], [], [], 5)
        assert readable, "child did not confirm inherited descriptor"
        assert child.stdout.readline() == "ready\n"

        lease.close()
        with pytest.raises(ArtifactLeaseContention):
            ArtifactLease.acquire_exclusive(lock_path, blocking=False)

        assert child.stdin is not None
        child.stdin.write("x")
        child.stdin.close()
        assert child.wait(timeout=5) == 0

        with ArtifactLease.acquire_exclusive(lock_path, blocking=False):
            pass
    finally:
        lease.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_lease_file_is_regular_and_close_is_idempotent(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    lease = ArtifactLease.acquire_shared(lock_path)
    owned_fd = lease.fileno()

    assert stat.S_ISREG(lock_path.stat(follow_symlinks=False).st_mode)
    assert lease.closed is False
    assert not hasattr(lease, "release")

    lease.close()
    lease.close()

    assert lease.closed is True
    assert lease.fd is None
    assert lease.inherited_fds == ()
    with pytest.raises(OSError):
        os.fstat(owned_fd)
    with pytest.raises(ValueError, match="closed"):
        lease.fileno()


def test_lease_rejects_non_lock_suffix_and_final_symlink(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.lock"):
        ArtifactLease.acquire_shared(tmp_path / "projection.lease")

    target = tmp_path / "target.lock"
    target.touch()
    link = tmp_path / "projection.lock"
    link.symlink_to(target)

    with pytest.raises(OSError):
        ArtifactLease.acquire_shared(link)
    assert link.is_symlink()
    assert target.is_file()


def test_lease_rejects_non_regular_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "projection.lock"
    os.mkfifo(lock_path)

    with pytest.raises(RuntimeError, match="regular file"):
        ArtifactLease.acquire_shared(lock_path)


def test_acquire_preserves_primary_error_when_descriptor_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.core.runtime import artifact_lease

    real_open = artifact_lease.os.open
    real_close = artifact_lease.os.close
    opened_fds: list[int] = []

    def recording_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    def failing_flock(_fd, _operation):
        raise OSError(errno.EIO, "primary flock failure")

    def failing_close(_fd):
        raise OSError(errno.EBADF, "cleanup close failure")

    monkeypatch.setattr(artifact_lease.os, "open", recording_open)
    monkeypatch.setattr(artifact_lease.os, "close", failing_close)
    monkeypatch.setattr(artifact_lease.fcntl, "flock", failing_flock)

    try:
        with pytest.raises(OSError, match="primary flock failure") as caught:
            ArtifactLease.acquire_shared(tmp_path / "projection.lock")
        assert caught.value.errno == errno.EIO
        assert sum("cleanup close failure" in note for note in caught.value.__notes__) == 2
    finally:
        for fd in reversed(opened_fds):
            real_close(fd)


def test_directory_close_failure_releases_acquired_lease_fd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from autoskillit.core.runtime import artifact_lease

    real_open = artifact_lease.os.open
    real_close = artifact_lease.os.close
    opened_fds: list[int] = []

    def recording_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    def fail_directory_close(fd: int) -> None:
        if fd == opened_fds[0]:
            raise OSError(errno.EIO, "directory close failure")
        real_close(fd)

    monkeypatch.setattr(artifact_lease.os, "open", recording_open)
    monkeypatch.setattr(artifact_lease.os, "close", fail_directory_close)

    try:
        with pytest.raises(OSError, match="directory close failure"):
            ArtifactLease.acquire_shared(tmp_path / "projection.lock")
        assert len(opened_fds) == 2
        with pytest.raises(OSError):
            os.fstat(opened_fds[1])
    finally:
        real_close(opened_fds[0])


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("semantic_key", ""),
        ("semantic_key", 1),
        ("incarnation_id", ""),
        ("incarnation_id", "not-canonical"),
        ("manifest_schema_version", 0),
        ("manifest_schema_version", True),
        ("manifest_schema_version", 1.5),
        ("artifact_digest", ""),
        ("artifact_digest", "z" * 64),
        ("artifact_digest", 1),
        ("managed_path", Path("relative-projection")),
        ("managed_path", "/absolute-but-not-a-Path"),
        ("manifest_path", Path("relative-manifest.json")),
        ("manifest_path", "/absolute-but-not-a-Path"),
    ],
)
def test_plugin_artifact_identity_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "semantic_key": "semantic-key",
        "incarnation_id": "00000000000040008000000000000001",
        "manifest_schema_version": 1,
        "artifact_digest": "a" * 64,
        "managed_path": tmp_path / "projection",
        "manifest_path": tmp_path / "projection.json",
    }
    values[field] = invalid

    with pytest.raises(ValueError):
        PluginArtifactIdentity(**values)  # type: ignore[arg-type]


def test_plugin_load_modes_identify_artifact_consumers() -> None:
    assert {mode for mode in PluginLoadMode if mode.consumes_artifact} == {
        PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        PluginLoadMode.PROJECTED_HOME,
    }


def test_launch_binding_context_closes_lease_idempotently(tmp_path: Path) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    binding = PluginLaunchBinding(
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        plugin_dir=tmp_path / "projection",
        identity=_identity(tmp_path),
        inherited_fds=(lease.fileno(), lease.fileno()),
        _lease=lease,
    )

    with binding as entered:
        assert entered is binding
        assert binding.closed is False
        assert binding.inherited_fds == lease.inherited_fds

    assert binding.closed is True
    binding.close()


def test_launch_binding_context_preserves_body_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    binding = PluginLaunchBinding(
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        plugin_dir=tmp_path / "projection",
        identity=_identity(tmp_path),
        inherited_fds=lease.inherited_fds,
        _lease=lease,
    )
    real_close = ArtifactLease.close

    def fail_after_close(owner: ArtifactLease) -> None:
        real_close(owner)
        raise OSError("injected binding close failure")

    monkeypatch.setattr(ArtifactLease, "close", fail_after_close)

    with pytest.raises(RuntimeError, match="primary launch failure") as caught:
        with binding:
            raise RuntimeError("primary launch failure")

    assert binding.closed is True
    assert any(
        "injected binding close failure" in note for note in getattr(caught.value, "__notes__", ())
    )


def test_launch_binding_rejects_path_identity_mismatch(tmp_path: Path) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    try:
        with pytest.raises(ValueError, match="must match the leased artifact identity"):
            PluginLaunchBinding(
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
                plugin_dir=tmp_path / "different-projection",
                identity=_identity(tmp_path),
                inherited_fds=lease.inherited_fds,
                _lease=lease,
            )
    finally:
        lease.close()


def test_launch_binding_rejects_descriptors_owned_by_another_lease(
    tmp_path: Path,
) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    other = ArtifactLease.acquire_shared(tmp_path / "other.lock")
    try:
        with pytest.raises(ValueError, match="owned by the launch lease"):
            PluginLaunchBinding(
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
                plugin_dir=tmp_path / "projection",
                identity=_identity(tmp_path),
                inherited_fds=other.inherited_fds,
                _lease=lease,
            )
    finally:
        other.close()
        lease.close()


@pytest.mark.parametrize("inherited_fds", [(-1,), (True,), ("3",)])
def test_launch_binding_rejects_invalid_inherited_descriptors(
    tmp_path: Path,
    inherited_fds: tuple[object, ...],
) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    try:
        with pytest.raises(ValueError, match="non-negative integer"):
            PluginLaunchBinding(
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
                plugin_dir=tmp_path / "projection",
                identity=_identity(tmp_path),
                inherited_fds=inherited_fds,  # type: ignore[arg-type]
                _lease=lease,
            )
    finally:
        lease.close()


@pytest.mark.parametrize(
    "load_mode",
    [PluginLoadMode.GENERATED_HOME, PluginLoadMode.NONE],
)
def test_launch_binding_rejects_non_consuming_modes(
    tmp_path: Path,
    load_mode: PluginLoadMode,
) -> None:
    lease = ArtifactLease.acquire_shared(tmp_path / "projection.lock")
    try:
        with pytest.raises(ValueError, match="non-artifact mode"):
            PluginLaunchBinding(
                load_mode=load_mode,
                plugin_dir=tmp_path / "projection",
                identity=_identity(tmp_path),
                inherited_fds=lease.inherited_fds,
                _lease=lease,
            )
    finally:
        lease.close()


@pytest.mark.parametrize(
    "error_type",
    [
        PluginArtifactContentionError,
        PluginArtifactPublicationError,
        PluginArtifactValidationError,
    ],
)
def test_plugin_artifact_errors_are_typed_runtime_errors(
    error_type: type[RuntimeError],
) -> None:
    error = error_type("artifact failure")
    assert isinstance(error, RuntimeError)
    assert str(error) == "artifact failure"
