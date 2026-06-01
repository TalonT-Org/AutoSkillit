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
import unittest.mock

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

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
) -> str:
    """Invoke git_ops_guard.main() and return captured stdout."""
    from autoskillit.hooks.guards.git_ops_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        cmd_key = "command" if use_bash_key else "cmd"
        tool_input = {cmd_key: cmd, "cwd": str(tmpdir)}
        tool_name = _BASH_TOOL_NAME if use_bash_key else _TOOL_NAME
        stdin_payload = {"tool_name": tool_name, "tool_input": tool_input}
        stdin_content = json.dumps(stdin_payload)

    if kitchen_open:
        hook_cfg = tmpdir / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps({"kitchen": "open"}))

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
