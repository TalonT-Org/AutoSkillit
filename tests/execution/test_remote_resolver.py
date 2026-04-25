"""Unit tests for execution.remote_resolver.resolve_remote_repo and resolve_remote_name."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.execution import resolve_remote_repo
from autoskillit.execution.remote_resolver import resolve_remote_name

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# ---------------------------------------------------------------------------
# Hint-path tests (no subprocess calls)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_with_full_url_hint_returns_parsed_repo() -> None:
    """Full GitHub URL hint → parse and return without subprocess calls."""
    result = await resolve_remote_repo("/any/cwd", hint="https://github.com/owner/repo.git")
    assert result == "owner/repo"


@pytest.mark.anyio
async def test_resolve_with_owner_repo_hint_returned_as_is() -> None:
    """Already-parsed owner/repo hint → return as-is without subprocess calls."""
    result = await resolve_remote_repo("/any/cwd", hint="owner/repo")
    assert result == "owner/repo"


@pytest.mark.anyio
async def test_resolve_with_ssh_url_hint() -> None:
    """SSH URL hint → parsed correctly."""
    result = await resolve_remote_repo("/any/cwd", hint="git@github.com:owner/repo.git")
    assert result == "owner/repo"


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_resolve_with_non_parseable_hint_falls_through_to_remotes(tmp_path: Path) -> None:
    """hint that is neither owner/repo format nor a parseable GitHub URL falls through."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)
    # hint is an arbitrary string — neither owner/repo nor a GitHub URL
    result = await resolve_remote_repo(str(repo), hint="not-a-url")
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


@pytest.mark.anyio
async def test_resolve_tries_upstream_before_origin(tmp_path: Path) -> None:
    """Clone scenario: file:// origin + real URL upstream → returns upstream result."""
    clone_path = _make_repo_with_remotes(
        tmp_path,
        origin="file:///home/user/runs/clone-20260315/repo",
        upstream="https://github.com/testowner/testrepo.git",
    )
    result = await resolve_remote_repo(str(clone_path))
    assert result == "testowner/testrepo"


@pytest.mark.anyio
async def test_resolve_falls_back_to_origin_when_no_upstream(tmp_path: Path) -> None:
    """Non-clone context: only origin with real GitHub URL → returns origin result."""
    source_path = _make_repo_with_remotes(
        tmp_path,
        origin="https://github.com/testowner/sourcerepo.git",
    )
    result = await resolve_remote_repo(str(source_path))
    assert result == "testowner/sourcerepo"


@pytest.mark.anyio
async def test_resolve_returns_none_when_no_github_remote(tmp_path: Path) -> None:
    """Repo with only file:// or no remotes → None."""
    bare_repo = _make_repo_with_remotes(
        tmp_path,
        origin="file:///some/local/path",
    )
    result = await resolve_remote_repo(str(bare_repo))
    assert result is None


@pytest.mark.anyio
async def test_resolve_returns_none_when_no_remotes(tmp_path: Path) -> None:
    """Repo with no remotes at all → None."""
    repo = _make_repo_with_remotes(tmp_path)
    result = await resolve_remote_repo(str(repo))
    assert result is None


def test_resolve_remote_has_timeout_in_source() -> None:
    """Static check: resolve_remote_repo uses wait_for with a timeout on subprocess calls."""
    import ast
    import inspect

    source = inspect.getsource(resolve_remote_repo)
    tree = ast.parse(source)
    has_wait_for = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait_for"
        for node in ast.walk(tree)
    )
    assert has_wait_for, "resolve_remote_repo should use asyncio.wait_for for timeout protection"


# ---------------------------------------------------------------------------
# resolve_remote_name() tests (T1)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_remote_name_upstream_exists(tmp_path: Path) -> None:
    """upstream remote with HTTPS URL → returns 'upstream'."""
    repo = _make_repo_with_remotes(
        tmp_path,
        origin="file:///local/clone/path",
        upstream="https://github.com/org/repo.git",
    )
    result = await resolve_remote_name(str(repo))
    assert result == "upstream"


@pytest.mark.anyio
async def test_resolve_remote_name_upstream_missing_falls_back_to_origin(tmp_path: Path) -> None:
    """No upstream remote → falls back to 'origin'."""
    repo = _make_repo_with_remotes(
        tmp_path,
        origin="https://github.com/org/repo.git",
    )
    result = await resolve_remote_name(str(repo))
    assert result == "origin"


@pytest.mark.anyio
async def test_resolve_remote_name_upstream_is_file_url_falls_back_to_origin(
    tmp_path: Path,
) -> None:
    """upstream has file:// URL (clone isolation) → rejected; falls back to 'origin'."""
    repo = _make_repo_with_remotes(
        tmp_path,
        origin="https://github.com/org/repo.git",
        upstream="file:///local/worktree/clone",
    )
    result = await resolve_remote_name(str(repo))
    assert result == "origin"


@pytest.mark.anyio
async def test_resolve_remote_name_both_missing_returns_origin(tmp_path: Path) -> None:
    """No remotes at all → safe default 'origin'."""
    repo = _make_repo_with_remotes(tmp_path)
    result = await resolve_remote_name(str(repo))
    assert result == "origin"


@pytest.mark.anyio
async def test_resolve_remote_name_upstream_ssh_url(tmp_path: Path) -> None:
    """upstream with SSH URL is valid (not file://) → returns 'upstream'."""
    repo = _make_repo_with_remotes(
        tmp_path,
        origin="file:///local/clone",
        upstream="git@github.com:org/repo.git",
    )
    result = await resolve_remote_name(str(repo))
    assert result == "upstream"
