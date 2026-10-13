"""Tests for ensure_project_temp with configurable override."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core.io import (
    _AUTOSKILLIT_GITIGNORE_ENTRIES,
    ensure_project_gitignore,
    ensure_project_temp,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def test_repository_profile_artifact_is_project_gitignored() -> None:
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


@pytest.mark.parametrize("existing", [None, "# keep this comment\ncustom/\n"])
def test_ensure_project_temp_leaves_project_gitignore_untouched(
    tmp_path: Path, existing: str | None
) -> None:
    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    gitignore = autoskillit_dir / ".gitignore"
    if existing is not None:
        gitignore.write_text(existing, encoding="utf-8")
    root_gitignore = tmp_path / ".gitignore"
    root_gitignore.write_text("root rules\n", encoding="utf-8")

    ensure_project_temp(tmp_path)

    actual = gitignore.read_text(encoding="utf-8") if gitignore.exists() else None
    assert actual == existing
    assert root_gitignore.read_text(encoding="utf-8") == "root rules\n"


def test_ensure_project_gitignore_preserves_rules_and_is_idempotent(tmp_path: Path) -> None:
    ensure_project_gitignore(tmp_path)
    gitignore = tmp_path / ".autoskillit" / ".gitignore"
    assert set(_AUTOSKILLIT_GITIGNORE_ENTRIES).issubset(gitignore.read_text().splitlines())

    gitignore.write_text("# user comment\ncustom/\ntemp/\n", encoding="utf-8")
    ensure_project_gitignore(tmp_path)
    content = gitignore.read_text(encoding="utf-8")
    assert content.startswith("# user comment\ncustom/\ntemp/\n")
    assert set(_AUTOSKILLIT_GITIGNORE_ENTRIES).issubset(content.splitlines())

    ensure_project_gitignore(tmp_path)
    assert gitignore.read_text(encoding="utf-8") == content


def test_runtime_state_is_ignored_in_a_git_checkout(tmp_path: Path) -> None:
    from tests._git_topology import commit_file, init_checkout

    repo = init_checkout(tmp_path / "repo")
    ensure_project_gitignore(repo)
    gitignore = repo / ".autoskillit" / ".gitignore"
    commit_file(
        repo,
        ".autoskillit/.gitignore",
        gitignore.read_text(),
        "ignore runtime state",
    )
    (gitignore.parent / ".state.yaml").write_text("runtime: true\n", encoding="utf-8")

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert status.stdout == ""
