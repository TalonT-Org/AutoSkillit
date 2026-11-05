"""Tests for the git_ops_guard PreToolUse hook.

Guards against destructive git operations (commit --amend, push --force,
reset --hard, clean -f, checkout .) in headless skill sessions.

Pattern mirrors test_pr_create_guard.py.
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

# The sys.path bootstrap needed for _git_command_classification.py's own
# internal bare-name sibling imports (_github_mutation_analysis,
# _command_classification) is centralized in tests/conftest.py -- it must
# run before this module's own top-level imports, which a fixture cannot do.
from autoskillit.hooks._classification._option_grammar import OptionArity, OptionDef
from autoskillit.hooks._runtime._git_command_classification import _classify_fetch
from autoskillit.hooks._runtime._git_option_tables import _GIT_FETCH_FLAG_SPEC
from autoskillit.hooks.guards import git_ops_guard
from tests._git_topology import commit_file, init_checkout
from tests.infra._git_ops_guard_harness import (
    _TOOL_NAME,
    _checked_out_ref_result,
    _git,
    _guard_exit_code,
    _guard_verdict,
    _is_denied,
    _run_guard,
    build_linked_repo,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


@pytest.fixture
def linked_repo(tmp_path: Path) -> dict[str, Path | str]:
    return build_linked_repo(tmp_path)


def _assert_ref_unchanged(repo: Path, ref: str, old_sha: str) -> None:
    assert _git(repo, "rev-parse", ref) == old_sha


# ---------------------------------------------------------------------------
# Denied cases: commit --amend
# ---------------------------------------------------------------------------


class TestGitAmendDenied:
    def test_denies_git_commit_amend(self, tmp_path):
        out = _run_guard("git commit --amend", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_commit_amend_no_edit(self, tmp_path):
        out = _run_guard("git commit --amend --no-edit", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_with_global_flag_commit_amend(self, tmp_path):
        out = _run_guard("git -C /path commit --amend", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_full_path_git_commit_amend(self, tmp_path):
        out = _run_guard("/usr/bin/git commit --amend", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_deny_reason_mentions_operation(self, tmp_path):
        out = _run_guard("git commit --amend", kitchen_open=True, tmpdir=tmp_path)
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "commit" in reason or "amend" in reason or "destructive" in reason.lower()


# ---------------------------------------------------------------------------
# Denied cases: push --force
# ---------------------------------------------------------------------------


class TestGitPushForceDenied:
    def test_denies_git_push_force(self, tmp_path):
        out = _run_guard("git push --force", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_push_force_short(self, tmp_path):
        out = _run_guard("git push -f", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_push_force_with_lease(self, tmp_path):
        out = _run_guard("git push --force-with-lease", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_full_path_git_push_force(self, tmp_path):
        out = _run_guard("/usr/local/bin/git push --force", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_push_force_with_unresolved_global_flag(self, tmp_path: Path) -> None:
        """An unrecognized git global flag (`--frobnicate`) before `push --force`
        makes extract_git_subcommand_and_flags return ("<unresolved>", ...):
        the flag's value cannot be told from the subcommand, so the invocation
        is denied fail-closed rather than read as a no-op that matches nothing
        in _BLOCKED_GIT_OPS. The checked-out-ref preflight, which runs first
        in a kitchen-open session, issues the deny with an unresolved value.
        """
        out = _run_guard(
            "git --frobnicate push --force",
            kitchen_open=True,
            tmpdir=tmp_path,
        )
        assert _guard_verdict(out) == "checked-out-ref"
        assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"


# ---------------------------------------------------------------------------
# Denied cases: reset --hard
# ---------------------------------------------------------------------------


class TestGitResetHardDenied:
    def test_denies_git_reset_hard(self, tmp_path):
        out = _run_guard("git reset --hard", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_allows_git_reset_soft(self, tmp_path):
        out = _run_guard("git reset --soft HEAD~1", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "command",
        [
            # Read forms: 1 positional must not IndexError (would fail-open
            # since IndexError is not in the preflight except tuple).
            "git symbolic-ref HEAD",
            "git symbolic-ref --short HEAD",
            "git symbolic-ref -q HEAD",
        ],
    )
    def test_allows_symbolic_ref_read_forms(self, tmp_path, command: str) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path)
        # Must not raise (exit 0, no JSON, no exception). An IndexError here
        # would surface as exit 1, which PreToolUse treats as non-blocking
        # (silent allow).
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Denied cases: clean
# ---------------------------------------------------------------------------


class TestGitCleanDenied:
    def test_denies_git_clean_fd(self, tmp_path):
        out = _run_guard("git clean -fd", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_clean_f(self, tmp_path):
        out = _run_guard("git clean -f", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Denied cases: checkout destructive
# ---------------------------------------------------------------------------


class TestGitCheckoutDestructiveDenied:
    def test_denies_git_checkout_dot(self, tmp_path):
        out = _run_guard("git checkout .", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_git_checkout_dashdash_dot(self, tmp_path):
        out = _run_guard("git checkout -- .", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_allows_git_checkout_branch(self, tmp_path):
        out = _run_guard("git checkout somebranch", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Allowed cases
# ---------------------------------------------------------------------------


class TestGitOpsGuardAllowed:
    def test_allows_git_commit_with_message(self, tmp_path):
        out = _run_guard('git commit -m "fix: something"', kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_git_push_to_remote(self, tmp_path):
        """A push to a configured remote name is judged by the remote's URL, not
        by treating the name as a filesystem path: a genuine network remote is a
        different repository, so the push is allowed rather than denied as an
        uncertain destination. Requires a real repository topology so that the
        push reaches `_classify_push`; outside any repository the preflight has
        nothing to classify.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Guard Test")
        _git(repo, "config", "user.email", "guard@example.invalid")
        (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "initial")
        _git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
        out = _run_guard("git push origin main", kitchen_open=True, tmpdir=repo)
        assert out.strip() == ""

    def test_allows_git_status(self, tmp_path):
        out = _run_guard("git status", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_quoted_git_amend_string(self, tmp_path):
        out = _run_guard('echo "git commit --amend"', kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_when_kitchen_closed(self, tmp_path):
        out = _run_guard("git commit --amend", kitchen_open=False, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_npm_commands_naming_neither_git_nor_ref_storage_are_allowed(self, tmp_path):
        """A command that names neither Git nor ref storage cannot reach a ref,
        so npm version queries and script runs are allowed."""
        out = _run_guard("npm --version", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

        out = _run_guard("npm run build", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Bash tool format
# ---------------------------------------------------------------------------


class TestBashToolFormat:
    def test_denies_via_bash_tool(self, tmp_path):
        out = _run_guard(
            "git commit --amend", kitchen_open=True, tmpdir=tmp_path, use_bash_key=True
        )
        assert _is_denied(out)

    def test_allows_safe_git_via_bash_tool(self, tmp_path):
        out = _run_guard("git status", kitchen_open=True, tmpdir=tmp_path, use_bash_key=True)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Exemptions: session type
# ---------------------------------------------------------------------------


class TestOrchestratorSessionExemption:
    def test_orchestrator_session_allowed(self, tmp_path):
        out = _run_guard(
            "git commit --amend",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="orchestrator",
        )
        assert out.strip() == "", "Orchestrator session must be allowed"

    def test_skill_session_denied(self, tmp_path):
        out = _run_guard(
            "git commit --amend",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="skill",
        )
        assert _is_denied(out), "Skill session must be denied"

    def test_no_session_type_denied(self, tmp_path):
        out = _run_guard(
            "git commit --amend",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type=None,
        )
        assert _is_denied(out), "Missing session type must be denied"


# ---------------------------------------------------------------------------
# Fail-open: malformed input
# ---------------------------------------------------------------------------


class TestGitOpsGuardEdgeCases:
    def test_fails_open_on_malformed_stdin(self, tmp_path):
        out = _run_guard("", kitchen_open=False, tmpdir=tmp_path, raw_stdin="not-json{{{")
        assert out.strip() == "", "Malformed JSON must fail open"

    def test_fails_open_on_non_object_stdin(self, tmp_path):
        out = _run_guard("", kitchen_open=False, tmpdir=tmp_path, raw_stdin="[]")
        assert out.strip() == "", "Non-object JSON must fail open"

    def test_fails_open_on_missing_cmd_field(self, tmp_path):
        stdin = json.dumps({"tool_name": _TOOL_NAME, "tool_input": {}})
        out = _run_guard("", kitchen_open=False, tmpdir=tmp_path, raw_stdin=stdin)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Interpreter wrap and nested shell
# ---------------------------------------------------------------------------


class TestInterpreterAndNestedShell:
    def test_denies_interpreter_wrapped_git_amend(self, tmp_path):
        cmd = "python3 -c \"import subprocess; subprocess.run(['git', 'commit', '--amend'])\""
        out = _run_guard(cmd, kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_nested_shell_git_amend(self, tmp_path):
        cmd = 'bash -c "git commit --amend"'
        out = _run_guard(cmd, kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_env_prefix_git_amend(self, tmp_path):
        # contains_blocked_git_op resolves the verb through the evaluated-segment
        # walk, which skips POSIX assignments the same way the checked-out-ref
        # preflight does, so an env-prefixed invocation (VAR=1 git ...) is seen.
        out = _run_guard("VAR=1 git commit --amend", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Stdin-literal (heredoc/herestring/pipe) consumer recognition — rectify #4941 Part A
# ---------------------------------------------------------------------------


class TestStdinLiteralConsumers:
    """One deny per shape family on the headless blocklist path, checked-out-ref
    preflight coverage on the real-repository path, and the inert-body allows
    that prove Defect A's carve-out (rectify #4941 Part A)."""

    @pytest.mark.parametrize(
        "cmd",
        [
            pytest.param("bash <<'EOF'\ngit push --force origin main\nEOF", id="heredoc-quoted"),
            pytest.param("bash <<EOF\ngit push --force origin main\nEOF", id="heredoc-unquoted"),
            pytest.param("bash <<-'EOF'\n\tgit push --force origin main\n\tEOF", id="heredoc-tab"),
            pytest.param("bash <<< 'git push --force origin main'", id="herestring"),
            pytest.param(
                "cat <<'EOF' | bash\ngit push --force origin main\nEOF", id="cat-pipe-bash"
            ),
            pytest.param(
                "sudo bash <<'EOF'\ngit push --force origin main\nEOF", id="sudo-bash-heredoc"
            ),
            pytest.param(
                "python3 - <<'EOF'\nimport subprocess\n"
                "subprocess.run(['git','push','--force','origin','main'])\nEOF",
                id="python-dash-heredoc",
            ),
        ],
    )
    def test_denies_push_force_through_every_shape_family(self, tmp_path, cmd: str) -> None:
        out = _run_guard(cmd, kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_denies_bash_heredoc_update_ref_delete_preflight(self, tmp_path) -> None:
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        out = _run_guard(
            "bash <<'EOF'\ngit update-ref -d refs/heads/develop\nEOF",
            kitchen_open=True,
            tmpdir=tmp_path,
        )
        assert _is_denied(out)

    def test_denies_python_heredoc_update_ref_delete_preflight(self, tmp_path) -> None:
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        cmd = (
            "python3 - <<'EOF'\nimport subprocess\n"
            "subprocess.run(['git','update-ref','-d','refs/heads/develop'])\nEOF"
        )
        out = _run_guard(cmd, kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    @pytest.mark.parametrize("include_execution_cwd", [True, False])
    def test_denies_raw_write_verb_via_heredoc_preflight(
        self, tmp_path, include_execution_cwd: bool
    ) -> None:
        """A raw-write verb (`rm`) delivered through a heredoc against a ref
        file must deny with or without a resolvable execution_cwd."""
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        out = _run_guard(
            "bash <<'EOF'\nrm .git/refs/heads/develop\nEOF",
            kitchen_open=True,
            tmpdir=tmp_path,
            include_execution_cwd=include_execution_cwd,
        )
        assert _is_denied(out)

    def test_allows_piped_install_heredoc_naming_neither_git_nor_ref_storage(
        self, tmp_path
    ) -> None:
        """A piped `install` with relative operands and no execution cwd names no
        ref storage, so it cannot reach a ref and is allowed."""
        out = _run_guard(
            "cat <<'EOF' | bash\ninstall source destination\nEOF",
            kitchen_open=True,
            tmpdir=tmp_path,
            include_execution_cwd=False,
        )
        assert out.strip() == ""

    def test_allows_inert_backtick_heredoc_git_push(self, tmp_path) -> None:
        """cat-redirect-heredoc-inline-backtick: an inert `cat` body mentioning
        `git push --force` inside a single backtick pair must not deny."""
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        out = _run_guard(
            "cat > f.md <<'EOF'\nrun `git push --force origin main` here\nEOF",
            kitchen_open=True,
            tmpdir=tmp_path,
        )
        assert out.strip() == ""

    @pytest.mark.parametrize(
        ("command", "include_execution_cwd"),
        [
            ("cat > f.md <<< 'git push --force origin main'", True),
            ("cat <<< 'git push --force origin main'", False),
        ],
    )
    def test_allows_inert_herestring_git_push(
        self, tmp_path, command: str, include_execution_cwd: bool
    ) -> None:
        """A `cat` herestring containing an inert git push must not deny."""
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        out = _run_guard(
            command,
            kitchen_open=True,
            tmpdir=tmp_path,
            include_execution_cwd=include_execution_cwd,
        )
        assert out.strip() == ""

    def test_allows_inert_fenced_heredoc_gh_api(self, tmp_path) -> None:
        """cat-redirect-heredoc-fenced: an inert `cat` body containing a fenced
        `gh api ...` example must not deny (git_ops_guard only cares about git,
        but this pins the same live-text projection github_mutation_guard uses)."""
        _git(tmp_path, "init", "-b", "develop")
        _git(tmp_path, "config", "user.name", "Guard Test")
        _git(tmp_path, "config", "user.email", "guard@example.invalid")
        (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", "f.txt")
        _git(tmp_path, "commit", "-m", "initial")
        out = _run_guard(
            "cat > notes.md <<'EOF'\n```\ngit push --force origin main\n```\nEOF",
            kitchen_open=True,
            tmpdir=tmp_path,
        )
        assert out.strip() == ""

    def test_denies_cross_worktree_heredoc_branch_force(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        """The multi-worktree topology the #4588 immunity was built for, proven
        on the heredoc path: from the primary worktree, `git branch -f` fed
        through a heredoc targeting the branch checked out in the *other*
        worktree must still deny."""
        primary = linked_repo["primary"]
        linked = linked_repo["linked"]
        out = _run_guard(
            f"bash <<'EOF'\ngit branch -f review {linked_repo['new_sha']}\nEOF",
            kitchen_open=True,
            tmpdir=primary,
        )
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        refs = result["threatened_refs"]
        assert isinstance(refs, list)
        assert {row["target_ref"] for row in refs} >= {"refs/heads/review"}
        review_rows = [row for row in refs if row["target_ref"] == "refs/heads/review"]
        assert any(str(linked) in row["owner_paths"] for row in review_rows), (
            "must prove the ref was resolved to the OTHER (linked) worktree, "
            f"not just that some ref named refs/heads/review was threatened: {refs}"
        )


# ---------------------------------------------------------------------------
# Session scope: headless vs interactive
# ---------------------------------------------------------------------------


class TestHeadlessScope:
    def test_denies_in_headless_session(self, tmp_path):
        out = _run_guard("git commit --amend", kitchen_open=True, tmpdir=tmp_path, headless=True)
        assert _is_denied(out)

    def test_allows_in_interactive_session(self, tmp_path):
        out = _run_guard("git commit --amend", kitchen_open=True, tmpdir=tmp_path, headless=False)
        assert out.strip() == "", "Interactive sessions must be allowed"


class TestCheckedOutRefMutations:
    @pytest.mark.parametrize(
        ("command_template", "target_ref", "owner_key"),
        [
            ("git update-ref refs/heads/develop {new}", "refs/heads/develop", "primary"),
            ("git update-ref refs/heads/review {new}", "refs/heads/review", "linked"),
            ("git update-ref HEAD {new}", "refs/heads/review", "linked"),
            ("git branch -f develop {new}", "refs/heads/develop", "primary"),
            ("git checkout -B develop {new}", "refs/heads/develop", "primary"),
            ("git switch -C develop {new}", "refs/heads/develop", "primary"),
            ("git reset {new}", "refs/heads/review", "linked"),
            (
                "git symbolic-ref HEAD refs/heads/develop",
                "refs/heads/develop",
                "primary",
            ),
            # -m <reason> can appear before OR after <name> and <ref>; both
            # orderings must deny the target ref.
            (
                "git symbolic-ref HEAD refs/heads/develop -m switching",
                "refs/heads/develop",
                "primary",
            ),
            (
                "git symbolic-ref -m switching HEAD refs/heads/develop",
                "refs/heads/develop",
                "primary",
            ),
            (
                "git symbolic-ref HEAD -m switching refs/heads/develop",
                "refs/heads/develop",
                "primary",
            ),
        ],
    )
    def test_denies_bounded_direct_mutations_with_provenance(
        self,
        linked_repo: dict[str, Path | str],
        command_template: str,
        target_ref: str,
        owner_key: str,
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        old_sha = str(linked_repo["old_sha"])
        new_sha = str(linked_repo["new_sha"])

        out = _run_guard(
            command_template.format(new=new_sha),
            kitchen_open=True,
            tmpdir=linked,
        )

        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        assert result == {
            "attempted_value": (
                "refs/heads/develop"
                if command_template.startswith("git symbolic-ref")
                else new_sha
            ),
            "common_git_dir": linked_repo["common_git_dir"],
            "execution_cwd": str(linked),
            "requesting_worktree_path": str(linked),
            "resolved_attempted_new_sha": (
                old_sha if command_template.startswith("git symbolic-ref") else new_sha
            ),
            "session_id": "guard-test-session",
            "threatened_refs": [
                {
                    "old_sha": old_sha,
                    "owner_paths": [str(linked_repo[owner_key])],
                    "target_ref": target_ref,
                }
            ],
            "worktree_git_dir": linked_repo["worktree_git_dir"],
        }
        _assert_ref_unchanged(linked, target_ref, old_sha)

    def test_update_ref_no_deref_head_cannot_rewrite_per_worktree_head(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        before = _git(linked, "symbolic-ref", "HEAD")
        out = _run_guard(
            f"git update-ref --no-deref HEAD {linked_repo['new_sha']}",
            kitchen_open=True,
            tmpdir=linked,
        )
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        assert result == {
            "attempted_value": linked_repo["new_sha"],
            "common_git_dir": linked_repo["common_git_dir"],
            "execution_cwd": str(linked),
            "requesting_worktree_path": str(linked),
            "resolved_attempted_new_sha": linked_repo["new_sha"],
            "session_id": "guard-test-session",
            "threatened_refs": [
                {
                    "old_sha": linked_repo["old_sha"],
                    "owner_paths": [str(linked)],
                    "target_ref": "HEAD",
                }
            ],
            "worktree_git_dir": linked_repo["worktree_git_dir"],
        }
        assert _git(linked, "symbolic-ref", "HEAD") == before

    def test_bash_uses_top_level_payload_cwd(self, linked_repo: dict[str, Path | str]) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            f"git update-ref refs/heads/develop {linked_repo['new_sha']}",
            kitchen_open=True,
            tmpdir=linked,
            use_bash_key=True,
        )
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        assert result["execution_cwd"] == str(linked)
        assert result["requesting_worktree_path"] == str(linked)

    @pytest.mark.parametrize(
        "command",
        ["git branch -f develop", "git checkout -B develop", "git switch -C develop"],
    )
    def test_default_start_point_still_protects_checked_out_destination(
        self, linked_repo: dict[str, Path | str], command: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        assert result["attempted_value"] == "HEAD"
        assert result["resolved_attempted_new_sha"] == linked_repo["old_sha"]

    @pytest.mark.parametrize(
        "command_template",
        [
            # branch -f: target is the FIRST positional (branch name),
            # start-point is the second. Force flag can be anywhere.
            "git branch develop {new} --force",
            "git branch develop --force {new}",
            "git branch --force develop {new}",
            # checkout/switch: start-point before the marker — the marker
            # MUST be followed by the new branch name, but the start-point
            # can appear anywhere else.
            "git checkout {new} -B develop",
            "git switch {new} -C develop",
        ],
    )
    def test_force_marker_anywhere_still_protects_checked_out_destination(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        new_sha = str(linked_repo["new_sha"])
        out = _run_guard(command_template.format(new=new_sha), kitchen_open=True, tmpdir=linked)
        assert _is_denied(out), f"Expected deny for: {command_template.format(new=new_sha)}"
        result = _checked_out_ref_result(out)
        # For branch -f: target is the FIRST positional (the branch name),
        # attempted is the start-point. For checkout/switch -B/-C: target
        # is the token immediately after the marker, attempted is the
        # start-point.
        assert result["attempted_value"] == new_sha
        threatened = result["threatened_refs"]
        assert isinstance(threatened, list)
        assert any(row["target_ref"] == "refs/heads/develop" for row in threatened)
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))

    @pytest.mark.parametrize(
        "command",
        [
            "git update-ref -d refs/heads/develop",
            "git push . :develop",
            # --create-reflog is a BOOLEAN flag; bundling it with the
            # value-taking -m previously caused the parser to consume the
            # next token as --create-reflog's value, silently dropping any
            # adjacent -d or <newvalue>. See _classify_update_ref.
            "git update-ref --create-reflog -d refs/heads/develop",
            "git update-ref -d --create-reflog refs/heads/develop",
        ],
    )
    def test_deletion_forms_protect_checked_out_destinations(
        self, linked_repo: dict[str, Path | str], command: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        assert result["attempted_value"] == "<delete>"
        assert result["resolved_attempted_new_sha"] == ""
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))

    @pytest.mark.parametrize(
        "command_template",
        [
            "bash -c 'git update-ref refs/heads/develop {new}'",
            (
                'python3 -c "import subprocess; '
                "subprocess.run(['git','update-ref','refs/heads/develop','{new}'])\""
            ),
        ],
    )
    def test_denies_literal_nested_mutations(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            command_template.format(new=linked_repo["new_sha"]),
            kitchen_open=True,
            tmpdir=linked,
        )
        assert _is_denied(out)
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))

    @pytest.mark.parametrize(
        "command_template",
        [
            "git fetch origin +refs/remotes/origin/develop:refs/heads/develop",
            "git fetch origin +refs/remotes/origin/*:refs/heads/*",
            "git fetch --refmap=+refs/remotes/origin/*:refs/heads/* origin develop",
            "git fetch origin",
            "git push . HEAD:develop",
        ],
    )
    def test_denies_fetch_and_local_push_destinations(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        _git(
            linked,
            "config",
            "remote.origin.fetch",
            "+refs/remotes/origin/*:refs/heads/*",
        )
        out = _run_guard(command_template, kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        threatened = result["threatened_refs"]
        assert isinstance(threatened, list)
        assert any(row["target_ref"] == "refs/heads/develop" for row in threatened)
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))

    def test_denies_fetch_with_unrecognized_value_flag_space_separated(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        """--negotiation-tip <rev> in space-separated form (NOT --flag=value,
        which is a single self-contained shlex token) must consume its value.
        A value misread as the remote name sends the remote.<name>.fetch lookup
        to the wrong remote, leaves the mappings empty, and lets the fetch pass
        through unclassified. See _GIT_FETCH_FLAG_SPEC in _git_option_tables.
        """
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        old_sha = str(linked_repo["old_sha"])
        out = _run_guard(
            f"git fetch origin --negotiation-tip {old_sha} "
            "+refs/remotes/origin/develop:refs/heads/develop",
            kitchen_open=True,
            tmpdir=linked,
        )
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        threatened = result["threatened_refs"]
        assert isinstance(threatened, list)
        assert any(row["target_ref"] == "refs/heads/develop" for row in threatened)
        _assert_ref_unchanged(linked, "refs/heads/develop", old_sha)

    @pytest.mark.parametrize(
        ("relative_target", "command_builder"),
        [
            (
                "refs/heads/develop",
                lambda path: f"printf x > {shlex.quote(str(path))}",
            ),
            ("HEAD", lambda path: f"printf x | tee {shlex.quote(str(path))}"),
            ("packed-refs", lambda path: f"truncate -s 0 {shlex.quote(str(path))}"),
            (
                "refs/heads/develop",
                lambda path: f"python3 -c \"open({str(path)!r}, 'w').write('x')\"",
            ),
        ],
    )
    def test_denies_static_raw_ref_writes(
        self,
        linked_repo: dict[str, Path | str],
        relative_target: str,
        command_builder,
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        ref_path = Path(str(linked_repo["common_git_dir"])) / relative_target
        before = ref_path.read_bytes() if ref_path.exists() else None
        out = _run_guard(command_builder(ref_path), kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        after = ref_path.read_bytes() if ref_path.exists() else None
        assert after == before

    @pytest.mark.parametrize(
        ("source_name", "destination_relative"),
        [("HEAD", "."), ("develop", "refs/heads")],
        ids=["head", "checked-out-branch"],
    )
    def test_denies_ln_directory_entry_for_checked_out_ref(
        self,
        linked_repo: dict[str, Path | str],
        tmp_path: Path,
        source_name: str,
        destination_relative: str,
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        source = tmp_path / "sources" / source_name
        source.parent.mkdir()
        source.write_text("replacement", encoding="utf-8")
        destination = Path(str(linked_repo["common_git_dir"])) / destination_relative

        out = _run_guard(
            f"ln -sf {shlex.quote(str(source))} {shlex.quote(str(destination))}",
            kitchen_open=True,
            tmpdir=linked,
        )

        threatened = _checked_out_ref_result(out)["threatened_refs"]
        assert isinstance(threatened, list)
        expected_ref = "HEAD" if source_name == "HEAD" else f"refs/heads/{source_name}"
        assert {row["target_ref"] for row in threatened} == {expected_ref}

    def test_denies_npm_prefix_targeting_checked_out_ref(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        target = Path(str(linked_repo["common_git_dir"])) / "refs/heads/develop"

        out = _run_guard(
            f"npm install --prefix {shlex.quote(str(target))} package",
            kitchen_open=True,
            tmpdir=linked,
        )

        threatened = _checked_out_ref_result(out)["threatened_refs"]
        assert isinstance(threatened, list)
        assert {row["target_ref"] for row in threatened} == {"refs/heads/develop"}

    @pytest.mark.parametrize("target_first", [True, False], ids=["target-first", "target-last"])
    def test_denies_install_target_directory_checked_out_ref_child(
        self,
        linked_repo: dict[str, Path | str],
        tmp_path: Path,
        target_first: bool,
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        destination = Path(str(linked_repo["common_git_dir"])) / "refs/heads"
        source = tmp_path / "sources/develop"
        source.parent.mkdir()
        source.write_text("replacement", encoding="utf-8")
        operands = (
            f"-t {shlex.quote(str(destination))} {shlex.quote(str(source))}"
            if target_first
            else f"{shlex.quote(str(source))} -t {shlex.quote(str(destination))}"
        )

        out = _run_guard(f"install {operands}", kitchen_open=True, tmpdir=linked)

        threatened = _checked_out_ref_result(out)["threatened_refs"]
        assert isinstance(threatened, list)
        assert {row["target_ref"] for row in threatened} == {"refs/heads/develop"}

    @pytest.mark.parametrize("target_first", [True, False], ids=["target-first", "target-last"])
    def test_allows_install_from_protected_source_to_unprotected_directory(
        self,
        linked_repo: dict[str, Path | str],
        tmp_path: Path,
        target_first: bool,
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        source = Path(str(linked_repo["common_git_dir"])) / "refs/heads/develop"
        destination = tmp_path / "allowed"
        destination.mkdir()
        operands = (
            f"-t {shlex.quote(str(destination))} {shlex.quote(str(source))}"
            if target_first
            else f"{shlex.quote(str(source))} -t {shlex.quote(str(destination))}"
        )

        out = _run_guard(f"install {operands}", kitchen_open=True, tmpdir=linked)

        assert out.strip() == ""


class TestCheckedOutRefAmbiguity:
    @pytest.mark.parametrize(
        "command",
        [
            "git update-ref --stdin",
            "git branch -f develop $TARGET",
            "git fetch --stdin origin",
            "git push . HEAD:$TARGET",
            "git push nonexistent-remote-name HEAD:develop",
            'printf x | tee "$TARGET/refs/heads/x"',
        ],
    )
    def test_recognized_ambiguous_mutations_fail_closed_for_all_owners(
        self, linked_repo: dict[str, Path | str], command: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        refs = result["threatened_refs"]
        assert isinstance(refs, list)
        assert {row["target_ref"] for row in refs} >= {
            "refs/heads/develop",
            "refs/heads/review",
        }
        assert all(row["old_sha"] == linked_repo["old_sha"] for row in refs)

    def test_literal_asterisk_in_value_is_recognized_as_dynamic(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        # Regression test: the shared _DYNAMIC_SHELL_TOKEN_RE (imported from
        # _github_mutation_analysis.py) must include `*`. The guard previously
        # defined its own local _DYNAMIC_TOKEN_RE for this preflight that
        # omitted `*`, so a start-point value containing a literal `*` (a
        # shell-glob-expandable character) was NOT recognized as dynamic and
        # could slip through as if it were a resolvable literal ref. It must
        # instead be treated as unresolved/ambiguous, matching the $TARGET
        # case above.
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard("git branch -f develop br*nch", kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        refs = result["threatened_refs"]
        assert isinstance(refs, list)
        assert {row["target_ref"] for row in refs} >= {
            "refs/heads/develop",
            "refs/heads/review",
        }
        assert all(row["old_sha"] == linked_repo["old_sha"] for row in refs)

    def test_unresolved_global_flag_before_subcommand_fails_closed_for_all_owners(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        # extract_git_subcommand_and_flags() must return ("<unresolved>", [])
        # -- not silently misparse -- when a global flag outside the guard's
        # recognized _GIT_GLOBAL_FLAG_SPEC appears in space-separated form
        # before the subcommand. `--frobnicate` is no Git option, so the guard
        # cannot know whether it takes a value. Skipping an unrecognized
        # `-`-prefixed token by exactly one would misread the flag's *value*
        # token ("foo") as the subcommand itself: `git --frobnicate foo branch
        # -f develop start` would classify subcommand="foo", which matches none
        # of _classify_git_invocation's dispatch branches, so the real
        # `branch -f` mutation would never be classified and would slip
        # through undetected. It must instead be treated as unresolved/ambiguous,
        # matching the $TARGET and asterisk cases above.
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            "git --frobnicate foo branch -f develop start",
            kitchen_open=True,
            tmpdir=linked,
        )
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        refs = result["threatened_refs"]
        assert isinstance(refs, list)
        assert {row["target_ref"] for row in refs} >= {
            "refs/heads/develop",
            "refs/heads/review",
        }
        assert all(row["old_sha"] == linked_repo["old_sha"] for row in refs)

    def test_valid_mutation_without_execution_cwd_fails_closed(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            f"git update-ref refs/heads/develop {linked_repo['new_sha']}",
            kitchen_open=True,
            tmpdir=linked,
            include_execution_cwd=False,
        )
        assert _is_denied(out)

    @pytest.mark.parametrize(
        "command",
        [
            'npm install --prefix "$DESTINATION" package',
            'ln -sf /tmp/source "$DESTINATION"',
        ],
        ids=["npm-prefix", "ln-entry"],
    )
    def test_unresolved_explicit_write_destination_naming_no_ref_storage_is_allowed(
        self,
        linked_repo: dict[str, Path | str],
        monkeypatch: pytest.MonkeyPatch,
        command: str,
    ) -> None:
        monkeypatch.delenv("DESTINATION", raising=False)
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)

        out = _run_guard(command, kitchen_open=True, tmpdir=linked)

        assert out.strip() == ""

    @pytest.mark.parametrize(
        "command",
        [
            'npm install --prefix "$DESTINATION/refs/heads" package',
            'ln -sf /tmp/source "$DESTINATION/.git/HEAD"',
        ],
        ids=["npm-prefix", "ln-entry"],
    )
    def test_unresolved_explicit_write_destination_naming_ref_storage_fails_closed(
        self,
        linked_repo: dict[str, Path | str],
        monkeypatch: pytest.MonkeyPatch,
        command: str,
    ) -> None:
        monkeypatch.delenv("DESTINATION", raising=False)
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)

        out = _run_guard(command, kitchen_open=True, tmpdir=linked)

        assert _is_denied(out)
        assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"

    @pytest.mark.parametrize(
        ("command_builder", "denied"),
        [
            (
                lambda root: f"npm install --prefix {shlex.quote(str(root / 'npm-root'))} package",
                False,
            ),
            (lambda root: f"ln -sf /tmp/source {shlex.quote(str(root / 'link'))}", False),
            (
                lambda root: f"install -t {shlex.quote(str(root / 'install-root'))} /tmp/source",
                False,
            ),
            (lambda _root: "cat > f.md <<< 'git push --force origin main'", False),
            (lambda _root: "cat > refs/heads/x <<< y", True),
        ],
        ids=[
            "npm-prefix",
            "ln-entry",
            "install-target-directory",
            "relative-redirect",
            "relative-redirect-naming-ref-storage",
        ],
    )
    def test_structural_write_without_execution_cwd_is_classified(
        self,
        linked_repo: dict[str, Path | str],
        command_builder,
        denied: bool,
    ) -> None:
        """Writes to absolute non-ref paths are classified and allowed without an
        execution cwd. A relative write has no cwd to resolve against, so it is
        ambiguous, and denied, only when the command names ref storage."""
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)

        out = _run_guard(
            command_builder(linked),
            kitchen_open=True,
            tmpdir=linked,
            include_execution_cwd=False,
        )

        if denied:
            assert _is_denied(out)
            assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"
        else:
            assert out.strip() == ""


def test_link_entry_parent_loop_naming_neither_git_nor_ref_storage_is_allowed(
    linked_repo: dict[str, Path | str], tmp_path: Path
) -> None:
    linked = linked_repo["linked"]
    assert isinstance(linked, Path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    out = _run_guard(
        f"ln -s source {shlex.quote(str(loop / 'entry'))}", kitchen_open=True, tmpdir=linked
    )

    assert out.strip() == ""


def test_link_entry_parent_loop_naming_ref_storage_fails_closed(
    linked_repo: dict[str, Path | str], tmp_path: Path
) -> None:
    linked = linked_repo["linked"]
    assert isinstance(linked, Path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)

    out = _run_guard(
        f"ln -s source {shlex.quote(str(loop / 'refs/heads/entry'))}",
        kitchen_open=True,
        tmpdir=linked,
    )

    assert _is_denied(out)
    assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"


class TestCheckedOutRefWriteTargets:
    @pytest.mark.parametrize(
        "command",
        [
            'echo x > "$GIT_TARGET"',
            'echo x > "$(printf /tmp/out)"',
            "echo x > `printf /tmp/out`",
            'cd "$UNSET_DIR" && echo y > rel.txt',
            'cd "$(printf /tmp)" && echo y > rel.txt',
            "cd - && dd if=/dev/zero of=rel.bin",
        ],
    )
    def test_unresolved_write_targets_naming_no_ref_storage_are_allowed(
        self,
        linked_repo: dict[str, Path | str],
        monkeypatch: pytest.MonkeyPatch,
        command: str,
    ) -> None:
        monkeypatch.delenv("GIT_TARGET", raising=False)
        monkeypatch.delenv("UNSET_DIR", raising=False)
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "command",
        [
            'echo x > "$G/refs/heads/develop"',
            'cd "$UNSET_DIR" && echo y > refs/heads/x',
            "cd - && dd if=/dev/zero of=.git/HEAD",
        ],
    )
    def test_unresolved_write_targets_naming_ref_storage_fail_closed(
        self,
        linked_repo: dict[str, Path | str],
        monkeypatch: pytest.MonkeyPatch,
        command: str,
    ) -> None:
        monkeypatch.delenv("G", raising=False)
        monkeypatch.delenv("UNSET_DIR", raising=False)
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"

    def test_absolute_write_after_unknown_cwd_is_allowed(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            f"cd - && echo x > {linked / 'outside.txt'}", kitchen_open=True, tmpdir=linked
        )
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "command",
        [
            "dd if=/dev/zero of=rel.bin && cd -",
            "bash -c 'dd if=/dev/zero of=rel.bin' && cd -",
        ],
    )
    def test_write_before_unknown_cwd_is_allowed(
        self, linked_repo: dict[str, Path | str], command: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        "command",
        [
            "cd - && git log",
            "cd - && git status",
            "cd - && git diff",
            "cd - && git show HEAD",
        ],
    )
    def test_read_only_git_after_failed_cd_is_allowed(
        self, linked_repo: dict[str, Path | str], command: str
    ) -> None:
        """Read-only git verbs must not be denied just because a preceding
        cd failed (cwd resolves to empty). The preflight only escalates an
        unknown repository when the subcommand is a known ref-mutating one.
        """
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert out.strip() == ""

    @pytest.mark.parametrize(
        ("command", "verdict"),
        [
            ("cd - && git push origin develop", "allow"),
            ("cd - && git reset --hard", "checked-out-ref"),
            ("cd - && git checkout .", "other-deny"),
        ],
    )
    def test_git_after_failed_cd_is_denied_only_when_it_rewrites_a_ref_or_is_destructive(
        self, linked_repo: dict[str, Path | str], command: str, verdict: str
    ) -> None:
        """After a failed cd the segment cannot be routed to a repository.
        `reset --hard` outside path mode is a ref mutation, so the preflight
        denies it fail-closed with an unresolved value. `checkout .` restores
        paths and rewrites no ref; the headless destructive-operation phase
        denies it. `push origin develop` names a remote, which is not a ref
        mutation of this repository, and a push without force is not risky,
        so it is allowed.
        """
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _guard_verdict(out) == verdict, command
        if verdict == "checked-out-ref":
            assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"
        elif verdict == "other-deny":
            reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
            assert reason == git_ops_guard._DENY_REASON_TEMPLATE.format(op="git checkout .")


class TestCheckedOutRefAllows:
    @pytest.mark.parametrize("write_to_owned_ref", [False, True], ids=["pr-body", "owned-ref"])
    def test_smoke_pr_creation_command_preserves_ref_protection(
        self, linked_repo: dict[str, Path | str], write_to_owned_ref: bool
    ) -> None:
        from autoskillit.core import load_yaml

        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        recipe_path = (
            Path(__file__).resolve().parents[2] / "scripts/e2e/recipes/sandbox-smoke.yaml"
        )
        command = load_yaml(recipe_path)["steps"]["create_pull_request"]["with"]["cmd"]
        temp_dir = linked / ".autoskillit" / "temp"
        replacements = {
            "{{AUTOSKILLIT_TEMP}}": str(temp_dir),
            "${{ inputs.repository }}": "example/sandbox",
            "${{ context.branch_name }}": "review",
            "${{ inputs.base_branch }}": "develop",
        }
        for source, value in replacements.items():
            command = command.replace(source, value)
        assert "{{" not in command
        if write_to_owned_ref:
            command = command.replace(
                str(temp_dir / "sandbox-smoke-pr-body.md"),
                str(Path(str(linked_repo["common_git_dir"])) / "refs/heads/review"),
            )

        output = _run_guard(command, kitchen_open=True, tmpdir=linked, session_type="orchestrator")

        assert _is_denied(output) is write_to_owned_ref
        _assert_ref_unchanged(linked, "refs/heads/review", str(linked_repo["old_sha"]))

    @pytest.mark.parametrize(
        "command_template",
        [
            "git commit -m safe",
            "git update-ref refs/heads/unowned {new}",
            "git update-ref refs/tags/release {new}",
            "git update-ref refs/remotes/origin/develop {new}",
            "git fetch --no-write-fetch-head origin {new}",
            "git fetch origin refs/heads/develop:refs/remotes/origin/develop",
            "git reset -- tracked.txt",
            "git reset {old}",
        ],
    )
    def test_allows_non_threatening_forms(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            command_template.format(new=linked_repo["new_sha"], old=linked_repo["old_sha"]),
            kitchen_open=True,
            tmpdir=linked,
            headless=False,
        )
        assert out.strip() == ""

    def test_detached_matching_head_does_not_own_branch(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        primary = linked_repo["primary"]
        linked = linked_repo["linked"]
        assert isinstance(primary, Path) and isinstance(linked, Path)
        _git(primary, "checkout", "--detach")
        out = _run_guard(
            f"git update-ref refs/heads/develop {linked_repo['new_sha']}",
            kitchen_open=True,
            tmpdir=linked,
            headless=False,
        )
        assert out.strip() == ""

    def test_standalone_clone_allows_unowned_branch_mutation(self, tmp_path: Path) -> None:
        repo = tmp_path / "standalone"
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Guard Test")
        _git(repo, "config", "user.email", "guard@example.invalid")
        (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "initial")
        sha = _git(repo, "rev-parse", "HEAD")
        out = _run_guard(
            f"git update-ref refs/heads/unowned {sha}",
            kitchen_open=True,
            tmpdir=repo,
            headless=False,
        )
        assert out.strip() == ""

    def test_allows_push_to_configured_network_remote(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        """Regression for #4937: a push to a genuine network remote (SCP-like
        syntax here; test_allows_git_push_to_remote covers the scheme-URL
        form) must be allowed even when the refspec names a ref that IS
        checked out locally -- pushing to another repository cannot mutate
        this worktree's own checked-out ref.
        """
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        _git(linked, "remote", "add", "upstream", "git@example.invalid:org/repo.git")
        out = _run_guard("git push upstream develop", kitchen_open=True, tmpdir=linked)
        assert out.strip() == ""

    def test_denies_push_to_remote_aliasing_linked_worktree(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        """Regression for #4937's stated boundary: resolving a remote name to
        its URL must not become a blanket "any configured remote is a
        different repo" shortcut. A remote aliasing another linked worktree
        of the SAME repository (sharing the common git dir) must remain
        protected.
        """
        linked = linked_repo["linked"]
        primary = linked_repo["primary"]
        assert isinstance(linked, Path) and isinstance(primary, Path)
        _git(linked, "remote", "add", "mirror", str(primary))
        out = _run_guard("git push mirror HEAD:develop", kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        threatened = result["threatened_refs"]
        assert isinstance(threatened, list)
        assert any(row["target_ref"] == "refs/heads/develop" for row in threatened)
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))

    def test_allows_push_to_remote_aliasing_unrelated_clone(
        self, linked_repo: dict[str, Path | str], tmp_path: Path
    ) -> None:
        """Regression for #4937: a named remote pointing at an unrelated
        local clone (different repository, different common git dir) must be
        allowed. Paired with test_denies_push_to_remote_aliasing_linked_worktree
        to prove the fix distinguishes the two cases by actually comparing
        common git dirs, not by treating "candidate names a configured
        remote" as sufficient on its own.
        """
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        _git(unrelated, "init", "-b", "develop")
        _git(unrelated, "config", "user.name", "Guard Test")
        _git(unrelated, "config", "user.email", "guard@example.invalid")
        (unrelated / "tracked.txt").write_text("other\n", encoding="utf-8")
        _git(unrelated, "add", "tracked.txt")
        _git(unrelated, "commit", "-m", "initial")
        _git(linked, "remote", "add", "other", str(unrelated))
        out = _run_guard("git push other HEAD:develop", kitchen_open=True, tmpdir=linked)
        assert out.strip() == ""

    def test_denies_push_to_remote_aliasing_linked_worktree_via_file_url(
        self, linked_repo: dict[str, Path | str]
    ) -> None:
        """Regression for #4937: the file:// branch of `_local_path_from_remote_url`
        (as opposed to the SCP-like or bare-path branches covered above) must also
        resolve a same-repo alias correctly. A remote configured with an explicit
        `file://` URL aliasing another linked worktree of the SAME repository must
        remain protected -- a misclassified file:// URL would silently re-allow the
        push instead of routing through the checked-out-ref deny path.
        """
        linked = linked_repo["linked"]
        primary = linked_repo["primary"]
        assert isinstance(linked, Path) and isinstance(primary, Path)
        _git(linked, "remote", "add", "mirror-file", f"file://{primary}")
        out = _run_guard("git push mirror-file HEAD:develop", kitchen_open=True, tmpdir=linked)
        assert _is_denied(out)
        result = _checked_out_ref_result(out)
        threatened = result["threatened_refs"]
        assert isinstance(threatened, list)
        assert any(row["target_ref"] == "refs/heads/develop" for row in threatened)
        _assert_ref_unchanged(linked, "refs/heads/develop", str(linked_repo["old_sha"]))


class TestCheckedOutRefPreflightOrdering:
    @pytest.mark.parametrize(
        ("headless", "session_type", "hook_config"),
        [
            (False, None, {"kitchen": "open"}),
            (True, "orchestrator", {"kitchen": "open"}),
            (True, None, {"kitchen": "open", "git_ops_policy": {"allow_update-ref": True}}),
        ],
    )
    def test_checked_out_ref_preflight_precedes_scope_exemptions_and_policy(
        self,
        linked_repo: dict[str, Path | str],
        headless: bool,
        session_type: str | None,
        hook_config: dict[str, object],
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        out = _run_guard(
            f"git update-ref refs/heads/develop {linked_repo['new_sha']}",
            kitchen_open=True,
            tmpdir=linked,
            headless=headless,
            session_type=session_type,
            hook_config=hook_config,
        )
        assert _is_denied(out)
        _checked_out_ref_result(out)


_SESSION_SHAPES = (
    (True, "skill"),
    (True, "orchestrator"),
    (False, "orchestrator"),
    (False, "skill"),
)


class TestCheckedOutRefOutcomeImmunity:
    """A ref rewrite is judged by the checked-out ref it rewrites, not by how its
    options are spelled; HEAD effects with no ref rewrite and list forms stay allowed.
    """

    @pytest.fixture(autouse=True)
    def _unowned_branch(self, linked_repo: dict[str, Path | str]) -> None:
        _git(Path(str(linked_repo["primary"])), "branch", "spare", str(linked_repo["old_sha"]))

    @pytest.mark.parametrize(
        "command_template",
        [
            "git checkout -Bdevelop {sha}",
            "git checkout -fB develop {sha}",
            "git checkout --ignore-other-worktrees -Bdevelop",
            "git checkout feat -B develop",
            "git switch -Cdevelop",
            "git switch --force-create=develop",
            "git switch --force-c develop",
            "git branch -m develop renamed",
            "git branch -M develop renamed",
            "git update-ref -dm why refs/heads/develop",
            "git push -o x {primary} HEAD:develop",
        ],
    )
    def test_denies_rewrite_of_ref_owned_by_primary(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        command = command_template.format(
            sha=linked_repo["new_sha"], primary=shlex.quote(str(linked_repo["primary"]))
        )
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _guard_verdict(out) == "checked-out-ref", command
        threatened = _checked_out_ref_result(out)["threatened_refs"]
        assert isinstance(threatened, list)
        assert [row["target_ref"] for row in threatened] == ["refs/heads/develop"], command

    @pytest.mark.parametrize(
        "command_template",
        [
            "git branch -m spare spare2",
            "git checkout -b fresh {sha}",
            "git checkout spare",
            "git switch spare",
            "git checkout --detach",
            "git branch -a",
            "git branch --merged",
            "git symbolic-ref HEAD",
        ],
    )
    def test_allows_unowned_rewrites_head_effects_and_list_forms(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        command = command_template.format(sha=linked_repo["new_sha"])
        out = _run_guard(command, kitchen_open=True, tmpdir=linked)
        assert _guard_verdict(out) == "allow", command

    @pytest.mark.parametrize(
        ("command", "verdict"),
        [
            ("git checkout -Bdevelop", "checked-out-ref"),
            ("git branch -m develop renamed", "checked-out-ref"),
            ("git update-ref -dm why refs/heads/develop", "checked-out-ref"),
            ("git branch -a", "allow"),
            ("git checkout -b fresh", "allow"),
            ("git checkout spare", "allow"),
        ],
    )
    def test_verdict_is_the_same_in_every_session_shape(
        self, linked_repo: dict[str, Path | str], command: str, verdict: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        verdicts = {
            shape: _guard_verdict(
                _run_guard(
                    command,
                    kitchen_open=True,
                    tmpdir=linked,
                    headless=shape[0],
                    session_type=shape[1],
                )
            )
            for shape in _SESSION_SHAPES
        }
        assert verdicts == dict.fromkeys(_SESSION_SHAPES, verdict)


@functools.cache
def _git_version() -> str:
    return subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()


def _context(command: str, observed: object = "") -> str:
    return f"{command!r} {observed!r} ({_git_version()})"


def _deny_reason(output: str) -> str:
    return json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]


def _destructive_reason(op: tuple[str, ...]) -> str:
    return git_ops_guard._DENY_REASON_TEMPLATE.format(op=" ".join(("git", *op)))


_AMEND = ("commit", "--amend")
_FORCE_PUSH = ("push", "--force")
_LEASE_PUSH = ("push", "--force-with-lease")
_HARD_RESET = ("reset", "--hard")
_FORCE_CLEAN = ("clean", "-f")
_CHECKOUT_TREE = ("checkout", ".")
_CHECKOUT_DASHDASH_TREE = ("checkout", "--", ".")
_UNRESOLVED_OP = ("<unresolved>",)


def _git_rows(op: tuple[str, ...], *tails: str) -> list[tuple[str, tuple[str, ...]]]:
    return [(f"git {tail}", op) for tail in tails]


_DESTRUCTIVE_ROWS = [
    *_git_rows(
        _FORCE_PUSH,
        "push -fu origin main",
        "push -qf origin main",
        "push origin +main",
        "push origin +HEAD:main",
    ),
    *_git_rows(
        _LEASE_PUSH, "push --force-w origin main", "push --force-with-lease=main origin main"
    ),
    *_git_rows(_HARD_RESET, "reset --har HEAD~", "reset --h HEAD~"),
    *_git_rows(
        _FORCE_CLEAN,
        "clean --forc",
        "clean -df",
        "clean -xdf",
        "clean -fx",
        "clean -ff",
        "clean -d",
        "-c clean.requireForce=0 clean -d",
    ),
    *_git_rows(_AMEND, "commit --amen", "commit --am --no-edit"),
    ("git-commit --amend", _AMEND),
    *_git_rows(
        _CHECKOUT_TREE, "checkout -f", "checkout ./", "checkout :/", "checkout . origin main"
    ),
    *_git_rows(_CHECKOUT_DASHDASH_TREE, "checkout -- ./"),
    *_git_rows(_UNRESOLVED_OP, "push --forc origin main", "reset --hard-foo"),
]

_DESTRUCTIVE_ALLOWED = [
    "git commit -m '--amend'",
    "git commit -m x --no-amend",
    "git push origin main",
    "git push -u origin main",
    "git push --force-if-includes origin main",
    "git push --recurse-submodules=check origin main",
    "git reset --soft HEAD~",
    "git clean -n",
    "git clean -nd",
    "git clean --dry-run -d",
    "git clean -i",
    "git checkout -- a.txt",
    "git checkout feature",
    "git -c user.name=x commit -m x",
]

_DENIED_SHAPE = (True, "skill")
_EXEMPT_SHAPES = tuple(shape for shape in _SESSION_SHAPES if shape != _DENIED_SHAPE)
_SHAPE_PARITY_COMMANDS = (
    "git push origin +main",
    "git reset --har HEAD~",
    "git clean -d",
    "git commit --am --no-edit",
    "git checkout ./",
    "git push --forc origin main",
)

_REPOSITORY_ALIAS_ROWS = [
    ("git ci --amend", _AMEND),
    ("git pf origin main", _FORCE_PUSH),
]
_UNKNOWN_IDENTITY_COMMANDS = [
    "git sh",
    "P=commit; git $P --amend",
    "git -c alias.x='commit --amend' x",
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
class TestDestructiveOutcomeImmunity:
    """The headless destructive-operation phase matches the operations
    RISKY_GIT_OPERATIONS names by what they do, not by how their options are
    spelled. A plain directory is no repository, so the checked-out-ref preflight
    has nothing to decide and the phase alone does.
    """

    @pytest.fixture(autouse=True)
    def _isolated_git_environment(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    @pytest.fixture
    def aliased_repo(self, tmp_path: Path) -> Path:
        """A checkout on ``main`` with a separate bare ``origin`` that is fetched,
        and repository aliases for commit, a forced push and a shell command."""
        repo = init_checkout(tmp_path / "repo")
        _git(repo, "branch", "-m", "main")
        commit_file(repo, "tracked.txt", "initial\n", "initial")
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", str(origin))
        _git(repo, "remote", "add", "origin", str(origin))
        _git(repo, "push", "-q", "origin", "main")
        _git(repo, "fetch", "-q", "origin")
        for alias, expansion in (
            ("ci", "commit"),
            ("pf", "push --force"),
            ("sh", "!git commit --amend"),
        ):
            _git(repo, "config", f"alias.{alias}", expansion)
        return repo

    def test_the_plain_directory_is_not_inside_a_repository(self, tmp_path: Path) -> None:
        result = subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0, _context("rev-parse in the plain directory", result.stdout)

    @pytest.mark.parametrize(("command", "op"), _DESTRUCTIVE_ROWS)
    def test_denies_each_spelling_with_the_destructive_reason(
        self, tmp_path: Path, command: str, op: tuple[str, ...]
    ) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path)

        assert _guard_verdict(out) == "other-deny", _context(command, out)
        assert _deny_reason(out) == _destructive_reason(op), _context(command, out)

    @pytest.mark.parametrize("command", _DESTRUCTIVE_ALLOWED)
    def test_allows_each_spelling_that_destroys_nothing(
        self, tmp_path: Path, command: str
    ) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path)

        assert _guard_verdict(out) == "allow", _context(command, out)

    @pytest.mark.parametrize(
        "command", [command for command, _ in _DESTRUCTIVE_ROWS] + _DESTRUCTIVE_ALLOWED
    )
    def test_allows_every_row_in_the_orchestrator_tier(self, tmp_path: Path, command: str) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path, session_type="orchestrator")

        assert _guard_verdict(out) == "allow", _context(command, out)

    @pytest.mark.parametrize(("headless", "session_type"), _EXEMPT_SHAPES)
    @pytest.mark.parametrize("command", _SHAPE_PARITY_COMMANDS)
    def test_allows_the_destructive_rows_outside_a_headless_skill_session(
        self, tmp_path: Path, command: str, headless: bool, session_type: str
    ) -> None:
        out = _run_guard(
            command,
            kitchen_open=True,
            tmpdir=tmp_path,
            headless=headless,
            session_type=session_type,
        )

        assert _guard_verdict(out) == "allow", _context(command, (headless, session_type, out))

    @pytest.mark.parametrize(("command", "op"), _REPOSITORY_ALIAS_ROWS)
    def test_denies_a_repository_alias_for_a_destructive_operation(
        self, aliased_repo: Path, command: str, op: tuple[str, ...]
    ) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=aliased_repo)

        assert _guard_verdict(out) == "other-deny", _context(command, out)
        assert _deny_reason(out) == _destructive_reason(op), _context(command, out)

    @pytest.mark.parametrize(("headless", "session_type"), _EXEMPT_SHAPES)
    @pytest.mark.parametrize("command", [command for command, _ in _REPOSITORY_ALIAS_ROWS])
    def test_allows_a_repository_alias_outside_a_headless_skill_session(
        self, aliased_repo: Path, command: str, headless: bool, session_type: str
    ) -> None:
        out = _run_guard(
            command,
            kitchen_open=True,
            tmpdir=aliased_repo,
            headless=headless,
            session_type=session_type,
        )

        assert _guard_verdict(out) == "allow", _context(command, (headless, session_type, out))

    @pytest.mark.parametrize(("headless", "session_type"), _SESSION_SHAPES)
    @pytest.mark.parametrize("command", _UNKNOWN_IDENTITY_COMMANDS)
    def test_denies_a_command_of_unknown_identity_in_every_session_shape(
        self, aliased_repo: Path, command: str, headless: bool, session_type: str
    ) -> None:
        out = _run_guard(
            command,
            kitchen_open=True,
            tmpdir=aliased_repo,
            headless=headless,
            session_type=session_type,
        )

        assert _guard_verdict(out) == "checked-out-ref", _context(
            command, (headless, session_type, out)
        )
        assert _checked_out_ref_result(out)["attempted_value"] == "<unresolved>"

    @pytest.mark.parametrize("command", ["git lfs version", "cd $X && git lfs pull"])
    def test_allows_an_external_subcommand(self, aliased_repo: Path, command: str) -> None:
        out = _run_guard(command, kitchen_open=True, tmpdir=aliased_repo)

        assert _guard_verdict(out) == "allow", _context(command, out)


class TestGuardFailsClosed:
    """An unexpected classification error blocks (exit 2, reason on stderr)."""

    def test_error_in_ref_classification_blocks(
        self,
        linked_repo: dict[str, Path | str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise IndexError("injected classification failure")

        monkeypatch.setattr(git_ops_guard, "_classify_git_invocation", _raise)

        code = _guard_exit_code(f"git update-ref refs/heads/x {linked_repo['new_sha']}", linked)

        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == ""
        assert "git_ops_guard: IndexError: injected classification failure" in captured.err

    def test_error_in_blocked_operation_match_blocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _raise(*_args: object, **_kwargs: object) -> None:
            raise KeyError("injected matcher failure")

        monkeypatch.setattr(git_ops_guard, "contains_blocked_git_op", _raise)

        code = _guard_exit_code("git status", tmp_path)

        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == ""
        assert "git_ops_guard: KeyError: 'injected matcher failure'" in captured.err


# ---------------------------------------------------------------------------
# Spec table coverage: every row of _GIT_FETCH_FLAG_SPEC must be recognized by
# _classify_fetch in each spelling it admits. Parametrized directly from the
# table's own keys so a row added to the table extends coverage
# automatically. Registered with test_guard_flag_spec_coverage.py via
# _SPEC_TABLE_TEST_FILES so the standing-invariant AST scanner confirms the
# parametrize genuinely iterates the table rather than a hand-copied list.
# ---------------------------------------------------------------------------


def _fetch_flag_spellings(row: OptionDef) -> list[list[str]]:
    """Return the argv fragments that spell *row*: long, short and negated.

    A REQUIRED value is stuck to the long spelling (``--name=VALUE``) and
    follows the short one as a separate token.
    """
    value = ["someval"] if row.arity == OptionArity.REQUIRED else []
    spellings: list[list[str]] = []
    if row.has_long:
        spellings.append([f"--{row.name}=someval"] if value else [f"--{row.name}"])
    if row.short:
        spellings.append([f"-{row.short}", *value])
    if row.has_long and row.negatable:
        spellings.append([f"--no-{row.name}"])
    return spellings


@pytest.mark.parametrize("name", sorted(_GIT_FETCH_FLAG_SPEC))
def test_every_git_fetch_spec_flag_is_recognized(name: str) -> None:
    """Generative coverage: every row of _GIT_FETCH_FLAG_SPEC, in each spelling
    it admits, must be recognized by _classify_fetch rather than misread as the
    remote/refspec operand. An unrecognized flag's value would have been
    misclassified as the fetch's remote URL and silently passed the
    destructive-op preflight; that must not happen for any table row.

    ``--stdin`` makes the destinations ambiguous by design (the refspecs arrive
    on stdin), so we assert that specific result shape for it rather than the
    generic non-unresolved invariant; its negation is an ordinary flag.
    """
    spellings = _fetch_flag_spellings(_GIT_FETCH_FLAG_SPEC[name])
    assert spellings, f"{name} has no spelling"

    for spelling in spellings:
        result = _classify_fetch([*spelling, "origin"], cwd="/tmp", owned_refs=[])

        if spelling == ["--stdin"]:
            assert result == [("", "<unresolved>", True)]
        else:
            # An `<unresolved>` entry means the flag was NOT recognized -- the
            # precise failure mode the option table is supposed to prevent.
            assert not any(entry[1] == "<unresolved>" for entry in result), spelling


def test_git_ops_guard_classify_imports_from_new_sibling() -> None:
    """Every classification primitive the orchestrator consumes from
    autoskillit.hooks._runtime._git_command_classification, and the blocked
    operation matcher it consumes from autoskillit.hooks._runtime._git_target,
    must remain reachable from the package import surface, as must the
    remote-identity helper `_same_repository` resolves through. Mirrors the
    orchestrator's import block in git_ops_guard.py. The fetch option table the
    classifier reads lives in _git_option_tables as OptionDef rows.
    """
    from autoskillit.hooks._runtime._git_command_classification import (
        _classify_git_invocation,
        _git_result,
        _git_text,
        _parse_worktree_owners,
        _resolve_attempted_sha,
        _resolve_git_common_dir,
    )
    from autoskillit.hooks._runtime._git_target import contains_blocked_git_op

    expected_callables = (
        _classify_git_invocation,
        contains_blocked_git_op,
        _git_result,
        _git_text,
        _parse_worktree_owners,
        _resolve_attempted_sha,
        _resolve_git_common_dir,
    )
    for symbol in expected_callables:
        assert callable(symbol), f"{symbol.__name__} must be callable"

    assert isinstance(_GIT_FETCH_FLAG_SPEC, Mapping)
    assert all(isinstance(row, OptionDef) for row in _GIT_FETCH_FLAG_SPEC.values())
    assert all(name == row.name for name, row in _GIT_FETCH_FLAG_SPEC.items())


def test_git_ops_guard_imports_with_hooks_directory_alone() -> None:
    """Step 9 (#4733): the orchestrator's extended bootstrap must let
    `_git_command_classification` resolve when only `hooks/` is on sys.path.
    Mirrors tests/arch/test_shell_capture_trust_anchor.py:59
    (test_capture_modules_import_with_hooks_directory_alone).
    """
    payload = json.dumps(
        {
            "session_id": "bootstrap-smoke",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
    )
    from tests.conftest import production_interpreter_env

    env = production_interpreter_env()
    env["PYTHONPATH"] = "src/autoskillit/hooks"
    env["PATH"] = os.environ["PATH"]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "src/autoskillit/hooks/guards/git_ops_guard.py",
        ],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert result.returncode == 0, (
        f"git_ops_guard.py subprocess failed (exit={result.returncode}): stderr={result.stderr!r}"
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert "ImportError" not in result.stderr


_INVOCATION_GRAMMAR_DENIED = [
    ("bash -lc 'git push --force origin main'", _FORCE_PUSH),
    ("sh -ec 'git commit --amend'", _AMEND),
    ("timeout -k 5 10 git push -f origin main", _FORCE_PUSH),
    ("sudo -n git commit --amend", _AMEND),
    ("env --ignore-signal git push -f origin main", _FORCE_PUSH),
    ("python3 -Bc \"import subprocess; subprocess.run(['git','reset','--hard'])\"", _HARD_RESET),
    ("echo x | xargs git push -f origin main", _FORCE_PUSH),
    ("xargs sh -c 'true&&git push -f origin main'", _FORCE_PUSH),
    ("env -S 'true;git commit --amend'", _AMEND),
    ("setsid git commit --amend", _AMEND),
    ("node -e \"require('child_process').execSync('git push -f origin main')\"", _FORCE_PUSH),
]

_INVOCATION_GRAMMAR_REF_REWRITES = [
    "bash -lc 'git update-ref refs/heads/develop {sha}'",
    "timeout -k 5 10 git checkout -Bdevelop {sha}",
    "sudo -D {primary} git update-ref refs/heads/develop {sha}",
]

_INVOCATION_GRAMMAR_HEAD_MOVES = [
    "bash -lc 'git checkout -b x {sha}'",
    "sudo -n git checkout other",
]

_INVOCATION_GRAMMAR_ALLOWED = [
    "bash -lc 'git status'",
    "timeout 600 uv run pytest -q",
    "uv run pytest -k git",
    "uv run pytest -q && git push origin main",
    "echo x | xargs grep git",
    "find . -exec git log {} \\;",
    "nice -n 5 make build",
    "command -v git",
    "bash script.sh",
]

_QUOTED_PUSH_TEXT_IN_LAUNCHER = 'find . -exec grep -l "git push" {} \\;'


def _denies_by_ref_or_destructive_phase(output: str, op: tuple[str, ...]) -> bool:
    """Whether *output* denies with the checked-out-ref reason or with the
    destructive-phase reason for *op* or for an unresolved operation."""
    if not _is_denied(output):
        return False
    reason = _deny_reason(output)
    return reason.startswith(git_ops_guard.CHECKED_OUT_REF_DENY_PREFIX) or reason in {
        _destructive_reason(op),
        _destructive_reason(_UNRESOLVED_OP),
    }


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
@pytest.mark.usefixtures("isolated_git_config")
class TestInvocationGrammarImmunity:
    """A command is judged as the Git invocation it carries, however a wrapper's
    options, a shell or interpreter command string, or an opaque launcher
    deliver it. A launcher whose payload cannot be located denies only when its
    text mentions a Git mutation; every other form keeps the verdict of the
    command it runs in all four session shapes.
    """

    @pytest.fixture
    def cook_repo(self, tmp_path: Path) -> dict[str, Path | str]:
        built = build_linked_repo(tmp_path, tracked_state_dir=True)
        _git(Path(str(built["primary"])), "branch", "other")
        return built

    @pytest.fixture
    def pushable_repo(self, linked_repo: dict[str, Path | str]) -> Path:
        """The unrelated repository, with the bare repository as ``origin``."""
        unrelated = Path(str(linked_repo["unrelated"]))
        _git(unrelated, "remote", "add", "origin", str(linked_repo["bare"]))
        return unrelated

    @pytest.mark.parametrize(("command", "op"), _INVOCATION_GRAMMAR_DENIED)
    def test_denies_a_git_mutation_behind_an_invocation_grammar(
        self, tmp_path: Path, command: str, op: tuple[str, ...]
    ) -> None:
        """A plain directory is no repository, so the preflight denies only a
        segment whose position is unresolved and that mentions a Git mutation;
        otherwise the destructive phase denies, naming the row's operation or an
        unresolved one. Either denial is a pass.
        """
        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path)

        assert _denies_by_ref_or_destructive_phase(out, op), _context(command, out)

    def test_denies_git_mutation_text_quoted_in_an_opaque_launcher_argument(
        self, tmp_path: Path
    ) -> None:
        """Text naming a Git mutation inside ``find -exec`` arguments cannot be told
        apart from a payload such as ``xargs sh -c '...git push...'``, so quoted
        data denies too.
        """
        out = _run_guard(_QUOTED_PUSH_TEXT_IN_LAUNCHER, kitchen_open=True, tmpdir=tmp_path)

        assert _denies_by_ref_or_destructive_phase(out, _UNRESOLVED_OP), _context(
            _QUOTED_PUSH_TEXT_IN_LAUNCHER, out
        )

    def test_allows_the_inner_command_the_launcher_wraps(self, tmp_path: Path) -> None:
        command = 'grep -rl "git push" .'

        out = _run_guard(command, kitchen_open=True, tmpdir=tmp_path)

        assert _guard_verdict(out) == "allow", _context(command, out)

    @pytest.mark.parametrize("command_template", _INVOCATION_GRAMMAR_REF_REWRITES)
    def test_denies_a_checked_out_ref_rewrite_behind_an_invocation_grammar(
        self, linked_repo: dict[str, Path | str], command_template: str
    ) -> None:
        linked = linked_repo["linked"]
        assert isinstance(linked, Path)
        command = command_template.format(
            sha=linked_repo["new_sha"], primary=shlex.quote(str(linked_repo["primary"]))
        )

        out = _run_guard(command, kitchen_open=True, tmpdir=linked)

        assert _guard_verdict(out) == "checked-out-ref", _context(command, out)
        threatened = _checked_out_ref_result(out)["threatened_refs"]
        assert isinstance(threatened, list)
        assert [row["target_ref"] for row in threatened] == ["refs/heads/develop"], _context(
            command, out
        )

    @pytest.mark.parametrize("command_template", _INVOCATION_GRAMMAR_HEAD_MOVES)
    def test_denies_a_cook_head_move_behind_an_invocation_grammar(
        self, cook_repo: dict[str, Path | str], tmp_path: Path, command_template: str
    ) -> None:
        primary = Path(str(cook_repo["primary"]))
        command = command_template.format(sha=cook_repo["new_sha"])

        out = _run_guard(
            command,
            kitchen_open=True,
            tmpdir=tmp_path,
            marker_root=primary,
            headless=False,
            extra_env={
                "AUTOSKILLIT_SESSION_TYPE": "skill",
                "AUTOSKILLIT_LAUNCH_ID": "0123456789abcdef",
                "AUTOSKILLIT_STATE_ROOT": str(primary),
            },
            use_bash_key=True,
            payload_cwd=primary,
        )

        assert _is_denied(out), _context(command, out)
        reason = _deny_reason(out)
        prefix = git_ops_guard.PRIMARY_HEAD_DENY_PREFIX
        assert reason.startswith(prefix), _context(command, out)
        details = json.loads(reason.removeprefix(prefix))
        assert details["subcommand"] == "checkout", _context(command, details)
        assert details["protected_primary"] == str(primary), _context(command, details)

    @pytest.mark.parametrize("command", _INVOCATION_GRAMMAR_ALLOWED)
    @pytest.mark.parametrize("directory", ["plain", "repository"])
    def test_allows_each_form_in_every_session_shape(
        self, tmp_path: Path, pushable_repo: Path, directory: str, command: str
    ) -> None:
        verdicts = {
            shape: _guard_verdict(
                _run_guard(
                    command,
                    kitchen_open=True,
                    tmpdir=tmp_path if directory == "plain" else pushable_repo,
                    headless=shape[0],
                    session_type=shape[1],
                )
            )
            for shape in _SESSION_SHAPES
        }

        assert verdicts == dict.fromkeys(_SESSION_SHAPES, "allow"), _context(command, verdicts)
