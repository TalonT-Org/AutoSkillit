"""Tests for plugin artifact identity and cross-process lease ownership."""

from __future__ import annotations

import os
import select
import stat
import subprocess
import sys
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
    pytest.mark.small,
    pytest.mark.skipif(os.name != "posix", reason="artifact leases require POSIX flock"),
]


def _identity(tmp_path: Path) -> PluginArtifactIdentity:
    return PluginArtifactIdentity(
        semantic_key="semantic-key",
        incarnation_id="incarnation-id",
        manifest_schema_version=1,
        artifact_digest="artifact-digest",
        managed_path=tmp_path / "projection",
        manifest_path=tmp_path / "projection.json",
    )


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


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("semantic_key", ""),
        ("incarnation_id", ""),
        ("manifest_schema_version", 0),
        ("artifact_digest", ""),
        ("managed_path", Path("relative-projection")),
        ("manifest_path", Path("relative-manifest.json")),
    ],
)
def test_plugin_artifact_identity_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    invalid: object,
) -> None:
    values: dict[str, object] = {
        "semantic_key": "semantic-key",
        "incarnation_id": "incarnation-id",
        "manifest_schema_version": 1,
        "artifact_digest": "artifact-digest",
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
        PluginLoadMode.IMPLICIT_INSTALLED,
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
