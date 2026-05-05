"""Core clone_repo tests — setup, paths, error handling, origin contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from autoskillit.workspace.clone import (
    clone_repo,
    remove_clone,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


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
        shutil.rmtree(Path(clone_path), ignore_errors=True)

    def test_keep_true_preserves_directory(self, git_repo: Path) -> None:
        result = clone_repo(str(git_repo), "test", strategy="clone_local")
        clone_path = result["clone_path"]
        remove_result = remove_clone(clone_path, keep="true")
        assert remove_result == {"removed": "false", "reason": "keep=true"}
        assert Path(clone_path).exists()
        # Cleanup
        shutil.rmtree(Path(clone_path), ignore_errors=True)

    def test_missing_path_returns_not_found(self) -> None:
        result = remove_clone("/nonexistent/clone/path", keep="false")
        assert result == {"removed": "false", "reason": "not_found"}


class TestCloneRepoDetectAndExpand:
    def test_ds4_expands_tilde(self) -> None:
        """T_DS4: clone_repo raises ValueError with 'resolved to' when tilde path doesn't exist."""
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo("~/nonexistent-autoskillit-test-xyz", "test-run")

    def test_ds5_raises_value_error_with_clear_message(self, tmp_path: Path) -> None:
        """T_DS5: non-existent path raises ValueError with 'resolved to' in message."""
        nonexistent = str(tmp_path / "does-not-exist")
        with pytest.raises(ValueError, match="resolved to"):
            clone_repo(nonexistent, "test-run")

    def test_cb7_calls_detect_branch_when_branch_empty(self, tmp_path: Path) -> None:
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

    def test_cb9_passes_branch_flag_to_git(self, tmp_path: Path) -> None:
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

    def test_cb10_returns_warning_dict_on_uncommitted_changes(self, tmp_path: Path) -> None:
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


class TestCloneRepoResultShape:
    """clone_repo result-value contracts — remote_url field and clone_source_type discriminator."""

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

        shutil.rmtree(Path(result["clone_path"]).parent, ignore_errors=True)


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
