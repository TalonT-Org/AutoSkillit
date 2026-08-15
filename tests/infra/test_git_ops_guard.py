"""Tests for the git_ops_guard PreToolUse hook.

Guards against destructive git operations (commit --amend, push --force,
reset --hard, clean -f, checkout .) in headless skill sessions.

Pattern mirrors test_pr_create_guard.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_TOOL_NAME = "mcp__autoskillit__local__autoskillit__run_cmd"
_BASH_TOOL_NAME = "Bash"
_HOOK_CONFIG_RELPATH = ".autoskillit/temp/.hook_config.json"


def _make_clean_env(
    skill_name: str | None,
    session_type: str | None = None,
    headless: bool = True,
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    if skill_name is not None:
        env["AUTOSKILLIT_SKILL_NAME"] = skill_name
    else:
        env.pop("AUTOSKILLIT_SKILL_NAME", None)
    if session_type is not None:
        env["AUTOSKILLIT_SESSION_TYPE"] = session_type
    else:
        env.pop("AUTOSKILLIT_SESSION_TYPE", None)
    return env


def _run_guard(
    cmd: str,
    kitchen_open: bool,
    tmpdir,
    raw_stdin: str | None = None,
    skill_name: str | None = None,
    session_type: str | None = None,
    use_bash_key: bool = False,
    headless: bool = True,
    include_execution_cwd: bool = True,
    hook_config: dict[str, object] | None = None,
) -> str:
    """Invoke git_ops_guard.main() and return captured stdout."""
    from autoskillit.hooks.guards.git_ops_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        cmd_key = "command" if use_bash_key else "cmd"
        tool_input = {cmd_key: cmd}
        if include_execution_cwd and not use_bash_key:
            tool_input["cwd"] = str(tmpdir)
        tool_name = _BASH_TOOL_NAME if use_bash_key else _TOOL_NAME
        stdin_payload = {
            "session_id": "guard-test-session",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        if include_execution_cwd and use_bash_key:
            stdin_payload["cwd"] = str(tmpdir)
        stdin_content = json.dumps(stdin_payload)

    if kitchen_open:
        hook_cfg = tmpdir / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps(hook_config or {"kitchen": "open"}))

    clean_env = _make_clean_env(skill_name, session_type, headless=headless)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
            with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
                with unittest.mock.patch("pathlib.Path.cwd", return_value=tmpdir):
                    try:
                        main()
                    except SystemExit as exc:
                        assert exc.code == 0, f"Guard exited non-zero: {exc.code!r}"

    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _git(cwd: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    ).stdout.strip()


@pytest.fixture
def linked_repo(tmp_path: Path) -> dict[str, Path | str]:
    primary = tmp_path / "repo"
    linked = tmp_path / "review"
    primary.mkdir()
    _git(primary, "init", "-b", "develop")
    _git(primary, "config", "user.name", "Guard Test")
    _git(primary, "config", "user.email", "guard@example.invalid")
    (primary / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "-m", "initial")
    old_sha = _git(primary, "rev-parse", "HEAD")
    tree = _git(primary, "rev-parse", "HEAD^{tree}")
    new_sha = _git(primary, "commit-tree", tree, "-p", old_sha, input_text="next\n")
    _git(primary, "worktree", "add", "-b", "review", str(linked), old_sha)
    return {
        "primary": primary,
        "linked": linked,
        "old_sha": old_sha,
        "new_sha": new_sha,
        "worktree_git_dir": _git(linked, "rev-parse", "--absolute-git-dir"),
        "common_git_dir": str((linked / _git(linked, "rev-parse", "--git-common-dir")).resolve()),
    }


def _checked_out_ref_result(output: str) -> dict[str, object]:
    data = json.loads(output)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    prefix = "Checked-out ref mutation blocked: "
    assert reason.startswith(prefix)
    return json.loads(reason.removeprefix(prefix))


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
        out = _run_guard("git push origin main", kitchen_open=True, tmpdir=tmp_path)
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

    def test_allows_unrelated_command(self, tmp_path):
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

    def test_allows_env_prefix_git_amend(self, tmp_path):
        # env-prefix pattern (VAR=1 git ...) fails-open matching artifact_download_guard
        out = _run_guard("VAR=1 git commit --amend", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""


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
        "command",
        ["git update-ref -d refs/heads/develop", "git push . :develop"],
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


class TestCheckedOutRefAmbiguity:
    @pytest.mark.parametrize(
        "command",
        [
            "git update-ref --stdin",
            "git branch -f develop $TARGET",
            "git fetch --stdin origin",
            "git push . HEAD:$TARGET",
            'printf x | tee "$TARGET"',
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


class TestCheckedOutRefAllows:
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
