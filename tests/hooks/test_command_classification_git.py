"""Tests for git command classification primitives in _command_classification."""

from __future__ import annotations

import pytest

from autoskillit.hooks._command_classification import (
    extract_git_subcommand_and_flags,
    is_allowed_protected_path_metadata_command,
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
        assert result == ("commit", ["--amend"])

    def test_global_flag_with_value(self):
        result = extract_git_subcommand_and_flags(
            ["git", "-C", "/path", "commit", "--amend", "--no-edit"]
        )
        assert result == ("commit", ["--amend", "--no-edit"])

    def test_push_force_with_lease(self):
        result = extract_git_subcommand_and_flags(["git", "push", "--force-with-lease"])
        assert result == ("push", ["--force-with-lease"])

    def test_add_flag(self):
        result = extract_git_subcommand_and_flags(["git", "add", "-u"])
        assert result == ("add", ["-u"])

    def test_full_path_git_reset_hard(self):
        result = extract_git_subcommand_and_flags(["/usr/bin/git", "reset", "--hard"])
        assert result == ("reset", ["--hard"])

    def test_no_pager_global_flag(self):
        result = extract_git_subcommand_and_flags(["git", "--no-pager", "log"])
        assert result == ("log", [])

    def test_no_subcommand_returns_none(self):
        result = extract_git_subcommand_and_flags(["git"])
        assert result is None

    def test_not_git_returns_none(self):
        result = extract_git_subcommand_and_flags(["gh", "pr", "create"])
        assert result is None

    def test_git_c_flag_skips_value(self):
        result = extract_git_subcommand_and_flags(["git", "-C", "/repo", "status"])
        assert result == ("status", [])

    def test_git_work_tree_skips_value(self):
        result = extract_git_subcommand_and_flags(["git", "--work-tree", "/work", "checkout", "."])
        assert result == ("checkout", ["."])

    def test_git_bare_flag(self):
        result = extract_git_subcommand_and_flags(["git", "--bare", "log"])
        assert result == ("log", [])


class TestCheckIgnoreMetadataClassification:
    """Guard-compatibility fixture for the dry-walkthrough prescribed check-ignore form.

    Binds `git check-ignore -v {path}` (SKILL.md:152) to
    is_allowed_protected_path_metadata_command so the guard classifier and the
    skill's committability contract cannot silently drift apart again.
    """

    def test_prescribed_dry_walkthrough_form(self):
        segment = ["git", "check-ignore", "-v", "src/autoskillit/recipes/remediation.yaml"]
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_verbose_long_flag(self):
        segment = ["git", "check-ignore", "--verbose", "src/autoskillit/recipes/remediation.yaml"]
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_no_index_flag(self):
        segment = [
            "git",
            "check-ignore",
            "-v",
            "--no-index",
            "src/autoskillit/recipes/remediation.yaml",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_double_dash_separator(self):
        segment = ["git", "check-ignore", "-v", "--", "src/autoskillit/recipes/remediation.yaml"]
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_multiple_literal_paths(self):
        segment = [
            "git",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
            "src/autoskillit/agents/explorer.md",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_no_paths_is_denied(self):
        assert is_allowed_protected_path_metadata_command(["git", "check-ignore", "-v"]) is False

    def test_stdin_flag_denied(self):
        segment = ["git", "check-ignore", "-v", "--stdin"]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_quiet_flag_denied(self):
        segment = ["git", "check-ignore", "-q", "src/autoskillit/recipes/remediation.yaml"]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_global_c_flag_before_subcommand_denied(self):
        segment = [
            "git",
            "-c",
            "core.excludesFile=src/autoskillit/recipes/remediation.yaml",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_global_git_dir_flag_denied(self):
        segment = [
            "git",
            "--git-dir",
            "/tmp/.git",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_global_work_tree_flag_denied(self):
        segment = [
            "git",
            "--work-tree",
            "/tmp",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_bare_flag_denied(self):
        segment = [
            "git",
            "--bare",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_redirect_target_denied(self):
        segment = [
            "git",
            "check-ignore",
            "-v",
            "src/autoskillit/recipes/remediation.yaml",
            ">",
            "/tmp/out",
        ]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_shell_variable_path_denied(self):
        segment = ["git", "check-ignore", "-v", "$VAR"]
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_content_bearing_subcommand_still_denied(self):
        segment = ["git", "show", "HEAD:src/autoskillit/recipes/remediation.yaml"]
        assert is_allowed_protected_path_metadata_command(segment) is False
