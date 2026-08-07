"""First-ever direct unit tests for ``directory_tree_digest``.

Covers determinism, mode sensitivity, entry-kind sensitivity, content
sensitivity, and characterizes that bytecode *is* hashed when present
(documenting why exclusion must happen upstream in the predicate, not
in the digest function).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.core.io import directory_tree_digest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _seed_tree(root: Path) -> None:
    """Seed a minimal two-file tree."""
    (root / "a.py").write_text("hello")
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("world")


class TestDirectoryTreeDigest:
    def test_determinism(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d1 = directory_tree_digest(tmp_path)
        d2 = directory_tree_digest(tmp_path)
        assert d1 == d2

    def test_content_sensitivity(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d1 = directory_tree_digest(tmp_path)
        (tmp_path / "a.py").write_text("changed")
        d2 = directory_tree_digest(tmp_path)
        assert d1 != d2

    def test_mode_sensitivity(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d1 = directory_tree_digest(tmp_path)
        f = tmp_path / "a.py"
        f.chmod(f.stat().st_mode | 0o100)
        d2 = directory_tree_digest(tmp_path)
        assert d1 != d2

    def test_entry_kind_sensitivity(self, tmp_path: Path) -> None:
        """A file vs. a directory with the same name produce different digests."""
        root_a = tmp_path / "variant_a"
        root_a.mkdir()
        (root_a / "x").write_text("file")

        root_b = tmp_path / "variant_b"
        root_b.mkdir()
        (root_b / "x").mkdir()

        assert directory_tree_digest(root_a) != directory_tree_digest(root_b)

    def test_empty_tree(self, tmp_path: Path) -> None:
        d = directory_tree_digest(tmp_path)
        assert isinstance(d, str) and len(d) == 64

    def test_symlink_rejection(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("data")
        os.symlink(tmp_path / "real.py", tmp_path / "link.py")
        with pytest.raises(ValueError, match="symlink"):
            directory_tree_digest(tmp_path)

    def test_bytecode_is_hashed_when_present(self, tmp_path: Path) -> None:
        """Characterize: bytecode IS included in the digest.

        This documents that exclusion must happen upstream in the predicate
        (``is_projected_asset``), not by modifying the digest function —
        the digest must always hash exactly what is present (invariant 9).
        """
        _seed_tree(tmp_path)
        d_clean = directory_tree_digest(tmp_path)

        pycache = tmp_path / "sub" / "__pycache__"
        pycache.mkdir()
        (pycache / "b.cpython-311.pyc").write_bytes(b"fake pyc")

        d_dirty = directory_tree_digest(tmp_path)
        assert d_clean != d_dirty, (
            "digest should change when bytecode is added — "
            "exclusion must happen upstream, not in the digest"
        )
