"""Unit tests for execution.remote_resolver.resolve_remote_repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.execution import resolve_remote_repo

# ---------------------------------------------------------------------------
# Hint-path tests (no subprocess calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_with_full_url_hint_returns_parsed_repo() -> None:
    """Full GitHub URL hint → parse and return without subprocess calls."""
    result = await resolve_remote_repo("/any/cwd", hint="https://github.com/owner/repo.git")
    assert result == "owner/repo"


@pytest.mark.asyncio
async def test_resolve_with_owner_repo_hint_returned_as_is() -> None:
    """Already-parsed owner/repo hint → return as-is without subprocess calls."""
    result = await resolve_remote_repo("/any/cwd", hint="owner/repo")
    assert result == "owner/repo"


@pytest.mark.asyncio
async def test_resolve_with_ssh_url_hint() -> None:
    """SSH URL hint → parsed correctly."""
    result = await resolve_remote_repo("/any/cwd", hint="git@github.com:owner/repo.git")
    assert result == "owner/repo"


@pytest.mark.asyncio
async def test_resolve_with_file_url_hint_falls_through_to_remotes(tmp_path: Path) -> None:
    """file:// hint is not a valid GitHub URL — resolver falls through to subprocess."""
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", str(bare)], check=True)
    # No upstream with a GitHub URL — so file:// hint + no github upstream → None
    result = await resolve_remote_repo(str(repo), hint="file:///some/path")
    assert result is None


# ---------------------------------------------------------------------------
# Subprocess resolution tests (real git repos)
# ---------------------------------------------------------------------------


def _make_repo_with_remotes(
    tmp_path: Path,
    *,
    origin: str | None = None,
    upstream: str | None = None,
) -> Path:
    """Create a minimal git repo with the given remotes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    if origin:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
    if upstream:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "upstream", upstream], check=True)
    return repo


@pytest.mark.asyncio
async def test_resolve_tries_upstream_before_origin(tmp_path: Path) -> None:
    """Clone scenario: file:// origin + real URL upstream → returns upstream result."""
    clone_path = _make_repo_with_remotes(
        tmp_path,
        origin="file:///home/user/runs/clone-20260315/repo",
        upstream="https://github.com/testowner/testrepo.git",
    )
    result = await resolve_remote_repo(str(clone_path))
    assert result == "testowner/testrepo"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_origin_when_no_upstream(tmp_path: Path) -> None:
    """Non-clone context: only origin with real GitHub URL → returns origin result."""
    source_path = _make_repo_with_remotes(
        tmp_path,
        origin="https://github.com/testowner/sourcerepo.git",
    )
    result = await resolve_remote_repo(str(source_path))
    assert result == "testowner/sourcerepo"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_github_remote(tmp_path: Path) -> None:
    """Repo with only file:// or no remotes → None."""
    bare_repo = _make_repo_with_remotes(
        tmp_path,
        origin="file:///some/local/path",
    )
    result = await resolve_remote_repo(str(bare_repo))
    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_remotes(tmp_path: Path) -> None:
    """Repo with no remotes at all → None."""
    repo = _make_repo_with_remotes(tmp_path)
    result = await resolve_remote_repo(str(repo))
    assert result is None
