"""Cross-boundary contract tests: clone isolation × CI/merge-queue resolution.

These tests cross the boundary between clone_repo (workspace/) and
resolve_remote_repo (execution/) to verify that the resolver correctly
uses the upstream remote (the real GitHub URL) rather than the file://
origin set by clone isolation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autoskillit.execution import resolve_remote_repo
from autoskillit.execution.ci import _parse_repo_from_remote
from autoskillit.workspace import clone_repo


# ---------------------------------------------------------------------------
# Explicit contract documentation
# ---------------------------------------------------------------------------


def test_parse_repo_from_file_url_returns_none() -> None:
    """Documents the explicit contract — file:// → None."""
    assert _parse_repo_from_remote("file:///home/user/run-123/repo") is None


# ---------------------------------------------------------------------------
# Primary cross-boundary integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_remote_repo_after_clone_uses_upstream(tmp_path: Path) -> None:
    """
    clone_repo sets file:// origin and upstream=real_url.
    resolve_remote_repo must return owner/repo from upstream, not fail on file://.

    Primary cross-boundary integration test.
    """
    # Set up a bare remote with a github-like URL
    bare_remote = tmp_path / "bare.git"
    bare_remote.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(bare_remote)], check=True
    )

    source = tmp_path / "source"
    subprocess.run(["git", "clone", str(bare_remote), str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t.com"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "T"], check=True)
    (source / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], check=True)
    src_branch = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(source), "push", "origin", src_branch], check=True)

    # Pass remote_url to clone_repo so it sets upstream to the GitHub URL while
    # cloning from the local bare repo (which exists). This mirrors production use
    # where the recipe provides remote_url from a previous step's context.
    github_url = "https://github.com/testowner/testrepo.git"
    result = clone_repo(str(source), "contract-ci-test", remote_url=github_url)
    clone_path = result["clone_path"]

    try:
        resolved = await resolve_remote_repo(clone_path)
        assert resolved == "testowner/testrepo"
    finally:
        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression guard: infer_repo_from_remote with file:// origin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_infer_repo_from_remote_returns_empty_for_file_url(tmp_path: Path) -> None:
    """Regression guard: file:// origin, no upstream → infer_repo_from_remote returns ''."""
    from autoskillit.server.helpers import infer_repo_from_remote

    # Repo with only file:// origin, no upstream
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo_path)], check=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "remote", "add", "origin", "file:///some/local/path"],
        check=True,
    )

    result = await infer_repo_from_remote(str(repo_path))
    assert result == ""
