"""Tests for autoskillit.workspace.clone module."""

from __future__ import annotations

import inspect
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from autoskillit.workspace.clone import (
    DefaultCloneManager,
    classify_remote_url,
    clone_repo,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
    push_to_remote,
    remove_clone,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """Create a bare git remote (simulates GitHub/origin)."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


@pytest.fixture
def local_with_remote(tmp_path: Path, bare_remote: Path) -> Path:
    """Local repo with origin configured, main pushed, feature/local-only unpublished."""
    local = tmp_path / "local"
    local.mkdir()
    subprocess.run(["git", "init", str(local)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "remote", "add", "origin", str(bare_remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    # Create local-only branch (never pushed to origin)
    subprocess.run(
        ["git", "-C", str(local), "checkout", "-b", "feature/local-only"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "commit", "--allow-empty", "-m", "local"],
        check=True,
        capture_output=True,
    )
    return local


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one empty commit.

    Returns tmp_path / 'repo' (a subdirectory) so that clone_repo output lands at
    tmp_path / 'autoskillit-runs' — inside the test's isolated tmp_path boundary.
    """
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
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


class TestCloneRepo:
    def test_success_result_and_directory_layout(self, git_repo: Path) -> None:
        """Clone creates a sibling directory with expected keys, parent, and git repo."""
        result = clone_repo(str(git_repo), "myrun", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        assert clone_path.is_dir()
        assert (clone_path / ".git").is_dir()
        assert "clone_path" in result
        assert "source_dir" in result
        assert "remote_url" in result
        assert result["source_dir"] == str(git_repo.resolve())
        expected_parent = git_repo.parent / "autoskillit-runs"
        assert clone_path.parent == expected_parent

    def test_clone_path_name_format(self, git_repo: Path) -> None:
        """Clone directory name follows run_name-YYYYMMDD-HHMMSS-ffffff format."""
        result = clone_repo(str(git_repo), "myrun", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        assert re.match(r"myrun-\d{8}-\d{6}-\d{6}$", clone_path.name), clone_path.name

    def test_invalid_source_dir_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="source_dir does not exist"):
            clone_repo("/nonexistent/path/that/does/not/exist", "name")

    def test_non_git_dir_raises_runtime_error(self, tmp_path: Path) -> None:
        # tmp_path exists but is not a git repo — probe fails with reason="error"
        with pytest.raises(RuntimeError, match="clone_origin_probe_failed"):
            clone_repo(str(tmp_path), "name")

    def test_cb1_explicit_branch_is_checked_out_in_clone(self, git_repo: Path) -> None:
        """T_CB1: explicit branch is checked out in the clone."""
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
        result = clone_repo(str(git_repo), "test", branch="dev", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        head = subprocess.run(
            ["git", "-C", str(clone_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == "dev"

    def test_cb2_auto_detects_current_branch_when_branch_empty(self, git_repo: Path) -> None:
        """T_CB2: branch="" auto-detects current HEAD branch and clones it."""
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
        result = clone_repo(str(git_repo), "test", branch="", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        head = subprocess.run(
            ["git", "-C", str(clone_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head == "feature"

    def test_cb3_returns_uncommitted_changes_warning_when_dirty(self, git_repo: Path) -> None:
        """T_CB3: untracked files trigger warning dict; no clone is created."""
        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test")
        assert result["uncommitted_changes"] == "true"
        assert "changed_files" in result
        assert "clone_path" not in result

    def test_cb4_strategy_proceed_skips_uncommitted_check(self, local_with_remote: Path) -> None:
        """T_CB4: strategy='proceed' clones without uncommitted changes check."""
        (local_with_remote / "untracked.txt").write_text("dirty")
        result = clone_repo(str(local_with_remote), "test", branch="main", strategy="proceed")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert not (clone_path / "untracked.txt").exists()

    def test_cb5_strategy_clone_local_includes_uncommitted_changes(self, git_repo: Path) -> None:
        """T_CB5: strategy='clone_local' copytree includes untracked files."""
        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert (clone_path / "untracked.txt").exists()

    def test_cb6_detached_head_falls_back_to_no_branch_flag(self, git_repo: Path) -> None:
        """T_CB6: detached HEAD yields branch=''; git clone succeeds without --branch."""
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
        result = clone_repo(str(git_repo), "test", branch="", strategy="clone_local")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert clone_path.is_dir()

    def test_returns_unpublished_branch_warning_when_not_on_remote(
        self, local_with_remote: Path
    ) -> None:
        """clone_repo returns unpublished_branch warning dict when branch has no origin ref."""
        result = clone_repo(str(local_with_remote), "test-run", branch="feature/local-only")
        assert result.get("unpublished_branch") == "true"
        assert result.get("branch") == "feature/local-only"
        assert result.get("source_dir") == str(local_with_remote)
        assert "clone_path" not in result  # sentinel: no clone was created

    def test_published_branch_proceeds_to_clone(self, local_with_remote: Path) -> None:
        """clone_repo clones normally when branch is on origin."""
        result = clone_repo(str(local_with_remote), "test-run", branch="main")
        assert "clone_path" in result
        assert "unpublished_branch" not in result

    def test_strategy_proceed_with_unpublished_branch_fails_from_remote(
        self, local_with_remote: Path
    ) -> None:
        """strategy='proceed' with unpublished branch now fails — remote is always used.

        Previously 'proceed' bypassed the guard by silently falling back to the
        local path. After the fix the clone always targets the remote, so a branch
        that doesn't exist on the remote causes git clone to fail.
        """
        with pytest.raises(RuntimeError, match="git clone failed"):
            clone_repo(
                str(local_with_remote),
                "test-run",
                branch="feature/local-only",
                strategy="proceed",
            )


def test_clone_output_stays_within_test_isolation_boundary(tmp_path: Path, git_repo: Path) -> None:
    """clone_repo output must be a descendant of tmp_path, never its parent.

    Fails when the git_repo fixture returns tmp_path itself, because clone_repo
    places autoskillit-runs/ at source.parent = tmp_path.parent (worker-shared).
    Passes once git_repo returns tmp_path / 'repo' (a subdirectory).
    """
    result = clone_repo(str(git_repo), "isolation-check", strategy="clone_local")
    clone_path = Path(result["clone_path"])
    assert clone_path.is_relative_to(tmp_path), (
        f"clone_repo placed output at {clone_path!r}, which is outside "
        f"the test's tmp_path {tmp_path!r}.\n"
        "The git_repo fixture must return a SUBDIRECTORY of tmp_path "
        "(e.g. tmp_path / 'repo'), not tmp_path itself. "
        "When git_repo is tmp_path, clone destination is tmp_path.parent — "
        "a directory shared across all tests in the same xdist worker."
    )
    shutil.rmtree(clone_path.parent, ignore_errors=True)


class TestCloneOriginContract:
    """Contract: clone's origin is a unique file:// URL; real URL is in the upstream remote."""

    def test_clone_origin_is_file_url_and_upstream_holds_real_url(self, tmp_path: Path) -> None:
        """Clone's origin must be a file:// URL (not source_dir or bare remote).

        After the fix: clone's origin = file://<clone_path>, upstream = real network URL.
        This prevents Claude Code from aliasing the clone session to the source project.
        """
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

        result = clone_repo(str(source), "contract-test")
        clone_path = Path(result["clone_path"])
        remote_url = result["remote_url"]

        try:
            origin_in_clone = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            upstream_in_clone = subprocess.run(
                ["git", "remote", "get-url", "upstream"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            # origin is now a local file URL unique to this clone
            assert origin_in_clone.startswith("file://"), (
                f"Clone's origin ({origin_in_clone!r}) must be a file:// URL"
            )
            assert origin_in_clone != str(source), (
                "Clone's origin must not be the local source_dir path"
            )
            # upstream holds the real push target
            assert upstream_in_clone == remote_url, (
                f"Clone's upstream ({upstream_in_clone!r}) should equal"
                f" remote_url ({remote_url!r})"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_local_strategy_rewrites_origin_to_file_url(self, tmp_path: Path) -> None:
        """clone_local strategy (copytree) rewrites origin to file:// unconditionally.

        Covers the #377 compounding regression: the isolation rewrite was previously
        skipped when effective_url was empty (no remote origin). After the fix,
        _ensure_origin_isolated fires unconditionally for every successful clone.
        """
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "T"], check=True)
        (source / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], check=True)

        result = clone_repo(str(source), "no-upstream-test", strategy="clone_local")
        try:
            clone_path = Path(result["clone_path"])
            origin_in_clone = subprocess.run(
                ["git", "-C", str(clone_path), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert origin_in_clone == f"file://{clone_path}", (
                f"Expected file://{clone_path!r}, got {origin_in_clone!r}"
            )
        finally:
            shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_clone_local_strategy_also_sets_correct_remotes(self, tmp_path: Path) -> None:
        """clone_local strategy (copytree) must also set origin=file:// and upstream=real URL.

        The rewrite applies to both the proceed (git clone) and clone_local (copytree) paths.
        """
        bare_remote = tmp_path / "bare.git"
        bare_remote.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare_remote)], check=True)
        source = tmp_path / "source"
        subprocess.run(["git", "clone", str(bare_remote), str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "T"], check=True)
        (source / "README.md").write_text("hello")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], check=True)

        result = clone_repo(str(source), "clone-local-contract", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        remote_url = result["remote_url"]

        try:
            origin_in_clone = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            upstream_in_clone = subprocess.run(
                ["git", "remote", "get-url", "upstream"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            assert origin_in_clone.startswith("file://"), (
                f"clone_local origin ({origin_in_clone!r}) must be a file:// URL"
            )
            assert upstream_in_clone == remote_url, (
                f"clone_local upstream ({upstream_in_clone!r}) should equal"
                f" remote_url ({remote_url!r})"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)


class TestCloneRepoRemoteUrlOverride:
    """Tests for the remote_url parameter on clone_repo (T_RU1, T_RU2)."""

    def _make_source_with_bare_remote(self, tmp_path: Path) -> tuple[Path, Path]:
        """Helper: create a bare remote and source repo pointing to it."""
        bare = tmp_path / "bare.git"
        bare.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

        source = tmp_path / "source"
        subprocess.run(["git", "clone", str(bare), str(source)], check=True, capture_output=True)
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
        (source / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-m", "init"],
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
        return source, bare

    def test_clone_repo_remote_url_override_applied(self, tmp_path: Path) -> None:
        """When remote_url is provided, clone's upstream is set to that URL (T_RU1).

        After the fix: origin = file://<clone_path>, upstream = override_url.
        """
        source, _bare = self._make_source_with_bare_remote(tmp_path)
        override_url = "https://github.com/example/repo.git"

        result = clone_repo(str(source), "test-run", strategy="proceed", remote_url=override_url)
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])

        try:
            actual_origin = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            ).stdout.strip()
            actual_upstream = subprocess.run(
                ["git", "remote", "get-url", "upstream"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            ).stdout.strip()
            # origin is now a local file URL unique to this clone
            assert actual_origin.startswith("file://")
            # upstream holds the real push target (the override URL)
            assert actual_upstream == override_url
            assert result["remote_url"] == override_url
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_repo_without_remote_url_uses_detected(self, tmp_path: Path) -> None:
        """Without remote_url, clone's upstream is set to the source's detected origin (T_RU2).

        After the fix: origin = file://<clone_path>, upstream = detected bare remote path.
        """
        source, bare = self._make_source_with_bare_remote(tmp_path)

        result = clone_repo(str(source), "test-run", strategy="proceed")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])

        try:
            actual_origin = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            ).stdout.strip()
            actual_upstream = subprocess.run(
                ["git", "remote", "get-url", "upstream"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            ).stdout.strip()
            # origin is now a local file URL unique to this clone
            assert actual_origin.startswith("file://")
            # upstream holds the real push target (the detected bare remote)
            assert actual_upstream == str(bare)
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)


class TestRemoveClone:
    def test_keep_false_removes_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        clone_path = result["clone_path"]
        remove_result = remove_clone(clone_path, keep="false")
        assert remove_result == {"removed": "true"}
        assert not Path(clone_path).exists()
        # Cleanup parent runs dir
        import shutil

        shutil.rmtree(Path(clone_path), ignore_errors=True)

    def test_keep_true_preserves_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        clone_path = result["clone_path"]
        remove_result = remove_clone(clone_path, keep="true")
        assert remove_result == {"removed": "false", "reason": "keep=true"}
        assert Path(clone_path).exists()
        # Cleanup
        import shutil

        shutil.rmtree(Path(clone_path), ignore_errors=True)

    def test_missing_path_returns_not_found(self) -> None:
        result = remove_clone("/nonexistent/clone/path", keep="false")
        assert result == {"removed": "false", "reason": "not_found"}


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

        result = push_to_remote(clone_path, str(source), branch, protected_branches=[])
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

    def test_push_to_remote_establishes_tracking_ref(self, tmp_path: Path) -> None:
        """push_to_remote must establish a tracking ref so remove_clone_guard passes."""
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

        clone_result = clone_repo(str(source), "tracktest")
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
            ["git", "-C", clone_path, "commit", "--allow-empty", "-m", "pipeline-commit"],
            check=True,
            capture_output=True,
        )

        result = push_to_remote(clone_path, str(source), src_branch, protected_branches=[])
        assert result["success"] is True

        upstream_rc = subprocess.run(
            ["git", "-C", clone_path, "rev-parse", "@{upstream}"],
            capture_output=True,
            text=True,
        ).returncode
        assert upstream_rc == 0, "@{upstream} must be set after push_to_remote"
        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_push_to_remote_fails_when_source_has_no_origin(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)

        result = push_to_remote("/nonexistent/clone", str(source), "main", protected_branches=[])
        assert result["success"] is False
        assert len(result["stderr"]) > 0


class TestDetectSourceDir:
    def test_ds1_returns_git_toplevel(self) -> None:
        """T_DS1: returns git rev-parse --show-toplevel when returncode=0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/repo/root\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/repo/root"

    def test_ds2_falls_back_on_nonzero_returncode(self) -> None:
        """T_DS2: returns cwd unchanged when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_source_dir("/any/cwd") == "/any/cwd"


class TestCloneRepoDetectAndExpand:
    def test_ds4_expands_tilde(self) -> None:
        """T_DS4: clone_repo raises ValueError with 'resolved to' when tilde path doesn't exist."""
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo("~/nonexistent-autoskillit-test-xyz", "test-run")

    def test_ds5_raises_value_error_with_clear_message(self, tmp_path) -> None:
        """T_DS5: non-existent path raises ValueError with 'resolved to' in message."""
        nonexistent = str(tmp_path / "does-not-exist")
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo(nonexistent, "test-run")

    def test_cb7_calls_detect_branch_when_branch_empty(self, tmp_path) -> None:
        """T_CB7: detect_branch is called with source_dir when branch=""."""
        with patch(
            "autoskillit.workspace.clone.detect_branch", return_value="main"
        ) as mock_detect:
            with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
                mock_clone = MagicMock()
                mock_clone.returncode = 0
                with patch("subprocess.run", return_value=mock_clone):
                    clone_repo(str(tmp_path), "test-run", branch="")
        mock_detect.assert_called_once_with(str(tmp_path))

    def test_cb9_passes_branch_flag_to_git(self, tmp_path) -> None:
        """T_CB9: --branch and branch name appear in the git clone subprocess call."""
        with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
            mock_clone = MagicMock()
            mock_clone.returncode = 0
            with patch("subprocess.run", return_value=mock_clone) as mock_run:
                clone_repo(str(tmp_path), "test-run", branch="dev")
        git_clone_calls = [
            call for call in mock_run.call_args_list if call.args and "clone" in call.args[0]
        ]
        assert git_clone_calls, "git clone was not called"
        cmd = git_clone_calls[0].args[0]
        assert "--branch" in cmd
        assert "dev" in cmd

    def test_cb10_returns_warning_dict_on_uncommitted_changes(self, tmp_path) -> None:
        """T_CB10: uncommitted changes produce warning dict; git clone not called."""
        with patch("autoskillit.workspace.clone.detect_branch", return_value="main"):
            with patch(
                "autoskillit.workspace.clone.detect_uncommitted_changes",
                return_value=[" M file.py"],
            ):
                with patch("subprocess.run") as mock_run:
                    result = clone_repo(str(tmp_path), "test-run")
        assert result["uncommitted_changes"] == "true"
        mock_run.assert_not_called()


class TestDetectBranch:
    def test_cb11_returns_branch_name_on_success(self) -> None:
        """T_CB11: returns branch name when git rev-parse succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "main\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "main"

    def test_cb12_returns_empty_string_on_nonzero_returncode(self) -> None:
        """T_CB12: returns "" when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == ""

    def test_cb13_returns_head_literal_for_detached_state(self) -> None:
        """T_CB13: returns literal 'HEAD' in detached HEAD state; caller treats as no branch."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "HEAD\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_branch("/any") == "HEAD"


class TestDetectUncommittedChanges:
    def test_cb14_returns_empty_list_when_clean(self) -> None:
        """T_CB14: returns [] when working tree is clean."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []

    def test_cb15_returns_changed_file_lines_when_dirty(self) -> None:
        """T_CB15: returns non-empty status lines when changes exist."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M file.py\n?? new.txt\n"
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == [" M file.py", "?? new.txt"]

    def test_cb16_returns_empty_list_on_git_failure(self) -> None:
        """T_CB16: returns [] when git exits non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("subprocess.run", return_value=mock_result):
            assert detect_uncommitted_changes("/any") == []


class TestPushToRemoteProtectedBranch:
    """push_to_remote rejects pushes to protected branches."""

    @pytest.mark.parametrize("branch", ["main", "integration", "stable"])
    def test_push_to_remote_rejects_protected_branch(self, tmp_path: Path, branch: str) -> None:
        """push_to_remote must reject when branch is a protected branch."""
        clone = tmp_path / "clone"
        clone.mkdir()

        result = push_to_remote(
            clone_path=str(clone),
            branch=branch,
            remote_url="https://github.com/example/repo.git",
            protected_branches=["main", "integration", "stable"],
        )

        assert result["success"] is False
        assert result.get("error_type") == "protected_branch_push"


class TestPushToRemoteMocked:
    def test_ds6_push_to_remote_calls_get_url_then_push(self) -> None:
        """T_DS6: push_to_remote calls git remote get-url origin then git push -u upstream."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""

        with patch("subprocess.run", side_effect=[mock_url, mock_push]) as mock_run:
            result = push_to_remote(
                "/clone", "/source", "main", protected_branches=[], force=False
            )

        assert result == {"success": True, "stderr": ""}
        # First call: git remote get-url origin from source_dir
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == ["git", "remote", "get-url", "origin"]
        assert first_call[1]["cwd"] == "/source"
        # Second call: git push -u upstream <branch> from clone_path — no --force-with-lease
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0] == ["git", "push", "-u", "upstream", "main"]
        assert second_call[1]["cwd"] == "/clone"

    def test_ds7_push_to_remote_fails_when_no_origin(self) -> None:
        """T_DS7: push_to_remote returns error when git remote get-url fails, no push attempted."""
        mock_fail = MagicMock()
        mock_fail.returncode = 128
        mock_fail.stdout = ""
        mock_fail.stderr = "error: No such remote 'origin'"

        with patch("subprocess.run", return_value=mock_fail) as mock_run:
            result = push_to_remote("/clone", "/source", "main", protected_branches=[])

        assert result["success"] is False
        assert "origin" in result["stderr"]
        assert mock_run.call_count == 1  # no push attempted

    def test_push_to_remote_with_force_injects_force_with_lease(self) -> None:
        """T1: push_to_remote with force=True appends --force-with-lease to push command."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""

        with patch("subprocess.run", side_effect=[mock_url, mock_push]) as mock_run:
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result == {"success": True, "stderr": ""}
        second_call = mock_run.call_args_list[1]
        cmd = second_call[0][0]
        assert "--force-with-lease" in cmd
        assert "git" in cmd
        assert "push" in cmd
        assert "upstream" in cmd
        assert "main" in cmd
        assert second_call[1]["cwd"] == "/clone"

    def test_push_to_remote_default_force_false_does_not_inject_lease(self) -> None:
        """T2: push_to_remote with force=False (default) does not inject --force-with-lease."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""

        with patch("subprocess.run", side_effect=[mock_url, mock_push]) as mock_run:
            result = push_to_remote(
                "/clone", "/source", "main", protected_branches=[], force=False
            )

        assert result == {"success": True, "stderr": ""}
        second_call = mock_run.call_args_list[1]
        cmd = second_call[0][0]
        assert cmd == ["git", "push", "-u", "upstream", "main"]

    def test_default_clone_manager_push_to_remote_accepts_force_param(self) -> None:
        """T5: DefaultCloneManager.push_to_remote has force keyword param with default False."""
        sig = inspect.signature(DefaultCloneManager.push_to_remote)
        assert "force" in sig.parameters, (
            "DefaultCloneManager.push_to_remote must have 'force' param"
        )
        param = sig.parameters["force"]
        assert param.default is False, "force param must default to False"

    def test_force_with_lease_stale_returns_error_type(self) -> None:
        """push_to_remote returns error_type=force_with_lease_stale when git reports stale info."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "! [rejected] main -> main (stale info)"

        with patch("subprocess.run", side_effect=[mock_url, mock_push]):
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result["success"] is False
        assert result.get("error_type") == "force_with_lease_stale"

    def test_force_with_lease_no_upstream_returns_error_type(self) -> None:
        """push_to_remote returns error_type=force_with_lease_no_upstream for missing upstream."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "error: The current branch main has no upstream branch."

        with patch("subprocess.run", side_effect=[mock_url, mock_push]):
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result["success"] is False
        assert result.get("error_type") == "force_with_lease_no_upstream"

    def test_force_push_generic_failure_has_no_error_type(self) -> None:
        """push_to_remote returns no error_type for generic force-push failures."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "error: failed to push some refs"

        with patch("subprocess.run", side_effect=[mock_url, mock_push]):
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result["success"] is False
        assert "error_type" not in result

    def test_non_force_failure_has_no_error_type(self) -> None:
        """push_to_remote returns no error_type for non-force push failures."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "! [rejected] main -> main (stale info)"

        with patch("subprocess.run", side_effect=[mock_url, mock_push]):
            result = push_to_remote(
                "/clone", "/source", "main", protected_branches=[], force=False
            )

        assert result["success"] is False
        assert "error_type" not in result


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

        result = push_to_remote(clone_path, str(source), "main", protected_branches=[])

        assert result["success"] is False
        assert result.get("error_type") == "local_non_bare_remote"

        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

    def test_clone_repo_returns_remote_url(self, tmp_path: Path) -> None:
        """clone_repo result dict includes remote_url field."""
        remote = tmp_path / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(remote)],
            check=True,
            capture_output=True,
        )
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
            ["git", "-C", str(source), "push", "-u", "origin", "HEAD:main"],
            check=True,
            capture_output=True,
        )

        result = clone_repo(str(source), "test-remoteurl", strategy="proceed")

        assert "remote_url" in result
        assert str(remote) in result["remote_url"]

        # After the fix: origin is a file:// URL, upstream holds the real remote_url
        origin_in_clone = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=result["clone_path"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        upstream_in_clone = subprocess.run(
            ["git", "remote", "get-url", "upstream"],
            cwd=result["clone_path"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert origin_in_clone.startswith("file://"), (
            "Clone's origin must be a file:// URL after the fix"
        )
        assert upstream_in_clone == result["remote_url"], (
            "Clone's upstream must equal the returned remote_url"
        )

        import shutil

        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_clone_repo_local_strategy_returns_discriminator_when_no_origin(
        self, tmp_path: Path
    ) -> None:
        """clone_local strategy returns discriminator with clone_source_type=local."""
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

        result = clone_repo(str(source), "test-noremote", strategy="clone_local")

        assert "clone_path" in result
        assert result["remote_url"] == ""
        assert result["clone_source_type"] == "local"
        assert result["clone_source_reason"] == "strategy_clone_local"
        assert Path(result["clone_path"]).exists()

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
            ["git", "-C", str(source), "branch", "-M", "main"],
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
        result = push_to_remote(
            clone_path, remote_url=str(remote), branch="main", protected_branches=[]
        )

        assert result["success"] is True

        import shutil

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)


class TestClassifyRemoteUrl:
    """classify_remote_url correctly identifies remote URL types."""

    @pytest.mark.parametrize(
        "url,expected_type",
        [
            ("git@github.com:org/repo.git", "network"),
            ("https://github.com/org/repo.git", "network"),
            ("ssh://git@github.com/org/repo.git", "network"),
            ("http://example.com/repo.git", "network"),
            ("git://github.com/org/repo.git", "network"),
            ("file:///tmp/repo.git", "network"),
        ],
    )
    def test_network_urls(self, url: str, expected_type: str) -> None:
        result = classify_remote_url(url)
        assert result == expected_type

    def test_bare_local_path(self, tmp_path: Path) -> None:
        import subprocess

        bare = tmp_path / "repo.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        result = classify_remote_url(str(bare))
        assert result == "bare_local"

    def test_nonbare_local_path(self, tmp_path: Path) -> None:
        import subprocess

        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        result = classify_remote_url(str(repo))
        assert result == "nonbare_local"

    def test_empty_string_returns_none_type(self) -> None:
        result = classify_remote_url("")
        assert result == "none"

    def test_nonexistent_path_returns_unknown(self, tmp_path: Path) -> None:
        result = classify_remote_url(str(tmp_path / "nonexistent"))
        assert result == "unknown"


class TestCloneDecontamination:
    """clone_repo strips tracked and on-disk generated files from clones."""

    def test_clone_repo_untracks_inherited_generated_files(self, tmp_path: Path) -> None:
        """Tracked generated files in source are untracked in the clone."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        # Create and track a generated file
        hooks_dir = repo / "src" / "autoskillit" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text('{"hooks": {}}')
        subprocess.run(
            ["git", "-C", str(repo), "add", "src/autoskillit/hooks/hooks.json"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "add generated file"],
            check=True,
            capture_output=True,
        )

        result = clone_repo(str(repo), "decontam-test", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        try:
            ls_result = subprocess.run(
                ["git", "ls-files", "--", "src/autoskillit/hooks/hooks.json"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
            )
            assert ls_result.stdout.strip() == "", "Generated file should be untracked in clone"
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_repo_deletes_untracked_generated_files_from_disk(self, tmp_path: Path) -> None:
        """clone_local copies untracked generated files; decontamination deletes them."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        # Create on-disk generated file WITHOUT tracking it
        hooks_dir = repo / "src" / "autoskillit" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text('{"hooks": {}}')

        result = clone_repo(str(repo), "disk-cleanup-test", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        try:
            assert not (clone_path / "src" / "autoskillit" / "hooks" / "hooks.json").exists(), (
                "Untracked generated file should be deleted from clone disk"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_repo_noop_when_no_generated_files_tracked(self, git_repo: Path) -> None:
        """Clean repo with no tracked generated files clones without errors."""
        result = clone_repo(str(git_repo), "clean-test", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        try:
            assert clone_path.is_dir()
            assert (clone_path / ".git").is_dir()
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)


class TestCloneRemoteUrlResolution:
    """T1: clone_repo resolves remote URL before cloning when origin is configured."""

    # T1-A
    def test_clone_uses_remote_url_as_clone_source(
        self, local_with_remote: Path, bare_remote: Path
    ) -> None:
        """clone_repo uses the remote URL (not the local path) as git clone source."""
        with patch(
            "autoskillit.workspace.clone.subprocess.run",
            wraps=subprocess.run,
        ) as spy:
            result = clone_repo(str(local_with_remote), "test", branch="main", strategy="proceed")
        clone_path = Path(result["clone_path"])
        try:
            clone_calls = [
                call
                for call in spy.call_args_list
                if call[0] and isinstance(call[0][0], list) and call[0][0][:2] == ["git", "clone"]
            ]
            assert len(clone_calls) == 1, (
                f"Expected exactly one git clone call, got {len(clone_calls)}"
            )
            clone_args = clone_calls[0][0][0]
            # source is second-to-last positional arg (before clone_path)
            clone_source = clone_args[-2]
            assert clone_source == str(bare_remote), (
                f"Expected clone source to be remote URL {bare_remote!r}, "
                f"got {clone_source!r} (local path was used instead)"
            )
            assert result["clone_source_type"] == "remote", (
                f"Expected clone_source_type='remote', got {result.get('clone_source_type')!r}"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    # T1-B
    def test_clone_raises_on_no_origin_with_proceed_strategy(self, git_repo: Path) -> None:
        """clone_repo raises RuntimeError when no remote origin and strategy=proceed.

        Previously the code silently fell back to cloning from the local path
        (git clone /abs/path via local transport). After the fix, the no_origin
        probe result causes an immediate RuntimeError, instructing the caller to
        use strategy="clone_local" for an intentional local-only clone.
        """
        with pytest.raises(RuntimeError, match="clone_origin_probe_failed.*no_origin"):
            clone_repo(str(git_repo), "test", strategy="proceed")

    # T1-C
    def test_clone_result_remote_url_correct_after_remote_clone(
        self, local_with_remote: Path, bare_remote: Path
    ) -> None:
        """clone_repo result['remote_url'] equals the remote URL after cloning."""
        result = clone_repo(str(local_with_remote), "test", branch="main", strategy="proceed")
        clone_path = Path(result["clone_path"])
        try:
            assert result["remote_url"] == str(bare_remote)
            assert result["clone_source_type"] == "remote"
            assert result["clone_source_reason"] == "ok"
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    # T1-D
    def test_clone_uses_remote_when_branch_not_on_remote(
        self, local_with_remote: Path, bare_remote: Path
    ) -> None:
        """Regression: clone always uses remote URL even when branch not on remote.

        Previously the origin probe collapse allowed falling back to the local path
        when ls-remote did not find the branch on the remote. After the fix the probe
        always uses the remote URL when one is configured, so git clone fails (correctly)
        instead of silently cloning local state.
        """
        with patch(
            "autoskillit.workspace.clone.subprocess.run",
            wraps=subprocess.run,
        ) as spy:
            with pytest.raises(RuntimeError, match="git clone failed"):
                clone_repo(
                    str(local_with_remote),
                    "test",
                    branch="feature/local-only",
                    strategy="proceed",
                )
        clone_calls = [
            call
            for call in spy.call_args_list
            if call[0] and isinstance(call[0][0], list) and call[0][0][:2] == ["git", "clone"]
        ]
        assert len(clone_calls) == 1, "Expected exactly one git clone call"
        cmd = clone_calls[0][0][0]
        # Extract positional args from git clone, skipping flags and their values.
        # Handles any optional flags (--branch, --no-hardlinks, --depth, etc.)
        # without relying on a fixed index like [-2].
        positional: list[str] = []
        i = cmd.index("clone") + 1
        while i < len(cmd):
            if cmd[i].startswith("-"):
                i += 2  # skip flag and its value
            else:
                positional.append(cmd[i])
                i += 1
        assert len(positional) == 2, f"Expected [source, target] in clone cmd: {cmd}"
        clone_source = positional[0]
        assert clone_source == str(bare_remote), (
            f"Expected remote URL {bare_remote!r} as clone source, "
            f"got {clone_source!r} — local path fallback detected"
        )


class TestProbeSingleRemote:
    """Unit tests for _probe_single_remote helper."""

    def test_probe_single_remote_returns_ok_reason_when_remote_configured(
        self, local_with_remote: Path, bare_remote: Path
    ) -> None:
        from autoskillit.workspace.clone import _probe_single_remote

        resolution = _probe_single_remote(local_with_remote, "origin")
        assert resolution.reason == "ok"
        assert resolution.url == str(bare_remote)
        assert resolution.stderr == ""

    def test_probe_single_remote_returns_no_origin_reason_when_no_remote(
        self, git_repo: Path
    ) -> None:
        from autoskillit.workspace.clone import _probe_single_remote

        resolution = _probe_single_remote(git_repo, "origin")
        assert resolution.reason == "no_origin"
        assert resolution.url == ""

    def test_probe_single_remote_returns_timeout_reason_on_timeout(self, tmp_path: Path) -> None:
        from autoskillit.workspace.clone import _probe_single_remote

        with patch(
            "autoskillit.workspace.clone.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            resolution = _probe_single_remote(tmp_path, "origin")
        assert resolution.reason == "timeout"
        assert resolution.url == ""

    def test_probe_single_remote_returns_error_reason_on_non_zero_rc(self, tmp_path: Path) -> None:
        from autoskillit.workspace.clone import _probe_single_remote

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"
        with patch("autoskillit.workspace.clone.subprocess.run", return_value=mock_result):
            resolution = _probe_single_remote(tmp_path, "origin")
        assert resolution.reason == "error"
        assert resolution.url == ""
        assert resolution.stderr == "fatal: not a git repository"


class TestProbeCloneSourceUrl:
    """Unit tests for the updated _probe_clone_source_url URL resolution logic."""

    def test_prefers_upstream_network_url_over_file_origin(self, tmp_path: Path) -> None:
        """When origin=file:// and upstream=network URL, uses upstream (the key bug fix).

        This is the exact scenario that caused stale clones in multi-batch pipelines:
        source_dir is a previous autoskillit clone with origin rewritten to file://.
        """
        from autoskillit.workspace.clone import _probe_clone_source_url

        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        # Simulate a previous _ensure_origin_isolated call: origin=file://, upstream=real remote
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", f"file://{source}"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "upstream", str(bare)],
            check=True,
            capture_output=True,
        )

        result = _probe_clone_source_url(source)

        assert result.reason == "ok"
        assert result.url == str(bare), (
            f"Expected upstream URL {bare!r}, got {result.url!r}. "
            "When origin=file://, upstream should be preferred."
        )

    def test_falls_back_to_origin_when_no_upstream(self, tmp_path: Path) -> None:
        """Without upstream remote, falls back to origin URL (existing behavior preserved)."""
        from autoskillit.workspace.clone import _probe_clone_source_url

        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        source = tmp_path / "source"
        subprocess.run(["git", "clone", str(bare), str(source)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        # Only origin is configured, no upstream

        result = _probe_clone_source_url(source)

        assert result.reason == "ok"
        assert result.url == str(bare)

    def test_uses_origin_when_upstream_is_file_url_and_origin_is_network(
        self, tmp_path: Path
    ) -> None:
        """When upstream=file:// and origin=non-file-local-path that is network-equivalent,
        falls through to origin result (covers edge cases where upstream is also local)."""
        from autoskillit.workspace.clone import _probe_clone_source_url

        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        # upstream is a file:// URL (not a real network URL), origin is the bare path
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "upstream", f"file://{source}"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )

        result = _probe_clone_source_url(source)

        # upstream is file:// → excluded by _is_not_file_url → falls through to origin
        assert result.reason == "ok"
        assert result.url == str(bare)

    def test_returns_no_origin_for_repo_without_remotes(self, tmp_path: Path) -> None:
        """Repo with no remotes returns reason='no_origin' (unchanged from current behavior)."""
        from autoskillit.workspace.clone import _probe_clone_source_url

        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )

        result = _probe_clone_source_url(source)

        assert result.reason == "no_origin"
        assert result.url == ""


class TestCloneFromPreviousAutoskillitClone:
    """Regression: cloning from a previous autoskillit clone must use the network remote."""

    def test_clone_from_previous_clone_uses_upstream_not_stale_local(self, tmp_path: Path) -> None:
        """Batch N+1 clones from batch N's clone must get fresh HEAD from the network remote,
        not the stale local state of the previous clone.

        Setup:
          bare_remote (has commit A + commit B)
          source      (has commit A only — stale)
          source has: origin=file://source (isolation), upstream=bare_remote

        Expected: clone_from_source gets commit B (fetched from bare_remote via upstream).
        Bug behavior (pre-fix): gets only commit A (cloned from file://source via origin).
        """
        bare_remote = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(bare_remote)],
            check=True,
            capture_output=True,
        )

        # source: has commit A, stale (does not have commit B)
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "config", "user.name", "T"], check=True, capture_output=True
        )
        (source / "a.txt").write_text("commit A")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(source), "commit", "-m", "commit A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "branch", "-M", "main"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(source), "push", str(bare_remote), "main"],
            check=True,
            capture_output=True,
        )

        # Simulate _ensure_origin_isolated: source.origin = file://, source.upstream = bare_remote
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", f"file://{source}"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "upstream", str(bare_remote)],
            check=True,
            capture_output=True,
        )

        # Add commit B to bare_remote directly (simulates another batch merging)
        tmp_push = tmp_path / "push_helper"
        subprocess.run(
            ["git", "clone", str(bare_remote), str(tmp_push)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_push), "config", "user.email", "t@t.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_push), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        (tmp_push / "b.txt").write_text("commit B")
        subprocess.run(["git", "-C", str(tmp_push), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(tmp_push), "commit", "-m", "commit B"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_push), "push", "origin", "main"], check=True, capture_output=True
        )

        # Now clone from source (which is stale — missing commit B)
        result = clone_repo(str(source), "batch2", branch="main", strategy="proceed")
        clone_path = Path(result["clone_path"])

        try:
            # The clone MUST have b.txt (from bare_remote commit B), not just a.txt
            assert (clone_path / "b.txt").exists(), (
                "Clone is missing b.txt — it cloned from the stale local source instead of "
                "the network remote (bare_remote). This is the #817 regression."
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)
            shutil.rmtree(tmp_push, ignore_errors=True)


class TestCloneOriginProbeFailFast:
    """Tests 2A-2C: clone_repo raises RuntimeError when origin probe fails."""

    def test_clone_repo_raises_on_timeout_when_source_has_origin(
        self, local_with_remote: Path
    ) -> None:
        """The reported bug reproduced: timeout on origin probe raises RuntimeError.

        Patches subprocess.run to raise TimeoutExpired only on get-url origin
        while delegating all other calls to the real subprocess.run.
        """
        _real_run = subprocess.run

        def _selective_side_effect(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(cmd, list) and cmd == ["git", "remote", "get-url", "origin"]:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
            return _real_run(cmd, **kwargs)

        with patch(
            "autoskillit.workspace.clone.subprocess.run",
            side_effect=_selective_side_effect,
        ):
            with structlog.testing.capture_logs() as cap:
                with pytest.raises(RuntimeError, match="clone_origin_probe_failed.*timeout"):
                    clone_repo(str(local_with_remote), "t", strategy="proceed")

        warning_events = [e for e in cap if e.get("event") == "clone_origin_probe_failed"]
        assert warning_events, "Expected clone_origin_probe_failed warning to be emitted"
        assert warning_events[0].get("reason") == "timeout"

    def test_clone_repo_raises_on_non_zero_get_url_when_source_has_origin(
        self, local_with_remote: Path
    ) -> None:
        """Non-zero returncode on get-url origin raises RuntimeError."""
        _real_run = subprocess.run

        def _selective_side_effect(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(cmd, list) and cmd == ["git", "remote", "get-url", "origin"]:
                mock = MagicMock()
                mock.returncode = 1
                mock.stdout = ""
                mock.stderr = "fatal: some error"
                return mock
            return _real_run(cmd, **kwargs)

        with patch(
            "autoskillit.workspace.clone.subprocess.run",
            side_effect=_selective_side_effect,
        ):
            with pytest.raises(RuntimeError, match="clone_origin_probe_failed"):
                clone_repo(str(local_with_remote), "t", strategy="proceed")

    def test_clone_repo_raises_on_no_origin_without_explicit_local_strategy(
        self, git_repo: Path
    ) -> None:
        """clone_repo raises RuntimeError when source has no origin and strategy=proceed.

        Error message must instruct the caller to pass strategy="clone_local".
        """
        with pytest.raises(
            RuntimeError,
            match=r"clone_origin_probe_failed.*no_origin.*strategy=.?clone_local",
        ):
            clone_repo(str(git_repo), "t", strategy="proceed")


class TestCloneDiscriminator:
    """Tests 3A-3B and 5: typed return contract with clone_source_type discriminator."""

    def test_clone_local_strategy_succeeds_on_no_origin_with_discriminator(
        self, git_repo: Path
    ) -> None:
        """strategy=clone_local returns clone_source_type=local with discriminator."""
        result = clone_repo(str(git_repo), "t", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        try:
            assert result["clone_source_type"] == "local"
            assert result["clone_source_reason"] == "strategy_clone_local"
            assert result["remote_url"] == ""
            assert clone_path.exists()
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_proceed_strategy_succeeds_with_remote_discriminator(
        self, local_with_remote: Path, bare_remote: Path
    ) -> None:
        """strategy=proceed returns clone_source_type=remote with discriminator."""
        result = clone_repo(str(local_with_remote), "t", branch="main", strategy="proceed")
        clone_path = Path(result["clone_path"])
        try:
            assert result["clone_source_type"] == "remote"
            assert result["clone_source_reason"] == "ok"
            assert result["remote_url"] == str(bare_remote)
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    @pytest.mark.parametrize(
        "fixture_name,strategy,expected_source_type",
        [
            ("local_with_remote", "proceed", "remote"),
            ("git_repo", "clone_local", "local"),
        ],
    )
    def test_clone_result_always_has_discriminator_key(
        self,
        fixture_name: str,
        strategy: str,
        expected_source_type: str,
        request: pytest.FixtureRequest,
    ) -> None:
        """Structural invariant: every success result carries clone_source_type."""
        source = request.getfixturevalue(fixture_name)
        kwargs = {"strategy": strategy}
        if fixture_name == "local_with_remote":
            kwargs["branch"] = "main"
        result = clone_repo(str(source), "t", **kwargs)  # type: ignore[arg-type]
        clone_path = Path(result["clone_path"])
        try:
            assert result["clone_source_type"] == expected_source_type
            assert "clone_source_reason" in result
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)


class TestOriginIsolationUnconditional:
    """Tests 4A-4B: _ensure_origin_isolated fires unconditionally."""

    def test_clone_local_strategy_still_rewrites_origin_to_file_url(self, git_repo: Path) -> None:
        """clone_local (no remote) rewrites origin to file:// — covers #377 regression.

        Inverts the deleted test_clone_origin_unchanged_when_no_upstream.
        """
        result = clone_repo(str(git_repo), "t", strategy="clone_local")
        clone_path = Path(result["clone_path"])
        try:
            origin = subprocess.run(
                ["git", "-C", str(clone_path), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert origin == f"file://{clone_path}", (
                f"Expected file://{clone_path!r}, got {origin!r}"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_remote_strategy_rewrites_origin_to_file_url(
        self, local_with_remote: Path
    ) -> None:
        """clone via remote URL also rewrites origin to file:// unconditionally."""
        result = clone_repo(str(local_with_remote), "t", branch="main", strategy="proceed")
        clone_path = Path(result["clone_path"])
        try:
            origin = subprocess.run(
                ["git", "-C", str(clone_path), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert origin == f"file://{clone_path}", (
                f"Expected file://{clone_path!r}, got {origin!r}"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)
