"""Workspace test fixtures."""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture(scope="session")
def clone_isolation_repo(tmp_path_factory):
    """
    Creates a clone-isolated git repo mirroring what clone_repo produces:
    - origin → file://{clone_path}  (isolation)
    - upstream → https://github.com/testowner/testrepo.git  (real remote)

    Session-scoped to avoid repeated git subprocess calls per test.
    Use shutil.copytree in tests that mutate remotes.
    """
    base = tmp_path_factory.mktemp("clone_isolation")
    repo = base / "clone"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{base}/other"], cwd=str(repo), check=True
    )
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/testowner/testrepo.git"],
        cwd=str(repo),
        check=True,
    )
    return repo
