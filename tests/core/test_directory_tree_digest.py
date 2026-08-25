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

import autoskillit.core.io as io_module
from autoskillit.core._plugin_artifact_identity import classify_directory_tree_digest_error
from autoskillit.core.io import TreeVanishedError, directory_tree_digest, strict_walk
from autoskillit.core.types import PluginArtifactUnavailableError, PluginArtifactValidationError

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_REQUIRES_DIR_FD = pytest.mark.skipif(
    os.name == "nt" or os.scandir not in os.supports_fd,
    reason="requires POSIX directory descriptors",
)


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


@_REQUIRES_DIR_FD
class TestDirectoryTreeDigestRaceSafety:
    """Issue #4770: ``directory_tree_digest`` must fail loudly on any entry that
    vanishes or is substituted mid-walk, never silently omit it from the digest.

    ``strict_walk`` is POSIX-only by construction (``os.O_DIRECTORY``/
    ``os.O_NOFOLLOW``/``dir_fd``) — skipped, not failed, on platforms lacking
    directory-descriptor support, mirroring
    ``tests/exploration/test_bounded_collectors.py``'s own guard for its
    dir_fd-based race test.
    """

    def test_root_vanishes_before_walk_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone"
        with pytest.raises(TreeVanishedError) as excinfo:
            directory_tree_digest(missing)
        assert excinfo.value.relative_path == ""
        assert excinfo.value.root == missing

    def test_root_exists_but_is_not_a_directory_still_raises_plain_value_error(
        self, tmp_path: Path
    ) -> None:
        """Companion regression: a genuine precondition violation (root was
        never a directory) must still raise plain ``ValueError`` — proving
        the vanish/precondition split above does not widen
        ``TreeVanishedError`` to also cover this case."""
        plain_file = tmp_path / "not_a_dir.txt"
        plain_file.write_text("data")
        with pytest.raises(ValueError, match="not a regular directory") as excinfo:
            directory_tree_digest(plain_file)
        assert not isinstance(excinfo.value, TreeVanishedError)

    def test_subtree_vanishes_during_walk_raises_not_silently_omits(self, tmp_path: Path) -> None:
        """A queued subdirectory deleted before its own descent-open still
        yields ``TreeVanishedError`` — and critically, no digest is ever
        returned (the current bug's defining symptom: a 64-char hex digest
        silently omitting the vanished subtree)."""
        (tmp_path / "a.py").write_text("hello")
        sub1 = tmp_path / "sub1"
        sub1.mkdir()
        (sub1 / "x.py").write_text("x")
        sub2 = tmp_path / "sub2"
        sub2.mkdir()
        (sub2 / "y.py").write_text("y")

        original_open = os.open
        calls = 0

        def vanish_sub2_before_its_descent_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            if path == "sub2" and (flags & os.O_DIRECTORY):
                calls += 1
                if calls == 1:
                    import shutil

                    shutil.rmtree(sub2)
            return original_open(path, flags, *args, **kwargs)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(io_module.os, "open", vanish_sub2_before_its_descent_open)
        try:
            with pytest.raises(TreeVanishedError) as excinfo:
                directory_tree_digest(tmp_path)
            assert excinfo.value.relative_path == "sub2"
        finally:
            monkeypatch.undo()
        assert calls == 1

    def test_recursive_failure_closes_queued_sibling_descriptors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("a", "b"):
            subtree = tmp_path / name
            subtree.mkdir()
            (subtree / "leaf.txt").write_text(name, encoding="utf-8")

        original_open = os.open
        original_close = os.close
        original_scandir = os.scandir
        opened_subdirs: dict[str, int] = {}
        closed_fds: set[int] = set()

        def tracking_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            fd = original_open(path, flags, *args, **kwargs)
            if path in {"a", "b"} and flags & os.O_DIRECTORY:
                opened_subdirs[path] = fd
            return fd

        def fail_first_subtree(fd):  # type: ignore[no-untyped-def]
            if fd == opened_subdirs.get("a"):
                raise FileNotFoundError("injected recursive failure")
            return original_scandir(fd)

        def tracking_close(fd: int) -> None:
            closed_fds.add(fd)
            original_close(fd)

        monkeypatch.setattr(io_module.os, "open", tracking_open)
        monkeypatch.setattr(io_module.os, "scandir", fail_first_subtree)
        monkeypatch.setattr(io_module.os, "close", tracking_close)

        with pytest.raises(TreeVanishedError):
            directory_tree_digest(tmp_path)

        assert set(opened_subdirs.values()) <= closed_fds

    def test_entry_vanishes_between_list_and_stat_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        original_stat = os.DirEntry.stat
        calls = 0

        def counted_stat(self: os.DirEntry, *args: object, **kwargs: object) -> os.stat_result:
            nonlocal calls
            calls += 1
            if calls == 2:
                (tmp_path / self.name).unlink()
            return original_stat(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os.DirEntry, "stat", counted_stat)
        with pytest.raises(TreeVanishedError):
            directory_tree_digest(tmp_path)
        assert calls == 2

    def test_file_vanishes_between_stat_and_open_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "a.py"
        target.write_text("content")
        original_open = os.open

        def vanish_before_file_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            if path == "a.py" and not (flags & os.O_DIRECTORY):
                target.unlink()
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(io_module.os, "open", vanish_before_file_open)
        with pytest.raises(TreeVanishedError) as excinfo:
            directory_tree_digest(tmp_path)
        assert excinfo.value.relative_path == "a.py"

    def test_symlink_vanishes_between_stat_and_readlink_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = tmp_path / "real.py"
        real.write_text("data")
        link = tmp_path / "link.py"
        link.symlink_to(real)
        original_readlink = os.readlink

        def vanish_before_readlink(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if path == "link.py":
                link.unlink()
            return original_readlink(path, *args, **kwargs)

        monkeypatch.setattr(io_module.os, "readlink", vanish_before_readlink)
        with pytest.raises(TreeVanishedError) as excinfo:
            directory_tree_digest(tmp_path, allow_symlinks=True)
        assert excinfo.value.relative_path == "link.py"

    def test_parent_directory_substitution_between_levels_is_rejected(
        self,
        tmp_path: Path,
        tmp_path_factory: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The exact reproduction that falsified an ``os.fwalk``-based draft
        of ``strict_walk`` during review: ``os.fwalk``'s internal
        ``samestat()`` guard detects this race but silently declines to
        descend rather than raising. ``strict_walk`` must raise instead."""
        queued = tmp_path / "queued"
        queued.mkdir()
        (queued / "safe.txt").write_text("safe")
        outside = tmp_path_factory.mktemp("substitution-target")
        (outside / "secret.txt").write_text("secret")
        detached = tmp_path / "detached"

        original_open = os.open
        replaced = False

        def racing_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal replaced
            if path == "queued" and not replaced and (flags & os.O_DIRECTORY):
                replaced = True
                queued.rename(detached)
                queued.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(io_module.os, "open", racing_open)
        with pytest.raises(TreeVanishedError) as excinfo:
            directory_tree_digest(tmp_path)
        assert excinfo.value.relative_path == "queued"
        assert replaced

    def test_permission_error_still_propagates_unguarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression proving the fix is narrowly scoped: a ``PermissionError``
        (not ``FileNotFoundError``/``NotADirectoryError``) must propagate
        unchanged, both at the walk/scandir boundary and the per-entry
        stat/open boundary."""
        (tmp_path / "a.py").write_text("hello")

        def failing_scandir(_fd: int) -> object:
            raise PermissionError("injected diagnostic permission failure")

        monkeypatch.setattr(io_module.os, "scandir", failing_scandir)
        with pytest.raises(PermissionError):
            directory_tree_digest(tmp_path)

    def test_permission_error_at_entry_stat_still_propagates_unguarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "a.py").write_text("hello")

        def failing_stat(_self: os.DirEntry, *_args: object, **_kwargs: object) -> os.stat_result:
            raise PermissionError("injected diagnostic permission failure")

        monkeypatch.setattr(os.DirEntry, "stat", failing_stat)
        with pytest.raises(PermissionError):
            directory_tree_digest(tmp_path)

    def test_permission_error_at_entry_type_check_still_propagates_unguarded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class InaccessibleEntry:
            name = "blocked"

            def is_dir(self) -> bool:
                raise PermissionError("injected entry type-check failure")

        with monkeypatch.context() as patch:
            patch.setattr(io_module.os, "scandir", lambda _fd: [InaccessibleEntry()])
            with pytest.raises(PermissionError):
                directory_tree_digest(tmp_path)

    def test_golden_digest_unchanged_for_non_racing_tree(self, tmp_path: Path) -> None:
        """Compatibility guard: the digest algorithm's *output* for a
        non-racing tree must be byte-for-byte unchanged by the rewrite —
        only its race behavior changes. Pinned hex values were captured
        from the pre-fix, ``os.walk``-based implementation against a fixed,
        deterministic multi-sibling-directory fixture (order-sensitivity —
        the two-pass recursion redesign only manifests a bug when a
        directory has a later sibling at the same level)."""

        def build_multi_sibling_tree(root: Path) -> None:
            (root / "aaa.py").write_text("first file")
            bbb_dir = root / "bbb_dir"
            bbb_dir.mkdir()
            (bbb_dir / "inner.py").write_text("inside bbb")
            (bbb_dir / "zzz_nested.py").write_text("nested sibling")
            (root / "ccc.py").write_text("third file")
            mmm_dir = root / "mmm_dir"
            mmm_dir.mkdir()
            (mmm_dir / "deep").mkdir()
            (mmm_dir / "deep" / "leaf.py").write_text("deep leaf")
            (mmm_dir / "aaa_first.py").write_text("mmm sibling a")
            zzz_dir = root / "zzz_dir"
            zzz_dir.mkdir()
            (zzz_dir / "inner2.py").write_text("inside zzz")

        build_multi_sibling_tree(tmp_path)
        assert directory_tree_digest(tmp_path) == (
            "df53a7221e47379fe8ecc8b7b2a4c298cf87a6770e7313e46ffeada2858fcb91"
        )
        assert directory_tree_digest(tmp_path, ignore_bytecode=True) == (
            "df53a7221e47379fe8ecc8b7b2a4c298cf87a6770e7313e46ffeada2858fcb91"
        )

        symlink_root = tmp_path.parent / f"{tmp_path.name}_symlink"
        symlink_root.mkdir()
        build_multi_sibling_tree(symlink_root)
        os.symlink("../aaa.py", symlink_root / "bbb_dir" / "aaa_link.py")
        assert directory_tree_digest(symlink_root, allow_symlinks=True) == (
            "b5bd3a2910b549db23554256c9209f285a49b937d1b479be2007e88387e0c0e0"
        )

    def test_tree_vanished_error_classifies_as_unavailable(self) -> None:
        """A race-induced vanish is transient, not tamper evidence: routed to
        the Unavailable outcome, never the durable/invalid outcome — and
        checked *before* the generic ``ValueError`` branch, since
        ``TreeVanishedError`` is itself a ``ValueError`` subclass."""
        exc = TreeVanishedError("sub/missing.py", Path("/tmp/artifact"))
        classified = classify_directory_tree_digest_error(exc)
        assert isinstance(classified, PluginArtifactUnavailableError)
        assert not isinstance(classified, PluginArtifactValidationError)

    def test_plain_value_error_classifies_as_validation(self) -> None:
        classified = classify_directory_tree_digest_error(
            ValueError("artifact contains a symlink")
        )
        assert isinstance(classified, PluginArtifactValidationError)

    def test_os_error_classifies_as_unavailable(self) -> None:
        classified = classify_directory_tree_digest_error(PermissionError("denied"))
        assert isinstance(classified, PluginArtifactUnavailableError)

    def test_strict_walk_raises_clear_error_when_posix_dir_fd_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Platform-capability guard, not a race test — runs everywhere."""
        monkeypatch.delattr(io_module.os, "O_NOFOLLOW", raising=False)
        with pytest.raises(NotImplementedError, match="O_DIRECTORY"):
            directory_tree_digest(tmp_path)

    def test_strict_walk_is_directly_reusable_by_non_digest_callers(self, tmp_path: Path) -> None:
        """``strict_walk`` is a shared primitive, not digest-private —
        Phase C consumers call it directly for their own tamper checks."""
        (tmp_path / "a.py").write_text("hello")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("world")
        entries = {entry.relative_path: entry.kind for entry in strict_walk(tmp_path)}
        assert entries == {"a.py": "f", "sub": "d", "sub/b.py": "f"}
