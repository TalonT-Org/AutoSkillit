"""Remote resolution tests — _probe_single_remote, _probe_clone_source_url, clone URL resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.workspace._clone_remote import (
    _probe_clone_source_url,
    _probe_single_remote,
)
from autoskillit.workspace.clone import clone_repo

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


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
            cmd = clone_calls[0][0][0]
            # Extract positional args by skipping flags and their values.
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
        resolution = _probe_single_remote(local_with_remote, "origin")
        assert resolution.reason == "ok"
        assert resolution.url == str(bare_remote)
        assert resolution.stderr == ""

    def test_probe_single_remote_returns_no_origin_reason_when_no_remote(
        self, git_repo: Path
    ) -> None:
        resolution = _probe_single_remote(git_repo, "origin")
        assert resolution.reason == "no_origin"
        assert resolution.url == ""

    def test_probe_single_remote_returns_timeout_reason_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "autoskillit.workspace._clone_remote.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            resolution = _probe_single_remote(tmp_path, "origin")
        assert resolution.reason == "timeout"
        assert resolution.url == ""

    def test_probe_single_remote_returns_error_reason_on_non_zero_rc(self, tmp_path: Path) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"
        with patch("autoskillit.workspace._clone_remote.subprocess.run", return_value=mock_result):
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
        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        source = tmp_path / "source"
        subprocess.run(["git", "clone", str(bare), str(source)], check=True, capture_output=True)
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
