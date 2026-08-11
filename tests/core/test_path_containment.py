"""Tests for path containment utilities."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.core.path_containment as path_containment
from autoskillit.core.path_containment import (
    ContainmentError,
    check_metadata_stable,
    read_stable_contained_bytes,
    read_stable_contained_range,
    resolve_contained_path,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _with_metadata_drift(metadata: os.stat_result, field: str) -> SimpleNamespace:
    values = {
        "st_dev": metadata.st_dev,
        "st_ino": metadata.st_ino,
        "st_mode": metadata.st_mode,
        "st_mtime_ns": metadata.st_mtime_ns,
        "st_nlink": metadata.st_nlink,
        "st_size": metadata.st_size,
    }
    values[field] = values[field] ^ 0o100 if field == "st_mode" else values[field] + 1
    return SimpleNamespace(**values)


class TestResolveContainedPath:
    def test_accepts_normal_file(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        f = allowed / "ok.txt"
        f.write_text("data")
        resolved = resolve_contained_path(f, allowed)
        assert resolved == f.resolve()

    def test_blocks_traversal(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("nope")
        with pytest.raises(ContainmentError):
            resolve_contained_path(outside, allowed)

    def test_blocks_symlink_escape(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("nope")
        link = allowed / "link"
        link.symlink_to(outside)
        with pytest.raises(ContainmentError, match="[Ss]ymlink"):
            resolve_contained_path(link, allowed)

    def test_blocks_oversized_file(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        f = allowed / "big.bin"
        f.write_bytes(b"a" * 10)
        with pytest.raises(ContainmentError, match="[Ll]arge|[Bb]ig"):
            resolve_contained_path(f, allowed, max_size_bytes=5)

    def test_blocks_world_writable(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        f = allowed / "ww.txt"
        f.write_text("x")
        os.chmod(f, 0o666)
        try:
            with pytest.raises(ContainmentError, match="[Ww]orld"):
                resolve_contained_path(f, allowed)
        finally:
            os.chmod(f, 0o600)

    def test_blocks_hardlink(self, tmp_path) -> None:
        allowed = tmp_path / "root"
        allowed.mkdir()
        source = tmp_path / "source.txt"
        source.write_text("data")
        hardlinked = allowed / "hardlinked.txt"
        try:
            os.link(source, hardlinked)
        except OSError:
            pytest.skip("hardlink not supported in this environment")
        with pytest.raises(ContainmentError, match="[Hh]ardlink"):
            resolve_contained_path(hardlinked, allowed)

    def test_symlinked_allowed_root_is_a_caller_authority_precondition(self, tmp_path) -> None:
        trusted_target = tmp_path / "trusted-target"
        trusted_target.mkdir()
        child = trusted_target / "child.txt"
        child.write_text("data")
        allowed_spelling = tmp_path / "allowed-root"
        allowed_spelling.symlink_to(trusted_target, target_is_directory=True)

        assert resolve_contained_path(allowed_spelling / child.name, allowed_spelling) == child


class TestCheckMetadataStable:
    def test_accepts_unchanged(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("same")
        pre = os.stat(f)
        post = os.stat(f)
        check_metadata_stable(f, pre, post)

    def test_detects_mtime_change(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("a")
        pre = os.stat(f)
        f.write_text("changed content")
        post = os.stat(f)
        with pytest.raises(ContainmentError):
            check_metadata_stable(f, pre, post)


class TestReadStableContainedBytes:
    def test_rejects_intermediate_symlink_swap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        allowed = tmp_path / "root"
        nested = allowed / "nested"
        nested.mkdir(parents=True)
        artifact = nested / "artifact.txt"
        artifact.write_text("stable")
        relocated = tmp_path / "relocated"
        real_open = os.open
        swapped = False

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                nested.rename(relocated)
                nested.symlink_to(relocated, target_is_directory=True)
                swapped = True
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)

        with pytest.raises(ContainmentError, match="[Ss]ymlink|component"):
            read_stable_contained_bytes(artifact, allowed)
        assert swapped

    @pytest.mark.parametrize("field", ("st_dev", "st_mode", "st_nlink"))
    def test_detects_open_file_metadata_drift(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(fd: int):
            nonlocal calls
            metadata = real_fstat(fd)
            calls += 1
            return _with_metadata_drift(metadata, field) if calls == 2 else metadata

        monkeypatch.setattr(os, "fstat", drifting_fstat)

        with pytest.raises(ContainmentError, match="TOCTOU"):
            read_stable_contained_bytes(artifact, tmp_path)
        assert calls == 2

    @pytest.mark.parametrize("field", ("st_dev", "st_mode", "st_nlink"))
    def test_detects_path_metadata_drift_after_read(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        real_stat = Path.stat
        artifact_stat_calls = 0

        def drifting_stat(path: Path, *args, **kwargs):
            nonlocal artifact_stat_calls
            metadata = real_stat(path, *args, **kwargs)
            if path == artifact:
                artifact_stat_calls += 1
                if artifact_stat_calls == 3:
                    return _with_metadata_drift(metadata, field)
            return metadata

        monkeypatch.setattr(Path, "stat", drifting_stat)

        with pytest.raises(ContainmentError, match="TOCTOU"):
            read_stable_contained_bytes(artifact, tmp_path)
        assert artifact_stat_calls == 3


class TestReadStableContainedRange:
    @pytest.mark.parametrize(
        ("offset", "length", "max_range_bytes"),
        [(-1, 1, 10), (0, -1, 10), (0, 11, 10)],
    )
    def test_invalid_ranges_fail_closed(
        self,
        tmp_path: Path,
        offset: int,
        length: int,
        max_range_bytes: int,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")

        with pytest.raises(ContainmentError) as exc_info:
            read_stable_contained_range(
                artifact,
                tmp_path,
                offset=offset,
                length=length,
                max_range_bytes=max_range_bytes,
            )

        assert exc_info.value.reason == "range_invalid"

    @pytest.mark.parametrize(
        ("offset", "length", "expected"),
        [(6, 3, b""), (7, 3, b""), (4, 10, b"le")],
    )
    def test_eof_ranges_return_only_available_opening_snapshot_bytes(
        self,
        tmp_path: Path,
        offset: int,
        length: int,
        expected: bytes,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_bytes(b"stable")

        _, data, opening = read_stable_contained_range(
            artifact,
            tmp_path,
            offset=offset,
            length=length,
        )

        assert data == expected
        assert opening.st_size == 6

    def test_open_identity_drift_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        real_fstat = os.fstat

        monkeypatch.setattr(
            os,
            "fstat",
            lambda fd: _with_metadata_drift(real_fstat(fd), "st_ino"),
        )

        with pytest.raises(ContainmentError) as exc_info:
            read_stable_contained_range(artifact, tmp_path, offset=0, length=3)

        assert exc_info.value.reason == "range_unstable"

    def test_path_replacement_before_secure_open_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        outside = tmp_path / "outside.txt"
        outside.write_text("outside")
        secure_open = path_containment._open_beneath_root_without_symlinks

        def replace_with_symlink(
            path: str | Path,
            allowed_root: str | Path,
            resolved: Path,
        ) -> int:
            artifact.unlink()
            artifact.symlink_to(outside)
            return secure_open(path, allowed_root, resolved)

        monkeypatch.setattr(
            path_containment,
            "_open_beneath_root_without_symlinks",
            replace_with_symlink,
        )

        with pytest.raises(ContainmentError) as exc_info:
            read_stable_contained_range(artifact, tmp_path, offset=0, length=3)

        assert exc_info.value.reason == "containment_error"

    @pytest.mark.parametrize("drift", ["shrink", "mtime"])
    def test_open_snapshot_drift_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        drift: str,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(fd: int):
            nonlocal calls
            metadata = real_fstat(fd)
            calls += 1
            if calls != 2:
                return metadata
            changed = _with_metadata_drift(metadata, "st_mtime_ns")
            if drift == "shrink":
                changed.st_mtime_ns = metadata.st_mtime_ns
                changed.st_size = metadata.st_size - 1
            return changed

        monkeypatch.setattr(os, "fstat", drifting_fstat)

        with pytest.raises(ContainmentError) as exc_info:
            read_stable_contained_range(artifact, tmp_path, offset=0, length=3)

        assert exc_info.value.reason == "range_unstable"

    def test_reads_small_range_from_sparse_file_above_whole_file_cap(self, tmp_path: Path) -> None:
        artifact = tmp_path / "large.jsonl"
        with artifact.open("wb") as handle:
            handle.write(b'{"first":true}\n')
            handle.seek(50_000_001)
            handle.write(b"\n")

        resolved, data, opening = read_stable_contained_range(
            artifact,
            tmp_path,
            offset=0,
            length=15,
        )

        assert resolved == artifact
        assert data == b'{"first":true}\n'
        assert opening.st_size > 50_000_000
        with pytest.raises(ContainmentError, match="too large"):
            read_stable_contained_bytes(artifact, tmp_path)

    def test_secure_open_unavailable_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = tmp_path / "artifact.txt"
        artifact.write_text("stable")
        monkeypatch.delattr(os, "O_NOFOLLOW")

        with pytest.raises(ContainmentError) as exc_info:
            read_stable_contained_range(artifact, tmp_path, offset=0, length=3)

        assert exc_info.value.reason == "secure_open_unavailable"

    def test_append_during_read_is_bounded_to_opening_snapshot(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        artifact = tmp_path / "events.jsonl"
        artifact.write_bytes(b"one\n")
        real_pread = os.pread

        def appending_pread(fd: int, length: int, offset: int) -> bytes:
            data = real_pread(fd, length, offset)
            with artifact.open("ab") as handle:
                handle.write(b"two\n")
            return data

        monkeypatch.setattr(os, "pread", appending_pread)
        _, data, opening = read_stable_contained_range(
            artifact,
            tmp_path,
            offset=0,
            length=100,
        )

        assert data == b"one\n"
        assert opening.st_size == 4
        assert artifact.stat().st_size == 8
