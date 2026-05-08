"""Tests for ensure_project_temp with configurable override."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import ensure_project_temp

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_ensure_project_temp_default_writes_self_gitignore(tmp_path: Path) -> None:
    result = ensure_project_temp(tmp_path)
    assert result == tmp_path / ".autoskillit" / "temp"
    gitignore = result / ".gitignore"
    assert gitignore.exists()
    assert "*" in gitignore.read_text()
    assert not (tmp_path / ".gitignore").exists()


def test_ensure_project_temp_relative_override_writes_self_gitignore(
    tmp_path: Path,
) -> None:
    result = ensure_project_temp(tmp_path, override="build/temp")
    assert result == tmp_path / "build" / "temp"
    gitignore = result / ".gitignore"
    assert gitignore.exists()
    assert "*" in gitignore.read_text()
    assert not (tmp_path / ".gitignore").exists()


def test_ensure_project_temp_absolute_override_writes_self_gitignore(
    tmp_path: Path,
) -> None:
    abs_target = tmp_path / "external" / "scratch"
    result = ensure_project_temp(tmp_path, override=str(abs_target))
    assert result == abs_target
    gitignore = result / ".gitignore"
    assert gitignore.exists()
    assert "*" in gitignore.read_text()
    assert not (tmp_path / ".gitignore").exists()


def test_ensure_project_temp_gitignore_written_before_other_content(
    tmp_path: Path,
) -> None:
    """The self-gitignore must exist immediately after ensure_project_temp returns,
    even though no session content has been written yet."""
    result = ensure_project_temp(tmp_path)
    children = sorted(p.name for p in result.iterdir())
    assert children == [".gitignore"], (
        f"only .gitignore should exist after ensure_project_temp: {children}"
    )


def test_ensure_project_temp_idempotent_on_existing_gitignore(tmp_path: Path) -> None:
    target = tmp_path / ".autoskillit" / "temp"
    target.mkdir(parents=True)
    custom = "# custom user content\n*\n!keep.txt\n"
    (target / ".gitignore").write_text(custom)

    ensure_project_temp(tmp_path)
    ensure_project_temp(tmp_path)

    assert (target / ".gitignore").read_text() == custom
