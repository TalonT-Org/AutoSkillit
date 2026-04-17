"""Tests for autoskillit.config resolve_ingredient_defaults."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.medium]


def test_resolve_ingredient_defaults_uses_upstream_when_origin_is_file_url(tmp_path):
    """resolve_ingredient_defaults must return the upstream URL when origin is file://."""
    from autoskillit.config import resolve_ingredient_defaults

    # Create repo with file:// origin and real URL upstream
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{tmp_path}/other"], cwd=str(repo), check=True
    )
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/testowner/testrepo.git"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("source_dir") == "https://github.com/testowner/testrepo.git"


def test_resolve_ingredient_defaults_still_works_with_github_origin(tmp_path):
    """Non-clone context: origin has real GitHub URL — must continue to work."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
        cwd=str(repo),
        check=True,
    )
    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("source_dir") == "https://github.com/owner/repo.git"
