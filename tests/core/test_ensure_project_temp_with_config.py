"""Tests for ensure_project_temp with configurable override."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import ensure_project_temp

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_repository_profile_artifact_is_project_gitignored() -> None:
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES

    assert "repository-profile.v1.json" in _AUTOSKILLIT_GITIGNORE_ENTRIES


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


def test_ensure_self_ignored_writes_marker_only_when_absent(tmp_path: Path) -> None:
    from autoskillit.core.io import ensure_self_ignored, is_self_ignored

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
    from autoskillit.core.io import is_self_ignored

    target = tmp_path / "runtime"
    target.mkdir()
    assert not is_self_ignored(target)
    marker = target / ".gitignore"
    marker.write_text("*.pyc\n", encoding="utf-8")
    assert not is_self_ignored(target)
    marker.write_text("# generated\n*\n", encoding="utf-8")
    assert is_self_ignored(target)


def test_is_self_ignored_rejects_symlinked_marker(tmp_path: Path) -> None:
    from autoskillit.core.io import is_self_ignored

    target = tmp_path / "runtime"
    target.mkdir()
    external_marker = tmp_path / "external-ignore"
    external_marker.write_text("*\n", encoding="utf-8")
    (target / ".gitignore").symlink_to(external_marker)
    assert not is_self_ignored(target)
