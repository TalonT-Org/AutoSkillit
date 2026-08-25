"""First-ever direct unit tests for ``_InstallSnapshot._matches_staged_state``.

No prior behavioral coverage existed for this function anywhere in the
repository (confirmed: the only other ``tests/`` reference is a
dangerous-operation ratchet-registry entry in
``tests/infra/test_plugin_source_ratchets.py``, not behavioral coverage).
Covers the directory-comparison branch, which used to enumerate both trees
via ``Path.rglob("*")`` — silently dropping any subtree whose ``scandir()``
failed mid-walk (issue #4770).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._install_snapshot._snapshot import _InstallSnapshot
from tests._helpers import inject_vanishing_subtree_on_descent

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _seed_mirrored_dirs(root: Path) -> tuple[Path, Path]:
    current = root / "current"
    current.mkdir()
    (current / "x.txt").write_text("hello", encoding="utf-8")
    (current / "sub").mkdir()
    (current / "sub" / "y.txt").write_text("world", encoding="utf-8")

    backup = root / "backup"
    backup.mkdir()
    (backup / "x.txt").write_text("hello", encoding="utf-8")
    (backup / "sub").mkdir()
    (backup / "sub" / "y.txt").write_text("world", encoding="utf-8")
    return current, backup


class TestMatchesStagedStateDirectoryComparison:
    def test_identical_directory_trees_match(self, tmp_path: Path) -> None:
        current, backup = _seed_mirrored_dirs(tmp_path)
        assert _InstallSnapshot._matches_staged_state(current, "directory", backup) is True

    def test_differing_file_content_does_not_match(self, tmp_path: Path) -> None:
        current, backup = _seed_mirrored_dirs(tmp_path)
        (backup / "sub" / "y.txt").write_text("different", encoding="utf-8")
        assert _InstallSnapshot._matches_staged_state(current, "directory", backup) is False

    def test_extra_entry_in_backup_does_not_match(self, tmp_path: Path) -> None:
        current, backup = _seed_mirrored_dirs(tmp_path)
        (backup / "sub" / "extra.txt").write_text("extra", encoding="utf-8")
        assert _InstallSnapshot._matches_staged_state(current, "directory", backup) is False

    def test_missing_entry_in_backup_does_not_match(self, tmp_path: Path) -> None:
        current, backup = _seed_mirrored_dirs(tmp_path)
        (current / "sub" / "z.txt").write_text("extra", encoding="utf-8")
        assert _InstallSnapshot._matches_staged_state(current, "directory", backup) is False

    def test_missing_backup_returns_false(self, tmp_path: Path) -> None:
        current, _backup = _seed_mirrored_dirs(tmp_path)
        assert _InstallSnapshot._matches_staged_state(current, "directory", None) is False


class TestMatchesStagedStateRaceSafety:
    """Issue #4770 test 12: a subtree deleted mid-comparison must raise, not
    silently produce a shrunk entry set that could spuriously match."""

    def test_subtree_vanishes_before_its_own_descent_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        current, backup = _seed_mirrored_dirs(tmp_path)
        vanishing = current / "vanishing"
        vanishing.mkdir()
        (vanishing / "leaf.txt").write_text("leaf", encoding="utf-8")
        (backup / "vanishing").mkdir()
        (backup / "vanishing" / "leaf.txt").write_text("leaf", encoding="utf-8")

        inject_vanishing_subtree_on_descent(monkeypatch, vanishing)

        from autoskillit.core.io import TreeVanishedError

        with pytest.raises(TreeVanishedError):
            _InstallSnapshot._matches_staged_state(current, "directory", backup)
