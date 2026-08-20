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
        entry = tmp_path / "x"
        entry.write_text("file")
        file_digest = directory_tree_digest(tmp_path)

        entry.unlink()
        entry.mkdir()
        directory_digest = directory_tree_digest(tmp_path)

        assert file_digest != directory_digest

    def test_empty_tree(self, tmp_path: Path) -> None:
        d = directory_tree_digest(tmp_path)
        assert isinstance(d, str) and len(d) == 64

    def test_symlink_rejection(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("data")
        os.symlink(tmp_path / "real.py", tmp_path / "link.py")
        with pytest.raises(ValueError, match="symlink"):
            directory_tree_digest(tmp_path)

    def test_bytecode_is_hashed_when_present(self, tmp_path: Path) -> None:
        """Characterize: bytecode IS included in the digest, by default.

        This documents that exclusion must happen upstream in the predicate
        (``is_projected_asset``) or via ``ignore_bytecode=True`` (issue #4597
        Phase 3), not silently by default — the digest must always hash
        exactly what is present unless explicitly told otherwise
        (invariant 9).
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


class TestDirectoryTreeDigestAllowSymlinks:
    """``allow_symlinks=True`` (issue #4597 Phase 3): real venvs installed by
    ``uv`` always contain symlinks (``lib64 -> lib``, interpreter aliases) —
    sanitized plugin/projection content must still reject them by default.
    """

    def test_default_still_rejects_symlinks(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("data")
        os.symlink(tmp_path / "real.py", tmp_path / "link.py")
        with pytest.raises(ValueError, match="symlink"):
            directory_tree_digest(tmp_path)

    def test_allow_symlinks_true_permits_and_hashes_them(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("data")
        os.symlink(tmp_path / "real.py", tmp_path / "link.py")
        digest = directory_tree_digest(tmp_path, allow_symlinks=True)
        assert isinstance(digest, str) and len(digest) == 64

    def test_allow_symlinks_true_is_sensitive_to_retargeting(self, tmp_path: Path) -> None:
        """A symlink's target string is part of its digest contribution —
        retargeting it must change the digest, keeping it tamper-evident."""
        (tmp_path / "target_a").mkdir()
        (tmp_path / "target_b").mkdir()
        link = tmp_path / "link"
        os.symlink(tmp_path / "target_a", link)
        d1 = directory_tree_digest(tmp_path, allow_symlinks=True)

        link.unlink()
        os.symlink(tmp_path / "target_b", link)
        d2 = directory_tree_digest(tmp_path, allow_symlinks=True)

        assert d1 != d2

    def test_allow_symlinks_true_does_not_descend_into_symlinked_directory(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """A symlink-to-directory contributes only its own kind/target, never
        its pointee's contents — matching ``os.walk(followlinks=False)``.

        The pointee lives outside ``tmp_path`` entirely, so it is reachable
        *only* through the symlink — unlike a target nested inside the tree
        being digested, which ``os.walk`` would independently walk on its
        own regardless of any symlink pointing at it.
        """
        target = tmp_path_factory.mktemp("symlink-target")
        (target / "inside.py").write_text("should not be walked into")
        os.symlink(target, tmp_path / "link")

        digest_with_link = directory_tree_digest(tmp_path, allow_symlinks=True)

        # The same symlink, now pointing at a target whose content differs,
        # must produce the identical digest — proving the pointee's content
        # was never descended into for either tree.
        (target / "inside.py").unlink()
        digest_with_empty_target = directory_tree_digest(tmp_path, allow_symlinks=True)

        assert digest_with_link == digest_with_empty_target


class TestDirectoryTreeDigestIgnoreBytecode:
    """``ignore_bytecode=True`` (issue #4597 Phase 3): an install-root
    generation's own interpreter writes ``__pycache__`` merely by being
    imported, so treating that as content drift would make any generation
    that has ever actually run permanently fail its own digest check.
    """

    def test_default_still_hashes_bytecode(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d_clean = directory_tree_digest(tmp_path)
        pycache = tmp_path / "sub" / "__pycache__"
        pycache.mkdir()
        (pycache / "b.cpython-311.pyc").write_bytes(b"fake pyc")
        assert directory_tree_digest(tmp_path) != d_clean

    def test_ignore_bytecode_true_is_insensitive_to_pycache_dir(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d_clean = directory_tree_digest(tmp_path, ignore_bytecode=True)

        pycache = tmp_path / "sub" / "__pycache__"
        pycache.mkdir()
        (pycache / "b.cpython-311.pyc").write_bytes(b"fake pyc")

        assert directory_tree_digest(tmp_path, ignore_bytecode=True) == d_clean

    def test_ignore_bytecode_true_is_insensitive_to_top_level_pyc_file(
        self, tmp_path: Path
    ) -> None:
        _seed_tree(tmp_path)
        d_clean = directory_tree_digest(tmp_path, ignore_bytecode=True)

        (tmp_path / "stray.pyc").write_bytes(b"fake pyc")

        assert directory_tree_digest(tmp_path, ignore_bytecode=True) == d_clean

    def test_ignore_bytecode_true_still_hashes_real_content_changes(self, tmp_path: Path) -> None:
        _seed_tree(tmp_path)
        d1 = directory_tree_digest(tmp_path, ignore_bytecode=True)
        (tmp_path / "a.py").write_text("changed")
        d2 = directory_tree_digest(tmp_path, ignore_bytecode=True)
        assert d1 != d2
