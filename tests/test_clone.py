"""Tests for autoskillit.workspace.clone module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.workspace.clone import clone_repo, push_to_remote, remove_clone


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one empty commit."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return tmp_path


class TestCloneRepo:
    def test_success_creates_sibling_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
        clone_path = Path(result["clone_path"])
        assert clone_path.is_dir()
        assert (clone_path / ".git").is_dir()
        # Cleanup
        import shutil

        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_success_returns_expected_keys(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
        assert "clone_path" in result
        assert "source_dir" in result
        # source_dir matches resolved git_repo
        assert result["source_dir"] == str(git_repo.resolve())
        import shutil

        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_clone_path_name_format(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "myrun")
        clone_path = Path(result["clone_path"])
        # Pattern: myrun-YYYYMMDD-HHMMSS-ffffff
        assert re.match(r"myrun-\d{8}-\d{6}-\d{6}$", clone_path.name), clone_path.name
        import shutil

        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_in_autoskillit_runs_sibling(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
        clone_path = Path(result["clone_path"])
        # Parent must be ../autoskillit-runs relative to source
        expected_parent = git_repo.parent / "autoskillit-runs"
        assert clone_path.parent == expected_parent
        import shutil

        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_invalid_source_dir_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="source_dir does not exist"):
            clone_repo("/nonexistent/path/that/does/not/exist", "name")

    def test_non_git_dir_raises_runtime_error(self, tmp_path: Path) -> None:
        # tmp_path exists but is not a git repo
        with pytest.raises(RuntimeError, match="git clone failed"):
            clone_repo(str(tmp_path), "name")


class TestRemoveClone:
    def test_keep_false_removes_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
        clone_path = result["clone_path"]
        remove_result = remove_clone(clone_path, keep="false")
        assert remove_result == {"removed": "true"}
        assert not Path(clone_path).exists()
        # Cleanup parent runs dir
        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_keep_true_preserves_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
        clone_path = result["clone_path"]
        remove_result = remove_clone(clone_path, keep="true")
        assert remove_result == {"removed": "false", "reason": "keep=true"}
        assert Path(clone_path).exists()
        # Cleanup
        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_missing_path_returns_not_found(self) -> None:
        result = remove_clone("/nonexistent/clone/path", keep="false")
        assert result == {"removed": "false", "reason": "not_found"}

    def test_missing_path_does_not_raise(self) -> None:
        # Must not raise even for a completely bogus path
        remove_clone("/no/such/path/anywhere", keep="false")


class TestPushToRemote:
    def test_push_to_remote_propagates_to_upstream(self, tmp_path: Path) -> None:
        # 1. Create bare remote (simulates GitHub)
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

        # 2. Clone remote into source (simulates user's local checkout)
        source = tmp_path / "source"
        subprocess.run(["git", "clone", str(remote), str(source)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        src_branch = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(source), "push", "origin", src_branch],
            check=True,
            capture_output=True,
        )

        # 3. Pipeline clones source
        clone_result = clone_repo(str(source), "pushtest")
        clone_path = clone_result["clone_path"]

        # 4. Make a commit in pipeline-clone
        subprocess.run(
            ["git", "-C", clone_path, "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", clone_path, "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", clone_path, "commit", "--allow-empty", "-m", "pipeline-commit"],
            check=True,
            capture_output=True,
        )
        branch = subprocess.run(
            ["git", "-C", clone_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        # Record source HEAD before push (must not change)
        source_head_before = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        result = push_to_remote(clone_path, str(source), branch)
        assert result["success"] == "true"

        # Commit landed in remote
        remote_log = subprocess.run(
            ["git", "-C", str(remote), "log", "--oneline", "-3"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "pipeline-commit" in remote_log

        # source_dir HEAD unchanged
        source_head_after = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert source_head_before == source_head_after

        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_push_to_remote_fails_when_source_has_no_origin(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)

        result = push_to_remote("/nonexistent/clone", str(source), "main")
        assert result["success"] == "false"
        assert len(result["stderr"]) > 0

    def test_push_to_remote_does_not_raise(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        push_to_remote("/no/such/clone", str(source), "main")  # must not raise
