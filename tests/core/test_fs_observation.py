"""Tests for the enumeration-derived path observation funnel."""

from __future__ import annotations

import gc
import os
import shutil
import stat
from pathlib import Path

import pytest

import autoskillit.core.fs_observation as fs_observation
from autoskillit.core import VANISHED_ERRORS, ObservedEntry, scan_observed
from autoskillit.core.fs_observation import observe_path_mode, safe_mtime

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class _FakeDirEntry:
    def __init__(self, path: Path, status: os.stat_result) -> None:
        self.path = str(path)
        self._status = status

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        assert follow_symlinks is False
        return self._status


class _FakeScandir:
    def __init__(self, entries: list[_FakeDirEntry]) -> None:
        self._entries = iter(entries)
        self.closed = False

    def __iter__(self) -> _FakeScandir:
        return self

    def __next__(self) -> _FakeDirEntry:
        return next(self._entries)

    def close(self) -> None:
        self.closed = True


def _regular_file_status() -> os.stat_result:
    return os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 1, 2, 3, 4))


def test_vanished_errors_is_publicly_exported() -> None:
    assert VANISHED_ERRORS == (FileNotFoundError, NotADirectoryError)


@pytest.mark.parametrize("error_type", VANISHED_ERRORS, ids=lambda error: error.__name__)
def test_safe_mtime_normalizes_every_vanished_error(
    error_type: type[OSError],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_vanished(_path: Path) -> float:
        raise error_type("injected")

    monkeypatch.setattr(fs_observation.os.path, "getmtime", raise_vanished)

    assert safe_mtime(tmp_path / "probe") is None


@pytest.mark.parametrize("error_type", VANISHED_ERRORS, ids=lambda error: error.__name__)
def test_observe_path_mode_normalizes_every_vanished_error(
    error_type: type[OSError],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_vanished(_path: Path) -> os.stat_result:
        raise error_type("injected")

    monkeypatch.setattr(Path, "lstat", raise_vanished)

    assert observe_path_mode(tmp_path / "probe") is None


def test_safe_mtime_propagates_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_permission_error(_path: Path) -> float:
        raise PermissionError("injected")

    monkeypatch.setattr(fs_observation.os.path, "getmtime", raise_permission_error)

    with pytest.raises(PermissionError, match="injected"):
        safe_mtime(tmp_path / "probe")


def test_scan_observed_yields_metadata_captured_during_enumeration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "entry.txt"
    target.write_text("hello")
    os.utime(target, (1_700_000_000, 1_700_000_000))
    expected = target.lstat()
    original_stat = os.DirEntry.stat
    calls = 0

    def counted_stat(
        entry: os.DirEntry[str],
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal calls
        calls += 1
        return original_stat(entry, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os.DirEntry, "stat", counted_stat)

    observed = list(scan_observed(tmp_path))

    assert len(observed) == 1
    entry = observed[0]
    assert isinstance(entry, ObservedEntry)
    assert entry.path == target
    assert entry.name == "entry.txt"
    assert entry.mtime == pytest.approx(1_700_000_000)
    assert entry.mode == expected.st_mode
    assert entry.is_dir is False
    assert entry.is_symlink is False
    assert entry.status.st_ctime == expected.st_ctime
    assert entry.status.st_size == expected.st_size
    assert calls == 1


def test_scan_observed_elides_an_entry_that_vanishes_before_its_own_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vanished = tmp_path / "a-vanished.txt"
    survivor = tmp_path / "b-survivor.txt"
    vanished.write_text("gone")
    survivor.write_text("present")
    original_stat = os.DirEntry.stat

    def vanish_then_stat(
        entry: os.DirEntry[str],
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if Path(entry.path) == vanished:
            vanished.unlink()
        return original_stat(entry, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os.DirEntry, "stat", vanish_then_stat)

    assert [entry.path for entry in scan_observed(tmp_path)] == [survivor]


def test_scan_observed_elides_an_entry_whose_intermediate_component_becomes_a_file(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child.txt"
    child.write_text("hello")
    scan = scan_observed(parent)
    shutil.rmtree(parent)
    parent.write_text("no longer a directory")

    with pytest.raises(NotADirectoryError):
        child.lstat()
    assert list(scan) == []


def test_scan_observed_propagates_permission_error_from_an_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "entry.txt"
    target.write_text("hello")

    def raise_permission_error(
        _entry: os.DirEntry[str],
        *,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        del follow_symlinks
        raise PermissionError("injected")

    monkeypatch.setattr(os.DirEntry, "stat", raise_permission_error)

    with pytest.raises(PermissionError, match="injected"):
        list(scan_observed(tmp_path))


def test_scan_observed_raises_for_a_missing_root_at_call_time(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_observed(tmp_path / "missing")


def test_scan_observed_raises_notadirectory_for_a_file_root_at_call_time(
    tmp_path: Path,
) -> None:
    root = tmp_path / "file"
    root.write_text("not a directory")

    with pytest.raises(NotADirectoryError):
        scan_observed(root)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink support")
def test_scan_observed_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    entries = {entry.name: entry for entry in scan_observed(tmp_path)}

    assert entries["link"].is_dir is False
    assert entries["link"].is_symlink is True


def test_scan_observed_closes_its_scandir_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = _FakeScandir([_FakeDirEntry(tmp_path / "entry", _regular_file_status())])
    monkeypatch.setattr(fs_observation.os, "scandir", lambda _root: scanner)

    assert len(list(scan_observed(tmp_path))) == 1
    assert scanner.closed is True


def test_scan_observed_releases_its_handle_when_abandoned_mid_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = _FakeScandir(
        [
            _FakeDirEntry(tmp_path / "first", _regular_file_status()),
            _FakeDirEntry(tmp_path / "second", _regular_file_status()),
        ]
    )
    monkeypatch.setattr(fs_observation.os, "scandir", lambda _root: scanner)
    observed = scan_observed(tmp_path)
    assert next(observed).name == "first"

    del observed
    gc.collect()

    assert scanner.closed is True


def test_scan_observed_releases_its_handle_when_never_iterated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = _FakeScandir([])
    monkeypatch.setattr(fs_observation.os, "scandir", lambda _root: scanner)
    observed = scan_observed(tmp_path)

    del observed
    gc.collect()

    assert scanner.closed is True


def test_observe_path_mode_returns_st_mode_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "present.txt"
    target.write_text("hello")
    mode = observe_path_mode(target)
    assert mode is not None
    assert stat.S_ISREG(mode)


def test_observe_path_mode_returns_none_for_path_unlinked_before_call(tmp_path: Path) -> None:
    target = tmp_path / "vanished.txt"
    target.write_text("hello")
    target.unlink()
    assert observe_path_mode(target) is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink support")
def test_observe_path_mode_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    mode = observe_path_mode(link)

    assert mode is not None
    assert stat.S_ISLNK(mode)


def test_observe_path_mode_returns_none_when_intermediate_component_becomes_a_file(
    tmp_path: Path,
) -> None:
    """A directory replaced by a regular file mid-walk raises NotADirectoryError,
    never FileNotFoundError — this arm must be pinned explicitly, not merely caught.
    """
    parent = tmp_path / "was_a_dir"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    parent_children = list(parent.iterdir())
    assert parent_children == [candidate]

    # Replace the intermediate directory with a regular file, then observe the
    # path that used to be nested inside it.
    import shutil

    shutil.rmtree(parent)
    parent.write_text("no longer a directory")

    with pytest.raises(NotADirectoryError):
        candidate.lstat()
    assert observe_path_mode(candidate) is None


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="requires POSIX permission enforcement as non-root",
)
def test_observe_path_mode_propagates_permission_error(tmp_path: Path) -> None:
    parent = tmp_path / "locked"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    os.chmod(parent, 0)
    try:
        with pytest.raises(PermissionError):
            observe_path_mode(candidate)
    finally:
        os.chmod(parent, 0o700)


def test_safe_mtime_returns_mtime_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "present.txt"
    target.write_text("hello")
    mtime = safe_mtime(target)
    assert mtime is not None
    assert mtime == pytest.approx(target.stat().st_mtime)


def test_safe_mtime_returns_none_for_path_unlinked_before_call(tmp_path: Path) -> None:
    target = tmp_path / "vanished.txt"
    target.write_text("hello")
    target.unlink()
    assert safe_mtime(target) is None


def test_safe_mtime_returns_mtime_for_directory(tmp_path: Path) -> None:
    target = tmp_path / "directory"
    target.mkdir()

    assert safe_mtime(target) == pytest.approx(target.stat().st_mtime)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink support")
def test_safe_mtime_follows_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    assert safe_mtime(link) == pytest.approx(target.stat().st_mtime)


def test_safe_mtime_returns_none_when_intermediate_component_becomes_a_file(
    tmp_path: Path,
) -> None:
    import shutil

    parent = tmp_path / "was_a_dir"
    parent.mkdir()
    candidate = parent / "child.txt"
    candidate.write_text("hello")
    shutil.rmtree(parent)
    parent.write_text("no longer a directory")

    with pytest.raises(NotADirectoryError):
        os.path.getmtime(candidate)
    assert safe_mtime(candidate) is None
