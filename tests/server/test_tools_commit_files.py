"""Tests for the commit_files MCP tool and validate_commit_paths helper."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime

import pytest

from autoskillit.core import CommitFailureClass, WorkspaceOutcomeKind
from autoskillit.server.git import validate_commit_paths
from autoskillit.server.tools.tools_workspace import commit_files
from tests.conftest import _make_result
from tests.server._outcome_ledger_fakes import _FailingLedger, _RecordingLedger

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# validate_commit_paths — pure function tests
# ---------------------------------------------------------------------------


class TestValidateCommitPaths:
    def test_relative_path_inside_cwd_is_valid(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "file.py").touch()
        assert validate_commit_paths(str(tmp_path), ["src/file.py"]) is None

    def test_nested_relative_path_is_valid(self, tmp_path):
        assert validate_commit_paths(str(tmp_path), ["a/b/c.py"]) is None

    def test_relative_path_escaping_cwd_is_rejected(self, tmp_path):
        result = validate_commit_paths(str(tmp_path), ["../outside.py"])
        assert result is not None
        assert "escapes cwd" in result

    def test_relative_path_with_multiple_parent_traversals_is_rejected(self, tmp_path):
        # Nested cwd so ../../.. actually escapes the filesystem root ancestry too
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        result = validate_commit_paths(str(nested), ["../../../etc/passwd"])
        assert result is not None
        assert "escapes cwd" in result

    def test_absolute_path_outside_worktree_is_rejected(self, tmp_path):
        outside = tmp_path.parent / "definitely_outside_dir" / "file.py"
        result = validate_commit_paths(str(tmp_path), [str(outside)])
        assert result is not None
        assert "escapes cwd" in result

    def test_absolute_path_inside_worktree_is_valid(self, tmp_path):
        inside = tmp_path / "file.py"
        assert validate_commit_paths(str(tmp_path), [str(inside)]) is None

    def test_path_with_dot_git_component_is_rejected(self, tmp_path):
        result = validate_commit_paths(str(tmp_path), [".git/config"])
        assert result is not None
        assert ".git component" in result

    def test_path_with_dot_git_component_nested_is_rejected(self, tmp_path):
        result = validate_commit_paths(str(tmp_path), ["sub/.git/hooks/pre-commit"])
        assert result is not None
        assert ".git component" in result

    def test_first_violation_is_reported_when_multiple_paths_given(self, tmp_path):
        result = validate_commit_paths(str(tmp_path), ["ok/file.py", "../escape.py"])
        assert result is not None
        assert "escape.py" in result

    def test_valid_paths_all_pass(self, tmp_path):
        assert validate_commit_paths(str(tmp_path), ["a.py", "b/c.py", "d/e/f.py"]) is None

    def test_cwd_itself_is_a_valid_path(self, tmp_path):
        assert validate_commit_paths(str(tmp_path), ["."]) is None


# ---------------------------------------------------------------------------
# commit_files tool — happy path
# ---------------------------------------------------------------------------


class TestCommitFilesHappyPath:
    @pytest.mark.anyio
    async def test_stages_commits_and_returns_sha(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))  # git add
        tool_ctx.runner.push(_make_result(0, "", ""))  # git commit
        tool_ctx.runner.push(_make_result(0, "abc123def456\n", ""))  # rev-parse HEAD

        result = json.loads(
            await commit_files(paths=["file.py"], message="fix: thing", cwd=str(wt))
        )

        assert result == {"success": True, "commit_sha": "abc123def456"}

    @pytest.mark.anyio
    async def test_git_add_invoked_with_correct_paths(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))

        await commit_files(paths=["a.py", "b.py"], message="msg", cwd=str(wt))

        add_call = tool_ctx.runner.call_args_list[0][0]
        assert add_call == ["git", "-C", str(wt), "add", "--", "a.py", "b.py"]

    @pytest.mark.anyio
    async def test_git_commit_invoked_with_message(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))

        await commit_files(paths=["a.py"], message="fix: the bug", cwd=str(wt))

        commit_call = tool_ctx.runner.call_args_list[1][0]
        assert commit_call == ["git", "-C", str(wt), "commit", "-m", "fix: the bug"]
        assert "--no-verify" not in commit_call


# ---------------------------------------------------------------------------
# commit_files tool — containment rejections (never invokes subprocess)
# ---------------------------------------------------------------------------


class TestCommitFilesContainmentRejections:
    @pytest.mark.anyio
    async def test_cwd_missing_returns_error(self, tool_ctx, tmp_path):
        missing = tmp_path / "does-not-exist"
        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(missing)))
        assert result["success"] is False
        assert "cwd does not exist" in result["error"]

    @pytest.mark.anyio
    async def test_empty_paths_returns_error(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        result = json.loads(await commit_files(paths=[], message="msg", cwd=str(wt)))
        assert result["success"] is False
        assert "paths list is empty" in result["error"]

    @pytest.mark.anyio
    async def test_relative_path_escaping_cwd_is_rejected(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        result = json.loads(await commit_files(paths=["../escape.py"], message="msg", cwd=str(wt)))
        assert result["success"] is False
        assert "escapes cwd" in result["error"]
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_absolute_path_outside_worktree_is_rejected(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside" / "file.py"
        result = json.loads(await commit_files(paths=[str(outside)], message="msg", cwd=str(wt)))
        assert result["success"] is False
        assert "escapes cwd" in result["error"]
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_dot_git_component_path_is_rejected(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        result = json.loads(await commit_files(paths=[".git/config"], message="msg", cwd=str(wt)))
        assert result["success"] is False
        assert ".git component" in result["error"]
        assert tool_ctx.runner.call_args_list == []


# ---------------------------------------------------------------------------
# commit_files tool — git add failure
# ---------------------------------------------------------------------------


class TestCommitFilesGitAddFailure:
    @pytest.mark.anyio
    async def test_git_add_failure_returns_error_without_committing(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(1, "", "fatal: pathspec did not match"))

        result = json.loads(await commit_files(paths=["missing.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert "git add failed" in result["error"]
        assert len(tool_ctx.runner.call_args_list) == 1


# ---------------------------------------------------------------------------
# commit_files tool — pre-commit invocation
# ---------------------------------------------------------------------------


class TestCommitFilesPreCommitInvocation:
    @pytest.mark.anyio
    async def test_pre_commit_invoked_when_config_present(self, tool_ctx, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")

        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add
        tool_ctx.runner.push(_make_result(0, "tree-before\n", ""))  # git write-tree
        tool_ctx.runner.push(_make_result(0, "", ""))  # pre-commit run --files (pass)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git commit
        tool_ctx.runner.push(_make_result(0, "sha123\n", ""))  # rev-parse HEAD

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is True
        pre_commit_call = tool_ctx.runner.call_args_list[2][0]
        assert pre_commit_call == ["/usr/bin/pre-commit", "run", "--files", "a.py"]
        assert "--no-verify" not in pre_commit_call

    @pytest.mark.anyio
    async def test_pre_commit_not_invoked_without_config(self, tool_ctx, tmp_path, monkeypatch):
        wt = tmp_path / "wt"
        wt.mkdir()
        # No .pre-commit-config.yaml present.
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add
        tool_ctx.runner.push(_make_result(0, "", ""))  # git commit
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))  # rev-parse HEAD

        await commit_files(paths=["a.py"], message="msg", cwd=str(wt))

        # Only 3 subprocess calls: add, commit, rev-parse — no pre-commit invocation.
        assert len(tool_ctx.runner.call_args_list) == 3
        assert tool_ctx.runner.call_args_list[1][0] == [
            "git",
            "-C",
            str(wt),
            "commit",
            "-m",
            "msg",
        ]

    @pytest.mark.anyio
    async def test_pre_commit_via_uv_run_when_uv_lock_present(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        (wt / "uv.lock").write_text("")

        def _which(cmd, **kw):
            if cmd == "uv":
                return "/usr/bin/uv"
            if cmd == "pre-commit":
                return "/usr/bin/pre-commit"
            return None

        monkeypatch.setattr(shutil, "which", _which)

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add
        tool_ctx.runner.push(_make_result(0, "tree-before\n", ""))  # git write-tree
        tool_ctx.runner.push(_make_result(0, "", ""))  # uv run pre-commit run --files
        tool_ctx.runner.push(_make_result(0, "", ""))  # git commit
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))  # rev-parse HEAD

        await commit_files(paths=["a.py"], message="msg", cwd=str(wt))

        pre_commit_call = tool_ctx.runner.call_args_list[2][0]
        assert pre_commit_call == ["/usr/bin/uv", "run", "pre-commit", "run", "--files", "a.py"]


# ---------------------------------------------------------------------------
# commit_files tool — hook auto-fix retry path
# ---------------------------------------------------------------------------


class TestCommitFilesHookAutoFixRetry:
    @pytest.mark.anyio
    async def test_hook_autofix_re_adds_and_retries_commit_once(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add (initial)
        tool_ctx.runner.push(_make_result(0, "tree-before\n", ""))  # git write-tree
        tool_ctx.runner.push(
            _make_result(1, "", "files were modified by this hook")
        )  # pre-commit fails (auto-fix)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git add (re-add after auto-fix)
        tool_ctx.runner.push(_make_result(0, "tree-after\n", ""))  # git write-tree changed
        tool_ctx.runner.push(_make_result(0, "", ""))  # pre-commit run --files (retry, passes)
        tool_ctx.runner.push(_make_result(0, "", ""))  # git commit
        tool_ctx.runner.push(_make_result(0, "sha456\n", ""))  # rev-parse HEAD

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result == {"success": True, "commit_sha": "sha456"}
        calls = [c[0] for c in tool_ctx.runner.call_args_list]
        assert calls == [
            ["git", "-C", str(wt), "add", "--", "a.py"],
            ["git", "-C", str(wt), "write-tree"],
            ["/usr/bin/pre-commit", "run", "--files", "a.py"],
            ["git", "-C", str(wt), "add", "--", "a.py"],
            ["git", "-C", str(wt), "write-tree"],
            ["/usr/bin/pre-commit", "run", "--files", "a.py"],
            ["git", "-C", str(wt), "commit", "-m", "msg"],
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
        ]

    @pytest.mark.anyio
    async def test_unchanged_staged_tree_skips_retry_and_preserves_both_streams(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same-tree\n", ""))
        tool_ctx.runner.push(_make_result(1, "stdout detail", "stderr detail"))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same-tree\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert result["failure_class"] == CommitFailureClass.HOOK_REJECTED
        assert result["retry_skipped_reason"] == ("staged tree unchanged after pre-commit failure")
        assert "stderr detail\nstdout detail" in result["error"]
        assert len(tool_ctx.runner.call_args_list) == 5

    @pytest.mark.anyio
    async def test_empty_hook_streams_still_name_failing_phase(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )
        ledger = _RecordingLedger()
        tool_ctx.workspace_outcome_ledger = ledger

        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same-tree\n", ""))
        tool_ctx.runner.push(_make_result(1, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same-tree\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["error"] == "pre-commit failed: pre-commit exited with status 1"
        assert not result["error"].endswith(": ")
        assert result["failure_class"] == CommitFailureClass.HOOK_REJECTED
        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.kind is WorkspaceOutcomeKind.COMMIT_ATTEMPT
        assert record.succeeded is False
        assert record.failure_class is CommitFailureClass.HOOK_REJECTED
        assert record.commit_sha is None

    @pytest.mark.anyio
    async def test_failed_write_tree_proof_does_not_skip_retry(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(1, "", "write-tree failed"))
        tool_ctx.runner.push(_make_result(1, "", "files were modified"))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same-tree\n", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result == {"success": True, "commit_sha": "sha"}
        assert [call[0] for call in tool_ctx.runner.call_args_list].count(
            ["/usr/bin/pre-commit", "run", "--files", "a.py"]
        ) == 2

    @pytest.mark.anyio
    async def test_pre_commit_calls_inherit_environment_and_pin_workspace_uv_cache(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setenv("AUTOSKILLIT_TEST_PARENT_ENV", "kept")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        for result in (
            _make_result(0, "", ""),
            _make_result(0, "before\n", ""),
            _make_result(1, "", "files were modified"),
            _make_result(0, "", ""),
            _make_result(0, "after\n", ""),
            _make_result(0, "", ""),
            _make_result(0, "", ""),
            _make_result(0, "sha\n", ""),
        ):
            tool_ctx.runner.push(result)

        await commit_files(paths=["a.py"], message="msg", cwd=str(wt))

        hook_calls = [
            call for call in tool_ctx.runner.call_args_list if call[0][0] == "/usr/bin/pre-commit"
        ]
        assert len(hook_calls) == 2
        expected_cache = wt / ".autoskillit" / "temp" / "uv-cache"
        for _cmd, _cwd, _timeout, kwargs in hook_calls:
            assert kwargs["env"]["AUTOSKILLIT_TEST_PARENT_ENV"] == "kept"
            assert kwargs["env"]["UV_CACHE_DIR"] == str(expected_cache)

    @pytest.mark.parametrize(
        ("diagnostic", "expected_class"),
        [
            ("Read-only file system", CommitFailureClass.HOOK_INFRASTRUCTURE),
            ("os error 30", CommitFailureClass.HOOK_INFRASTRUCTURE),
            (
                "Permission denied: /tmp/uv-cache",
                CommitFailureClass.HOOK_INFRASTRUCTURE,
            ),
            ("Permission denied: src/file.py", CommitFailureClass.HOOK_REJECTED),
        ],
    )
    @pytest.mark.anyio
    async def test_observed_infrastructure_signatures_are_classified(
        self, tool_ctx, tmp_path, monkeypatch, diagnostic, expected_class
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same\n", ""))
        tool_ctx.runner.push(_make_result(1, diagnostic, ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "same\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["failure_class"] == expected_class
        assert diagnostic in result["error"]


# ---------------------------------------------------------------------------
# commit_files tool — hard hook failure
# ---------------------------------------------------------------------------


class TestCommitFilesHardHookFailure:
    @pytest.mark.anyio
    async def test_re_add_failure_after_hook_failure_returns_error(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add (initial)
        tool_ctx.runner.push(_make_result(0, "tree-before\n", ""))  # git write-tree
        tool_ctx.runner.push(_make_result(1, "", "hook failed hard"))  # pre-commit fails
        tool_ctx.runner.push(_make_result(1, "", "fatal: pathspec"))  # re-add fails

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert "pre-commit re-add failed" in result["error"]
        assert result["failure_class"] == CommitFailureClass.GIT_ADD_FAILED
        # Never reaches git commit.
        assert len(tool_ctx.runner.call_args_list) == 4

    @pytest.mark.anyio
    async def test_retry_pre_commit_still_failing_returns_error_no_commit(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []\n")
        monkeypatch.setattr(
            shutil,
            "which",
            lambda cmd, **kw: "/usr/bin/pre-commit" if cmd == "pre-commit" else None,
        )

        tool_ctx.runner.push(_make_result(0, "", ""))  # git add (initial)
        tool_ctx.runner.push(_make_result(0, "tree-before\n", ""))  # git write-tree
        tool_ctx.runner.push(_make_result(1, "", "lint error"))  # pre-commit fails
        tool_ctx.runner.push(_make_result(0, "", ""))  # re-add succeeds
        tool_ctx.runner.push(_make_result(0, "tree-after\n", ""))  # git write-tree changed
        tool_ctx.runner.push(_make_result(1, "", "lint error persists"))  # retry still fails

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert "pre-commit retry failed" in result["error"]
        assert "lint error persists" in result["error"]
        # No commit call occurred — only the hook transaction calls above.
        assert len(tool_ctx.runner.call_args_list) == 6
        for call in tool_ctx.runner.call_args_list:
            assert call[0][:3] != ["git", "-C", str(wt)] or "commit" not in call[0]


# ---------------------------------------------------------------------------
# commit_files tool — git commit failure (post pre-commit)
# ---------------------------------------------------------------------------


class TestCommitFilesGitCommitFailure:
    @pytest.mark.anyio
    async def test_git_commit_failure_returns_error(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))  # git add
        tool_ctx.runner.push(_make_result(1, "", "nothing to commit"))  # git commit fails

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert "git commit failed" in result["error"]
        assert "nothing to commit" in result["error"]


# ---------------------------------------------------------------------------
# commit_files tool — envelope shape parity with push_to_remote
# ---------------------------------------------------------------------------


class TestCommitFilesEnvelopeShape:
    @pytest.mark.anyio
    async def test_success_envelope_has_success_true_and_commit_sha(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "deadbeef\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert set(result.keys()) == {"success", "commit_sha"}
        assert result["success"] is True
        assert isinstance(result["commit_sha"], str)

    @pytest.mark.anyio
    async def test_failure_envelope_has_success_false_and_error(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(1, "", "boom"))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert set(result.keys()) == {"success", "error", "failure_class"}
        assert result["success"] is False
        assert isinstance(result["error"], str)

    @pytest.mark.anyio
    async def test_validation_failure_envelope_matches_shape(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()

        result = json.loads(await commit_files(paths=["../escape.py"], message="msg", cwd=str(wt)))

        assert set(result.keys()) == {"success", "error", "failure_class"}
        assert result["success"] is False


class TestCommitFilesOutcomeAccounting:
    @pytest.mark.anyio
    async def test_success_records_one_realpath_utc_commit_outcome(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        ledger = _RecordingLedger()
        tool_ctx.workspace_outcome_ledger = ledger
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "abc123\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt / ".")))

        assert result == {"success": True, "commit_sha": "abc123"}
        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.workspace == os.path.realpath(wt)
        assert record.kind is WorkspaceOutcomeKind.COMMIT_ATTEMPT
        assert record.succeeded is True
        assert record.commit_sha == "abc123"
        assert record.failure_class is None
        assert datetime.fromisoformat(record.recorded_at).utcoffset() is not None

    @pytest.mark.anyio
    async def test_path_rejection_records_classified_failure_once(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        ledger = _RecordingLedger()
        tool_ctx.workspace_outcome_ledger = ledger

        result = json.loads(
            await commit_files(paths=["../outside.py"], message="msg", cwd=str(wt))
        )

        assert result["failure_class"] == CommitFailureClass.PATH_REJECTED
        assert len(ledger.records) == 1
        assert ledger.records[0].failure_class is CommitFailureClass.PATH_REJECTED
        assert ledger.records[0].succeeded is False

    @pytest.mark.anyio
    async def test_ledger_failure_after_commit_preserves_landed_sha_without_retry(
        self, tool_ctx, tmp_path
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        ledger = _FailingLedger()
        tool_ctx.workspace_outcome_ledger = ledger
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "landed-sha\n", ""))

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))

        assert result["success"] is False
        assert result["failure_class"] == CommitFailureClass.UNHANDLED
        assert result["commit_sha"] == "landed-sha"
        assert "outcome recording failed" in result["error"]
        assert ledger.calls == 1
        assert len(tool_ctx.runner.call_args_list) == 3


# ---------------------------------------------------------------------------
# commit_files tool — timing
# ---------------------------------------------------------------------------


class TestCommitFilesTiming:
    @pytest.mark.anyio
    async def test_step_name_records_timing(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))

        await commit_files(paths=["a.py"], message="msg", cwd=str(wt), step_name="commit")

        report = tool_ctx.timing_log.get_report()
        assert any(e["step_name"] == "commit" for e in report)

    @pytest.mark.anyio
    async def test_empty_step_name_skips_timing(self, tool_ctx, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "", ""))
        tool_ctx.runner.push(_make_result(0, "sha\n", ""))

        await commit_files(paths=["a.py"], message="msg", cwd=str(wt))

        assert tool_ctx.timing_log.get_report() == []

    @pytest.mark.anyio
    async def test_pre_commit_config_present_but_binary_missing_returns_error(
        self, tool_ctx, tmp_path, monkeypatch
    ):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".pre-commit-config.yaml").write_text("repos: []")
        (wt / "a.py").write_text("x = 1\n")
        tool_ctx.runner.push(_make_result(0, "", ""))

        monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)

        result = json.loads(await commit_files(paths=["a.py"], message="msg", cwd=str(wt)))
        assert result["success"] is False
        assert "pre-commit" in result["error"]
        assert "binary" in result["error"] or "not found" in result["error"]
        assert result["failure_class"] == CommitFailureClass.TOOLING_MISSING
