"""push_to_remote tests — E2E, mocked, protected branches, force-with-lease, merge queue."""

from __future__ import annotations

import inspect
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace.clone import (
    DefaultCloneManager,
    clone_repo,
    push_to_remote,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


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


class TestPushToRemoteProtectedBranch:
    """push_to_remote rejects pushes to protected branches."""

    @pytest.mark.parametrize("branch", ["main", "develop", "stable"])
    def test_push_to_remote_rejects_protected_branch(self, tmp_path: Path, branch: str) -> None:
        """push_to_remote must reject when branch is a protected branch."""
        clone = tmp_path / "clone"
        clone.mkdir()

        result = push_to_remote(
            clone_path=str(clone),
            branch=branch,
            remote_url="https://github.com/example/repo.git",
            protected_branches=["main", "develop", "stable"],
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ) as mock_run:
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", return_value=mock_fail
        ) as mock_run:
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ) as mock_run:
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ) as mock_run:
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
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

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
            result = push_to_remote(
                "/clone", "/source", "main", protected_branches=[], force=False
            )

        assert result["success"] is False
        assert "error_type" not in result

    def test_gh006_returns_queued_branch_error_type(self) -> None:
        """GH006 stderr produces error_type=queued_branch."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = (
            "remote: error: GH006: Protected branch update failed for refs/heads/main.\n"
            "remote: error: Changes must be made through a merge queue."
        )

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result["success"] is False
        assert result.get("error_type") == "queued_branch"

    def test_merge_queue_stderr_returns_queued_branch_error_type(self) -> None:
        """'protected by merge queue' stderr variant also produces queued_branch."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "remote: error: branch is protected by merge queue"

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
            result = push_to_remote("/clone", "/source", "main", protected_branches=[], force=True)

        assert result["success"] is False
        assert result.get("error_type") == "queued_branch"

    def test_gh006_non_force_also_returns_queued_branch(self) -> None:
        """GH006 fires for non-force pushes too (queue protects all push modes)."""
        mock_url = MagicMock()
        mock_url.returncode = 0
        mock_url.stdout = "git@github.com:org/repo.git\n"
        mock_url.stderr = ""

        mock_push = MagicMock()
        mock_push.returncode = 1
        mock_push.stderr = "remote: error: GH006: Protected branch update failed."

        with patch(
            "autoskillit.workspace.clone.subprocess.run", side_effect=[mock_url, mock_push]
        ):
            result = push_to_remote(
                "/clone", "/source", "main", protected_branches=[], force=False
            )

        assert result["success"] is False
        assert result.get("error_type") == "queued_branch"


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

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)

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

        shutil.rmtree(Path(clone_path).parent, ignore_errors=True)
