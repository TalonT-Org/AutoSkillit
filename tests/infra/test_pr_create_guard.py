"""Tests for the pr_create_guard PreToolUse hook.

Guards against `gh pr create` being issued via run_cmd when the kitchen is
open, enforcing the mandatory prepare_pr → compose_pr pipeline.

Pattern mirrors test_unsafe_install_guard.py exactly.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_TOOL_NAME = "mcp__autoskillit__local__autoskillit__run_cmd"
_HOOK_CONFIG_RELPATH = ".autoskillit/temp/.hook_config.json"


def _run_guard(cmd: str, kitchen_open: bool, tmpdir, raw_stdin: str | None = None) -> str:
    """Invoke pr_create_guard.main() and return captured stdout."""
    from autoskillit.hooks.pr_create_guard import main  # noqa: PLC0415

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

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
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
