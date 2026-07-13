"""Tests for path containment utilities."""

from __future__ import annotations

import os

import pytest

from autoskillit.core.path_containment import (
    ContainmentError,
    check_metadata_stable,
    resolve_contained_path,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


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
