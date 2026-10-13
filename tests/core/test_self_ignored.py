"""Tests for self-ignore markers in runtime directories."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import ensure_self_ignored, is_self_ignored

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_ensure_self_ignored_writes_marker_only_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "runtime"
    target.mkdir()
    marker = target / ".gitignore"
    ensure_self_ignored(target)
    assert marker.is_file() and is_self_ignored(target)
    custom = "# custom user content\n*\n!keep.txt\n"
    marker.write_text(custom, encoding="utf-8")

    ensure_self_ignored(target)

    assert marker.read_text(encoding="utf-8") == custom


def test_is_self_ignored_requires_a_star_line(tmp_path: Path) -> None:
    target = tmp_path / "runtime"
    target.mkdir()
    assert not is_self_ignored(target)
    marker = target / ".gitignore"
    marker.write_text("*.pyc\n", encoding="utf-8")
    assert not is_self_ignored(target)
    marker.write_text("# generated\n*\n", encoding="utf-8")
    assert is_self_ignored(target)


def test_is_self_ignored_rejects_symlinked_marker(tmp_path: Path) -> None:
    target = tmp_path / "runtime"
    target.mkdir()
    external_marker = tmp_path / "external-ignore"
    external_marker.write_text("*\n", encoding="utf-8")
    (target / ".gitignore").symlink_to(external_marker)
    assert not is_self_ignored(target)
