"""Tests for the pr_create_guard PreToolUse hook.

Guards against `gh pr create` being issued via run_cmd when the kitchen is
open, enforcing the mandatory prepare_pr → compose_pr pipeline.

Pattern mirrors test_unsafe_install_guard.py exactly.
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


def _make_clean_env(skill_name: str | None, session_type: str | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()}
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
) -> str:
    """Invoke pr_create_guard.main() and return captured stdout."""
    from autoskillit.hooks.guards.pr_create_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        tool_input = {"cmd": cmd, "cwd": str(tmpdir)}
        stdin_payload = {"tool_name": _TOOL_NAME, "tool_input": tool_input}
        stdin_content = json.dumps(stdin_payload)

    if kitchen_open:
        hook_cfg = tmpdir / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps({"kitchen": "open"}))

    clean_env = _make_clean_env(skill_name, session_type)

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


def _run_bash_guard(
    cmd: str,
    kitchen_open: bool,
    tmpdir,
    raw_stdin: str | None = None,
    skill_name: str | None = None,
    session_type: str | None = None,
) -> str:
    """Invoke pr_create_guard.main() with Bash tool format and return captured stdout."""
    from autoskillit.hooks.guards.pr_create_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        tool_input = {"command": cmd, "cwd": str(tmpdir)}
        stdin_payload = {"tool_name": _BASH_TOOL_NAME, "tool_input": tool_input}
        stdin_content = json.dumps(stdin_payload)

    if kitchen_open:
        hook_cfg = tmpdir / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps({"kitchen": "open"}))

    clean_env = _make_clean_env(skill_name, session_type)

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
# Denied cases
# ---------------------------------------------------------------------------


class TestPrCreateGuardDenied:
    def test_denies_gh_pr_create_when_kitchen_open(self, tmp_path):
        out = _run_guard("gh pr create --title foo --body bar", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)

    def test_deny_reason_mentions_pipeline(self, tmp_path):
        out = _run_guard("gh pr create --title foo", kitchen_open=True, tmpdir=tmp_path)
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "prepare_pr" in reason, "Deny reason must name the mandatory pipeline step"

    def test_denies_gh_pr_create_with_extra_flags(self, tmp_path):
        out = _run_guard(
            "gh pr create -t 'fix' -b 'desc' --base main",
            kitchen_open=True,
            tmpdir=tmp_path,
        )
        assert _is_denied(out)

    def test_denies_gh_pr_create_with_leading_whitespace(self, tmp_path):
        out = _run_guard("  gh pr create --title x", kitchen_open=True, tmpdir=tmp_path)
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Allowed cases
# ---------------------------------------------------------------------------


class TestBashToolDenyPath:
    """Bash tool sends command via 'command' key — guard must handle this format."""

    def test_denies_gh_pr_create_via_bash_tool(self, tmp_path):
        out = _run_bash_guard(
            "gh pr create --title foo --body bar", kitchen_open=True, tmpdir=tmp_path
        )
        assert _is_denied(out)

    def test_denies_gh_pr_create_with_flags_via_bash_tool(self, tmp_path):
        out = _run_bash_guard(
            "gh pr create -t 'fix' -b 'desc'", kitchen_open=True, tmpdir=tmp_path
        )
        assert _is_denied(out)

    def test_allows_non_pr_create_via_bash_tool(self, tmp_path):
        out = _run_bash_guard("gh pr list", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_when_kitchen_closed_via_bash_tool(self, tmp_path):
        out = _run_bash_guard("gh pr create --title foo", kitchen_open=False, tmpdir=tmp_path)
        assert out.strip() == ""


class TestPrCreateGuardAllowed:
    def test_allows_when_kitchen_closed(self, tmp_path):
        out = _run_guard("gh pr create --title foo", kitchen_open=False, tmpdir=tmp_path)
        assert out.strip() == "", "No output means allow"

    def test_allows_non_pr_create_commands(self, tmp_path):
        out = _run_guard("gh pr list", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_gh_pr_merge_unchanged(self, tmp_path):
        # gh pr merge is a separate concern; this guard must not over-block
        out = _run_guard("gh pr merge --squash 42", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_gh_pr_view(self, tmp_path):
        out = _run_guard("gh pr view 99", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_unrelated_run_cmd(self, tmp_path):
        out = _run_guard("npm run build", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""

    def test_allows_unrelated_git_command(self, tmp_path):
        out = _run_guard("git status", kitchen_open=True, tmpdir=tmp_path)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Edge cases: fail-open on malformed input
# ---------------------------------------------------------------------------


class TestPrCreateGuardEdgeCases:
    def test_fails_open_on_malformed_stdin(self, tmp_path):
        out = _run_guard("", kitchen_open=False, tmpdir=tmp_path, raw_stdin="not-json{{{")
        assert out.strip() == "", "Malformed JSON must fail open (no output = allow)"

    def test_fails_open_on_missing_cmd_field(self, tmp_path):
        stdin = json.dumps({"tool_name": _TOOL_NAME, "tool_input": {}})
        out = _run_guard("", kitchen_open=False, tmpdir=tmp_path, raw_stdin=stdin)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Exempt skills: pipeline skills that legitimately call gh pr create
# ---------------------------------------------------------------------------


class TestExemptSkills:
    """Tests that exempt pipeline skills are allowed to call gh pr create."""

    @pytest.mark.parametrize(
        "skill_name",
        [
            "compose-pr",
            "compose-research-pr",
            "open-integration-pr",
            "promote-to-main",
            "pipeline-summary",
        ],
    )
    def test_allows_exempt_skill(self, tmp_path, skill_name: str) -> None:
        """Exempt skills must be allowed even when kitchen is open."""
        out = _run_guard(
            "gh pr create --title foo --body bar",
            kitchen_open=True,
            tmpdir=tmp_path,
            skill_name=skill_name,
        )
        assert out.strip() == "", f"Exempt skill {skill_name!r} must be allowed"

    def test_still_blocks_unknown_skill(self, tmp_path) -> None:
        """Non-exempt skills must still be denied when kitchen is open."""
        out = _run_guard(
            "gh pr create --title foo",
            kitchen_open=True,
            tmpdir=tmp_path,
            skill_name="investigate",
        )
        assert _is_denied(out), "Non-exempt skill must be denied"

    def test_still_blocks_no_skill_name(self, tmp_path) -> None:
        """When AUTOSKILLIT_SKILL_NAME is absent, guard must deny."""
        out = _run_guard(
            "gh pr create --title foo",
            kitchen_open=True,
            tmpdir=tmp_path,
            skill_name=None,
        )
        assert _is_denied(out), "Missing skill name must be denied"

    def test_allows_exempt_skill_via_bash_tool(self, tmp_path) -> None:
        """Exempt skills via Bash tool must also be allowed."""
        out = _run_bash_guard(
            "gh pr create --title foo --body bar",
            kitchen_open=True,
            tmpdir=tmp_path,
            skill_name="compose-pr",
        )
        assert out.strip() == "", "Exempt skill via Bash tool must be allowed"


# ---------------------------------------------------------------------------
# Orchestrator session exemption
# ---------------------------------------------------------------------------


class TestOrchestratorSessionExemption:
    """Orchestrator sessions may call gh pr create even with kitchen open."""

    def test_orchestrator_session_allowed_run_cmd(self, tmp_path):
        out = _run_guard(
            "gh pr create --base main --head feat",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="orchestrator",
        )
        assert out.strip() == "", "Orchestrator session must be allowed"

    def test_orchestrator_session_allowed_bash(self, tmp_path):
        out = _run_bash_guard(
            "gh pr create --base main --head feat",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="orchestrator",
        )
        assert out.strip() == "", "Orchestrator session via Bash must be allowed"

    def test_skill_session_still_denied(self, tmp_path):
        out = _run_guard(
            "gh pr create --base main --head feat",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="skill",
        )
        assert _is_denied(out), "Skill session must be denied"

    def test_fleet_session_still_denied(self, tmp_path):
        out = _run_guard(
            "gh pr create --base main --head feat",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type="fleet",
        )
        assert _is_denied(out), "Fleet session must be denied"

    def test_no_session_type_still_denied(self, tmp_path):
        out = _run_guard(
            "gh pr create --base main --head feat",
            kitchen_open=True,
            tmpdir=tmp_path,
            session_type=None,
        )
        assert _is_denied(out), "Missing session type must be denied"


# ---------------------------------------------------------------------------
# Interactive kitchen exemption via recipe authorization
# ---------------------------------------------------------------------------


class TestInteractiveKitchenExemption:
    """Interactive kitchen sessions with recipe_allows_pr_create must be allowed."""

    def test_interactive_kitchen_with_recipe_allows_pr_create(self, tmp_path) -> None:
        """Kitchen open + recipe_allows_pr_create=true → allow."""
        hook_cfg = tmp_path / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(
            json.dumps(
                {
                    "recipe_allows_pr_create": True,
                    "quota_guard": {"cache_max_age": 300},
                    "kitchen_id": "test-kitchen-id",
                }
            )
        )
        from autoskillit.hooks.guards.pr_create_guard import main

        stdin_content = json.dumps(
            {
                "tool_name": _TOOL_NAME,
                "tool_input": {"cmd": "gh pr create --title foo --body bar"},
            }
        )
        clean_env = _make_clean_env(skill_name=None, session_type=None)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
                with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
                    with unittest.mock.patch("pathlib.Path.cwd", return_value=tmp_path):
                        try:
                            main()
                        except SystemExit as exc:
                            assert exc.code == 0

        assert buf.getvalue().strip() == "", (
            "Interactive kitchen with recipe_allows_pr_create must be allowed"
        )

    def test_interactive_kitchen_without_recipe_flag_still_denied(self, tmp_path) -> None:
        """Kitchen open + no recipe_allows_pr_create → deny."""
        out = _run_guard(
            "gh pr create --title foo --body bar",
            kitchen_open=True,
            tmpdir=tmp_path,
            skill_name=None,
            session_type=None,
        )
        assert _is_denied(out), "Kitchen open without recipe flag must be denied"

    def test_interactive_kitchen_recipe_flag_false_still_denied(self, tmp_path) -> None:
        """Kitchen open + recipe_allows_pr_create=false → deny."""
        hook_cfg = tmp_path / _HOOK_CONFIG_RELPATH
        hook_cfg.parent.mkdir(parents=True, exist_ok=True)
        hook_cfg.write_text(json.dumps({"recipe_allows_pr_create": False, "kitchen_id": "test"}))
        from autoskillit.hooks.guards.pr_create_guard import main

        stdin_content = json.dumps(
            {
                "tool_name": _TOOL_NAME,
                "tool_input": {"cmd": "gh pr create --title foo"},
            }
        )
        clean_env = _make_clean_env(skill_name=None, session_type=None)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
                with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
                    with unittest.mock.patch("pathlib.Path.cwd", return_value=tmp_path):
                        try:
                            main()
                        except SystemExit as exc:
                            assert exc.code == 0

        assert _is_denied(buf.getvalue()), (
            "Kitchen open with recipe_allows_pr_create=false must be denied"
        )
