"""Contract tests: server.helpers module."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.anyio
async def test_infer_repo_from_remote_returns_empty_for_file_url(tmp_path: Path) -> None:
    """Regression guard: file:// origin, no upstream → infer_repo_from_remote returns ''.

    Creates a minimal git repo with only a file:// origin remote (no upstream).
    """
    from autoskillit.server.helpers import infer_repo_from_remote

    repo = tmp_path / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{tmp_path}/bare"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    # No upstream remote — simulates file:// origin-only scenario

    result = await infer_repo_from_remote(str(repo))
    assert result == ""
