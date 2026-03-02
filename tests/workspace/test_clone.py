"""Tests for autoskillit.workspace.clone module."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace.clone import (
    classify_remote_url,
    clone_repo,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
    push_to_remote,
    remove_clone,
)


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
        result = clone_repo(str(git_repo), "myrun")
        clone_path = Path(result["clone_path"])
        try:
            assert clone_path.is_dir()
            assert (clone_path / ".git").is_dir()
            assert "clone_path" in result
            assert "source_dir" in result
            assert "remote_url" in result
            assert result["source_dir"] == str(git_repo.resolve())
            expected_parent = git_repo.parent / "autoskillit-runs"
            assert clone_path.parent == expected_parent
        finally:
            shutil.rmtree(clone_path, ignore_errors=True)

    def test_clone_path_name_format(self, git_repo: Path) -> None:
        """Clone directory name follows run_name-YYYYMMDD-HHMMSS-ffffff format."""
        result = clone_repo(str(git_repo), "myrun")
        clone_path = Path(result["clone_path"])
        try:
            assert re.match(r"myrun-\d{8}-\d{6}-\d{6}$", clone_path.name), clone_path.name
        finally:
            shutil.rmtree(clone_path, ignore_errors=True)

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
        shutil.rmtree(clone_path, ignore_errors=True)

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
        shutil.rmtree(clone_path, ignore_errors=True)

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
        shutil.rmtree(clone_path, ignore_errors=True)

    def test_cb5_strategy_clone_local_includes_uncommitted_changes(self, git_repo: Path) -> None:
        """T_CB5: strategy='clone_local' copytree includes untracked files."""
        import shutil

        (git_repo / "untracked.txt").write_text("dirty")
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        assert "clone_path" in result
        clone_path = Path(result["clone_path"])
        assert (clone_path / "untracked.txt").exists()
        shutil.rmtree(clone_path, ignore_errors=True)

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
        shutil.rmtree(clone_path, ignore_errors=True)

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
        import shutil

        result = clone_repo(str(local_with_remote), "test-run", branch="main")
        assert "clone_path" in result
        assert "unpublished_branch" not in result
        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_strategy_proceed_bypasses_unpublished_guard(self, local_with_remote: Path) -> None:
        """strategy='proceed' bypasses the unpublished_branch guard."""
        import shutil

        result = clone_repo(
            str(local_with_remote), "test-run", branch="feature/local-only", strategy="proceed"
        )
        # Guard bypassed — clone proceeds (NOT an unpublished warning)
        assert "unpublished_branch" not in result
        if "clone_path" in result:
            shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)


def test_clone_path_within_tmp_path(tmp_path: Path, git_repo: Path) -> None:
    """clone_repo output must be a descendant of tmp_path, never its parent.

    Fails when the git_repo fixture returns tmp_path itself, because clone_repo
    places autoskillit-runs/ at source.parent = tmp_path.parent (worker-shared).
    Passes once git_repo returns tmp_path / 'repo' (a subdirectory).
    """
    result = clone_repo(str(git_repo), "isolation-check")
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
    """Contract: clone's git origin remote == the remote_url returned by clone_repo."""

    def test_clone_origin_equals_remote_url_for_bare_upstream(self, tmp_path: Path) -> None:
        """Clone's origin must be rewritten to the real upstream, not source_dir.

        Before the fix: clone's origin = source_dir (local path), not the bare remote.
        After the fix: clone's origin = bare_remote path = result["remote_url"].
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
            # The clone's own origin must equal remote_url (the bare remote), NOT source_dir
            origin_in_clone = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(clone_path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            assert origin_in_clone == remote_url, (
                f"Clone's origin ({origin_in_clone!r}) should equal remote_url ({remote_url!r}), "
                f"not source_dir ({str(source)!r})"
            )
            assert origin_in_clone != str(source), (
                "Clone's origin must not be the local source_dir path"
            )
        finally:
            shutil.rmtree(clone_path.parent, ignore_errors=True)

    def test_clone_origin_unchanged_when_no_upstream(self, tmp_path: Path) -> None:
        """When source has no origin, clone still succeeds and remote_url is empty.

        Contract test — passes on main before the fix (no origin means no rewrite).
        """
        source = tmp_path / "source"
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "t@t.com"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "T"], check=True)
        (source / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-m", "init"], check=True)

        result = clone_repo(str(source), "no-upstream-test")
        try:
            assert result["remote_url"] == ""
            # No assertion on clone's origin — no upstream exists, contract vacuously holds
        finally:
            shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)

    def test_clone_local_strategy_origin_also_satisfies_contract(self, tmp_path: Path) -> None:
        """clone_local strategy (copytree) should also satisfy the origin == remote_url contract.

        Contract test — passes on main before the fix because copytree copies .git/config
        verbatim (source's origin = bare_remote = remote_url). The rewrite in the fix is a
        no-op for this path. This test guards against future regressions.
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

            assert origin_in_clone == remote_url
        finally:
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

        shutil.rmtree(Path(clone_path), ignore_errors=True)

    def test_keep_true_preserves_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test")
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

        shutil.rmtree(Path(clone_path), ignore_errors=True)

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


# ---------------------------------------------------------------------------
# Merged from test_workspace.py — detect_source_dir and detect_branch tests
# ---------------------------------------------------------------------------


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
    def test_ds3_calls_detect_source_dir_when_source_dir_empty(self, tmp_path) -> None:
        """T_DS3: clone_repo calls detect_source_dir when source_dir is empty."""
        with patch(
            "autoskillit.workspace.clone.detect_source_dir", return_value=str(tmp_path)
        ) as mock_detect:
            mock_clone = MagicMock()
            mock_clone.returncode = 0
            with patch("subprocess.run", return_value=mock_clone):
                clone_repo("", "test-run")
        mock_detect.assert_called_once()

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

    def test_cb8_skips_detect_branch_when_branch_provided(self, tmp_path) -> None:
        """T_CB8: detect_branch is NOT called when branch is explicitly provided."""
        with patch("autoskillit.workspace.clone.detect_branch") as mock_detect:
            with patch("autoskillit.workspace.clone.detect_uncommitted_changes", return_value=[]):
                mock_clone = MagicMock()
                mock_clone.returncode = 0
                with patch("subprocess.run", return_value=mock_clone):
                    clone_repo(str(tmp_path), "test-run", branch="feature")
        mock_detect.assert_not_called()

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


class TestPushToRemoteMocked:
    def test_ds6_push_to_remote_calls_get_url_then_push(self) -> None:
        """T_DS6: push_to_remote calls git remote get-url origin then git push <url> <branch>."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 0
        mock_push.stderr = ""

        with patch("subprocess.run", side_effect=[mock_url, mock_push]) as mock_run:
            result = push_to_remote("/clone", "/source", "main")

        assert result == {"success": True, "stderr": ""}
        # First call: git remote get-url origin from source_dir
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == ["git", "remote", "get-url", "origin"]
        assert first_call[1]["cwd"] == "/source"
        # Second call: git push <url> <branch> from clone_path
        second_call = mock_run.call_args_list[1]
        assert second_call[0][0] == ["git", "push", "git@github.com:org/repo.git", "main"]
        assert second_call[1]["cwd"] == "/clone"

    def test_ds7_push_to_remote_fails_when_no_origin(self) -> None:
        """T_DS7: push_to_remote returns error when git remote get-url fails, no push attempted."""
        mock_fail = MagicMock()
        mock_fail.returncode = 128
        mock_fail.stdout = ""
        mock_fail.stderr = "error: No such remote 'origin'"

        with patch("subprocess.run", return_value=mock_fail) as mock_run:
            result = push_to_remote("/clone", "/source", "main")

        assert result["success"] is False
        assert "origin" in result["stderr"]
        assert mock_run.call_count == 1  # no push attempted


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

        # Contract invariant: clone's own origin must equal the returned remote_url
        origin_in_clone = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=result["clone_path"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert origin_in_clone == result["remote_url"], (
            "Clone's origin must equal the returned remote_url"
        )

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
        result = push_to_remote(clone_path, remote_url=str(remote), branch="main")

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
