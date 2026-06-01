"""Integration test: remove_clone_guard with clone-isolated origin topology.

Verifies that _check_sync queries the real remote (upstream) instead of the
file:// origin URL when no tracking branch is configured.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.hooks.guards.remove_clone_guard import _check_sync

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _setup_isolated_clone_with_branch(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create a bare remote + clone with origin-isolated topology and a local-only branch.

    Returns (bare_remote, clone_path, branch_name).
    """
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (source / "file.txt").write_text("content")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "init")
    _git(source, "remote", "add", "origin", str(bare))
    _git(source, "push", "-u", "origin", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(clone)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )

    _git(clone, "remote", "set-url", "origin", f"file://{clone}")
    _git(clone, "remote", "add", "upstream", str(bare))

    branch = "feat/local-only"
    _git(clone, "checkout", "-b", branch)
    (clone / "new.txt").write_text("local work")
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "local work")

    return bare, clone, branch


class TestCheckSyncIsolation:
    """_check_sync must resolve the real remote when no tracking branch exists."""

    def test_unpushed_branch_detected_via_upstream(self, tmp_path: Path) -> None:
        _bare, clone, _branch = _setup_isolated_clone_with_branch(tmp_path)

        approved, reason = _check_sync(str(clone))

        assert not approved, "Branch not pushed to upstream should be denied"
        assert "upstream" in reason, f"Deny message should reference 'upstream', got: {reason}"
        assert "origin" not in reason.split("push -u")[1] if "push -u" in reason else True, (
            f"Deny push command should not hardcode 'origin': {reason}"
        )

    def test_pushed_branch_approved_via_upstream(self, tmp_path: Path) -> None:
        _bare, clone, branch = _setup_isolated_clone_with_branch(tmp_path)

        _git(clone, "push", "upstream", branch)

        approved, _reason = _check_sync(str(clone))

        assert approved, "Branch pushed to upstream should be approved"

    def test_falls_back_to_origin_without_upstream(self, tmp_path: Path) -> None:
        """When no upstream remote exists, origin is used."""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (repo / "f.txt").write_text("x")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "init")
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "push", "-u", "origin", "HEAD")

        _git(repo, "checkout", "-b", "feat/test")
        (repo / "g.txt").write_text("y")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "work")
        _git(repo, "push", "origin", "feat/test")

        approved, _reason = _check_sync(str(repo))

        assert approved, "Branch pushed to origin (no upstream) should be approved"
