"""Tests for path containment utilities."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core.path_containment import (
    ContainmentError,
    check_metadata_stable,
    read_stable_contained_bytes,
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
