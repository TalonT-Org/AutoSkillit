"""Tests for git command classification primitives in _command_classification."""

from __future__ import annotations

import pytest

from autoskillit.hooks._runtime._command_classification import (
    extract_git_subcommand_and_flags,
    is_git_command,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


class TestIsGitCommand:
    def test_git_commit(self):
        assert is_git_command(["git", "commit", "-m", "msg"]) is True

    def test_gh_is_not_git(self):
        assert is_git_command(["gh", "pr", "create"]) is False

    def test_echo_is_not_git(self):
        assert is_git_command(["echo", "git"]) is False

    def test_full_path_git(self):
        assert is_git_command(["/usr/bin/git", "status"]) is True

    def test_full_path_local_git(self):
        assert is_git_command(["/usr/local/bin/git", "push"]) is True

    def test_empty_segment(self):
        assert is_git_command([]) is False

    def test_gitignore_is_not_git(self):
        assert is_git_command(["gitignore", "status"]) is False


class TestExtractGitSubcommandAndFlags:
    def test_simple_commit_amend(self):
        result = extract_git_subcommand_and_flags(["git", "commit", "--amend"])
        assert result is not None
        assert result.subcommand == "commit"
        assert result.flags == ["--amend"]
        assert result.global_flags == []
        assert result.prefix_tokens == []

    def test_global_flag_with_value(self):
        result = extract_git_subcommand_and_flags(
            ["git", "-C", "/path", "commit", "--amend", "--no-edit"]
        )
        assert result is not None
        assert result.subcommand == "commit"
        assert result.flags == ["--amend", "--no-edit"]
        assert result.global_flags == ["-C"]

    def test_push_force_with_lease(self):
        result = extract_git_subcommand_and_flags(["git", "push", "--force-with-lease"])
        assert result is not None
        assert result.subcommand == "push"
        assert result.flags == ["--force-with-lease"]

    def test_add_flag(self):
        result = extract_git_subcommand_and_flags(["git", "add", "-u"])
        assert result is not None
        assert result.subcommand == "add"
        assert result.flags == ["-u"]

    def test_full_path_git_reset_hard(self):
        result = extract_git_subcommand_and_flags(["/usr/bin/git", "reset", "--hard"])
        assert result is not None
        assert result.subcommand == "reset"
        assert result.flags == ["--hard"]

    def test_no_pager_global_flag(self):
        result = extract_git_subcommand_and_flags(["git", "--no-pager", "log"])
        assert result is not None
        assert result.subcommand == "log"
        assert result.flags == []
        assert result.global_flags == ["--no-pager"]

    def test_no_subcommand_returns_none(self):
        result = extract_git_subcommand_and_flags(["git"])
        assert result is None

    def test_not_git_returns_none(self):
        result = extract_git_subcommand_and_flags(["gh", "pr", "create"])
        assert result is None

    def test_git_c_flag_skips_value(self):
        result = extract_git_subcommand_and_flags(["git", "-C", "/repo", "status"])
        assert result is not None
        assert result.subcommand == "status"
        assert result.flags == []
        assert result.global_flags == ["-C"]

    def test_git_work_tree_skips_value(self):
        result = extract_git_subcommand_and_flags(["git", "--work-tree", "/work", "checkout", "."])
        assert result is not None
        assert result.subcommand == "checkout"
        assert result.flags == ["."]
        assert result.global_flags == ["--work-tree"]

    def test_git_bare_flag(self):
        result = extract_git_subcommand_and_flags(["git", "--bare", "log"])
        assert result is not None
        assert result.subcommand == "log"
        assert result.flags == []
        assert result.global_flags == ["--bare"]
