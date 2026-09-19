"""Cross-boundary contract tests: clone isolation × CI/merge-queue resolution.

These tests cross the boundary between clone_repo (workspace/) and
resolve_remote_repo (execution/) to verify that the resolver correctly
uses the upstream remote (the real GitHub URL) rather than the file://
origin set by clone isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.execution import resolve_remote_repo
from autoskillit.workspace import clone_repo

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


def _remote_url(repository: str, name: str) -> str:
    """Return a configured remote URL from a real Git repository."""
    return subprocess.run(
        ["git", "-C", repository, "remote", "get-url", name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.anyio
async def test_resolve_remote_repo_after_clone_uses_upstream(local_with_remote: Path) -> None:
    """
    clone_repo sets file:// origin and upstream=real_url.
    resolve_remote_repo must return owner/repo from upstream, not fail on file://.

    Primary cross-boundary integration test.
    """
    github_url = "https://github.com/testowner/testrepo.git"
    result = clone_repo(
        str(local_with_remote),
        "contract-ci-test",
        branch="main",
        remote_url=github_url,
    )
    clone_path = result["clone_path"]

    assert _remote_url(clone_path, "origin") == f"file://{clone_path}"
    assert _remote_url(clone_path, "upstream") == github_url
    assert await resolve_remote_repo(clone_path) == "testowner/testrepo"


@pytest.mark.anyio
async def test_resolve_remote_repo_after_local_only_clone_returns_none(git_repo: Path) -> None:
    """A local-only clone must not be interpreted as GitHub-backed."""
    result = clone_repo(str(git_repo), "contract-ci-local-only", strategy="clone_local")
    clone_path = result["clone_path"]

    assert _remote_url(clone_path, "origin") == f"file://{clone_path}"
    upstream = subprocess.run(
        ["git", "-C", clone_path, "remote", "get-url", "upstream"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert upstream.returncode != 0
    assert await resolve_remote_repo(clone_path) is None
