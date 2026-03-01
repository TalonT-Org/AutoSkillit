"""Tests for autoskillit.workspace.clone module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.workspace.clone import (
    clone_repo,
    push_to_remote,
    remove_clone,
)


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

    def test_cb1_explicit_branch_is_checked_out_in_clone(self, git_repo: Path) -> None:
        """T_CB1: explicit branch is checked out in the clone."""
        import shutil

        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "-b", "dev"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "--allow-empty", "-m", "dev-commit"],
            check=True,
            capture_output=True,
        )
        result = clone_repo(str(git_repo), "test", branch="dev")
        clone_path = Path(result["clone_path"])
        head = subprocess.run(
            ["git", "-C", str(clone_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == "dev"
        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_cb2_auto_detects_current_branch_when_branch_empty(self, git_repo: Path) -> None:
        """T_CB2: branch="" auto-detects current HEAD branch and clones it."""
        import shutil

        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "-b", "feature"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "--allow-empty", "-m", "feature-commit"],
            check=True,
            capture_output=True,
        )
        result = clone_repo(str(git_repo), "test", branch="")
        clone_path = Path(result["clone_path"])
        head = subprocess.run(
            ["git", "-C", str(clone_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == "feature"
        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_cb3_returns_uncommitted_changes_warning_when_dirty(self, git_repo: Path) -> None:
        """T_CB3: untracked files trigger warning dict; no clone is created."""
        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test")
        assert result["uncommitted_changes"] == "true"
        assert "changed_files" in result
        assert "clone_path" not in result

    def test_cb4_strategy_proceed_skips_uncommitted_check(self, git_repo: Path) -> None:
        """T_CB4: strategy='proceed' clones without uncommitted changes check."""
        import shutil

        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test", strategy="proceed")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert not (clone_path / "untracked.txt").exists()
        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_cb5_strategy_clone_local_includes_uncommitted_changes(self, git_repo: Path) -> None:
        """T_CB5: strategy='clone_local' copytree includes untracked files."""
        import shutil

        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert (clone_path / "untracked.txt").exists()
        shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_cb6_detached_head_falls_back_to_no_branch_flag(self, git_repo: Path) -> None:
        """T_CB6: detached HEAD yields branch=''; git clone succeeds without --branch."""
        import shutil

        sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", sha],
            check=True,
            capture_output=True,
        )
        result = clone_repo(str(git_repo), "test", branch="")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert clone_path.is_dir()
        shutil.rmtree(clone_path.parent, ignore_errors=True)


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
        assert result["success"] is True

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
        assert result["success"] is False
        assert len(result["stderr"]) > 0

    def test_push_to_remote_does_not_raise(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        push_to_remote("/no/such/clone", str(source), "main")  # must not raise


class TestPushToRemoteNonBare:
    """push_to_remote fails with error_type when remote is a local non-bare repo."""

    def test_push_fails_with_local_nonbare_remote(self, tmp_path: Path) -> None:
        """push_to_remote returns error_type=local_non_bare_remote for non-bare local origin."""
        # upstream is a non-bare local repo with main checked out
        upstream = tmp_path / "upstream"
        subprocess.run(["git", "init", str(upstream)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(upstream), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(upstream), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(upstream), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )

        source = tmp_path / "source"
        subprocess.run(
            ["git", "clone", str(upstream), str(source)], check=True, capture_output=True
        )
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
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "src"],
            check=True,
            capture_output=True,
        )

        # upstream has main checked out — push from source will be refused
        clone_result = clone_repo(str(source), "test-nonbare", strategy="proceed")
        clone_path = clone_result["clone_path"]
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
            ["git", "-C", clone_path, "commit", "--allow-empty", "-m", "pipeline"],
            check=True,
            capture_output=True,
        )

        result = push_to_remote(clone_path, str(source), "main")

        assert result["success"] is False
        assert result.get("error_type") == "local_non_bare_remote"

        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_clone_repo_returns_remote_url(self, tmp_path: Path) -> None:
        """clone_repo result dict includes remote_url field."""
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
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

        result = clone_repo(str(source), "test-remoteurl", strategy="proceed")

        assert "remote_url" in result
        assert str(remote) in result["remote_url"]

        import shutil

        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_clone_repo_returns_empty_remote_url_when_no_origin(self, tmp_path: Path) -> None:
        """clone_repo returns remote_url='' when source_dir has no remote configured."""
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
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

        result = clone_repo(str(source), "test-noremote", strategy="proceed")

        assert "remote_url" in result
        assert result["remote_url"] == ""

        import shutil

        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_push_to_remote_uses_explicit_remote_url_without_reading_source_dir(
        self, tmp_path: Path
    ) -> None:
        """When remote_url is explicit, source_dir is not accessed for URL lookup."""
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
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
        subprocess.run(
            ["git", "-C", str(source), "push", "origin", "main"],
            check=True,
            capture_output=True,
        )

        clone_result = clone_repo(str(source), "test-explicit", strategy="proceed")
        clone_path = clone_result["clone_path"]
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
            ["git", "-C", clone_path, "commit", "--allow-empty", "-m", "impl"],
            check=True,
            capture_output=True,
        )

        # Pass explicit remote_url — source_dir is not needed
        result = push_to_remote(clone_path, remote_url=str(remote), branch="main")

        assert result["success"] is True

        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)
