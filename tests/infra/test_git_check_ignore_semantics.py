"""Real-Git semantics that justify protected-path ``check-ignore`` admission."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _run_git(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_check_ignore_reports_only_ignored_paths_in_real_repository(tmp_path: Path) -> None:
    """``check-ignore -v`` returns ignore metadata, not protected file content."""
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg-config"
    home.mkdir()
    xdg_config_home.mkdir()
    git_env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    version_result = _run_git(repo, git_env, "--version")
    git_version = version_result.stdout.strip() or "<unavailable>"
    version_context = (
        f"{git_version}: git version command failed: rc={version_result.returncode}, "
        f"stdout={version_result.stdout!r}, stderr={version_result.stderr!r}"
    )
    assert version_result.returncode == 0, version_context

    init_result = _run_git(repo, git_env, "init", "-q")
    init_context = (
        f"{git_version}: git init failed: rc={init_result.returncode}, "
        f"stdout={init_result.stdout!r}, stderr={init_result.stderr!r}"
    )
    assert init_result.returncode == 0, init_context

    ignored = repo / "ignored-recipe.yaml"
    tracked_not_ignored = repo / "tracked-recipe.yaml"
    untracked_not_ignored = repo / "untracked-recipe.yaml"
    nonexistent = "missing-recipe.yaml"
    ignored.write_text("protected content must never appear in check-ignore output\n")
    tracked_not_ignored.write_text("tracked protected content\n")
    untracked_not_ignored.write_text("untracked protected content\n")
    (repo / ".gitignore").write_text("ignored-recipe.yaml\n")

    add_result = _run_git(repo, git_env, "add", "tracked-recipe.yaml")
    add_context = (
        f"{git_version}: git add failed: rc={add_result.returncode}, "
        f"stdout={add_result.stdout!r}, stderr={add_result.stderr!r}"
    )
    assert add_result.returncode == 0, add_context

    def assert_check_ignore(
        *paths: str,
        expected_returncode: int,
        expected_lines: list[str],
    ) -> None:
        result = _run_git(repo, git_env, "check-ignore", "-v", "--", *paths)
        context = (
            f"{git_version}: git check-ignore -v -- {paths!r}: "
            f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        assert result.returncode == expected_returncode, context
        assert result.stdout.splitlines() == expected_lines, context

    ignored_line = ".gitignore:1:ignored-recipe.yaml\tignored-recipe.yaml"
    assert_check_ignore(
        "ignored-recipe.yaml",
        expected_returncode=0,
        expected_lines=[ignored_line],
    )
    assert_check_ignore(
        "tracked-recipe.yaml",
        expected_returncode=1,
        expected_lines=[],
    )
    assert_check_ignore(
        "untracked-recipe.yaml",
        expected_returncode=1,
        expected_lines=[],
    )
    assert_check_ignore(
        nonexistent,
        expected_returncode=1,
        expected_lines=[],
    )
    assert_check_ignore(
        "ignored-recipe.yaml",
        "tracked-recipe.yaml",
        "untracked-recipe.yaml",
        nonexistent,
        expected_returncode=0,
        expected_lines=[ignored_line],
    )
