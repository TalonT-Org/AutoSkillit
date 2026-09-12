"""Tests for the artifact_download_guard PreToolUse hook.

Guards against `gh run download` and `gh release download` without --dir,
which would dump CI artifacts into the project root.

Pattern mirrors test_pr_create_guard.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock

import pytest

from tests.hooks._evaluation_shape_matrix import EVALUATION_SHAPE_MATRIX

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_TOOL_NAME = "mcp__autoskillit__local__autoskillit__run_cmd"
_BASH_TOOL_NAME = "Bash"


def _run_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Invoke artifact_download_guard.main() with run_cmd format and return stdout."""
    from autoskillit.hooks.guards.artifact_download_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        tool_input = {"cmd": cmd}
        stdin_payload = {"tool_name": _TOOL_NAME, "tool_input": tool_input}
        stdin_content = json.dumps(stdin_payload)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
            try:
                main()
            except SystemExit as exc:
                assert exc.code == 0, f"Guard exited non-zero: {exc.code!r}"

    return buf.getvalue()


def _run_bash_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Invoke artifact_download_guard.main() with Bash tool format and return stdout."""
    from autoskillit.hooks.guards.artifact_download_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        tool_input = {"command": cmd}
        stdin_payload = {"tool_name": _BASH_TOOL_NAME, "tool_input": tool_input}
        stdin_content = json.dumps(stdin_payload)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
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
# Denied cases: gh run download without --dir
# ---------------------------------------------------------------------------


class TestArtifactDownloadGuardDenied:
    def test_denies_gh_run_download_without_dir(self):
        out = _run_guard("gh run download 12345")
        assert _is_denied(out)

    def test_deny_reason_mentions_dir_flag(self):
        out = _run_guard("gh run download 12345")
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "--dir" in reason, "Deny reason must mention --dir requirement"

    def test_denies_gh_release_download_without_dir(self):
        out = _run_guard("gh release download v1.0")
        assert _is_denied(out)

    def test_denies_chained_unguarded_download_after_guarded(self):
        out = _run_guard("gh run download 123 --dir /tmp/out && gh run download 456")
        assert _is_denied(out)

    def test_denies_when_dir_belongs_to_different_command(self):
        out = _run_guard("gh run download 123 && other-cmd --dir /tmp")
        assert _is_denied(out)

    def test_denies_gh_run_download_piped(self):
        out = _run_guard("gh run download 123 | head")
        assert _is_denied(out)

    def test_denies_via_bash_tool(self):
        out = _run_bash_guard("gh run download 12345")
        assert _is_denied(out)

    def test_denies_gh_release_download_via_bash_tool(self):
        out = _run_bash_guard("gh release download v1.0")
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Allowed cases: download with --dir or -D flag
# ---------------------------------------------------------------------------


class TestArtifactDownloadGuardAllowed:
    def test_allows_gh_run_download_with_dir_flag(self):
        out = _run_guard("gh run download 12345 --dir /tmp/out")
        assert out.strip() == ""

    def test_allows_gh_run_download_with_short_d_flag(self):
        out = _run_guard("gh run download 12345 -D /tmp/out")
        assert out.strip() == ""

    def test_allows_gh_release_download_with_dir_flag(self):
        out = _run_guard("gh release download v1.0 --dir /tmp/out")
        assert out.strip() == ""

    def test_allows_gh_release_download_with_short_d_flag(self):
        out = _run_guard("gh release download v1.0 -D /tmp/out")
        assert out.strip() == ""

    def test_allows_gh_run_view(self):
        out = _run_guard("gh run view 123")
        assert out.strip() == ""

    def test_allows_gh_run_list(self):
        out = _run_guard("gh run list")
        assert out.strip() == ""

    def test_allows_unrelated_command(self):
        out = _run_guard("npm run build")
        assert out.strip() == ""

    def test_allows_git_command(self):
        out = _run_guard("git status")
        assert out.strip() == ""

    def test_allows_via_bash_tool_with_dir(self):
        out = _run_bash_guard("gh run download 12345 --dir /tmp/out")
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Edge cases: fail-open on malformed or ambiguous input
# ---------------------------------------------------------------------------


class TestArtifactDownloadGuardEvaluationShapeMatrix:
    """Deny family proven through every semantically executing EVALUATION_SHAPE_MATRIX shape."""

    @pytest.mark.parametrize(
        "shape", [s for s in EVALUATION_SHAPE_MATRIX if s.executes], ids=lambda s: s.id
    )
    def test_executing_shape_denies_gh_run_download(self, shape) -> None:
        out = _run_guard(shape.build("gh run download 123"))
        assert _is_denied(out), f"shape {shape.id!r} must deny"

    @pytest.mark.parametrize(
        "shape", [s for s in EVALUATION_SHAPE_MATRIX if not s.executes], ids=lambda s: s.id
    )
    def test_inert_shape_allows_gh_run_download(self, shape) -> None:
        out = _run_guard(shape.build("gh run download 123"))
        assert out.strip() == "", f"shape {shape.id!r} must allow"


class TestArtifactDownloadGuardEdgeCases:
    def test_fails_open_on_malformed_stdin(self):
        out = _run_guard("", raw_stdin="not-json{{{")
        assert out.strip() == "", "Malformed JSON must fail open"

    def test_fails_open_on_missing_cmd_field(self):
        stdin = json.dumps({"tool_name": _TOOL_NAME, "tool_input": {}})
        out = _run_guard("", raw_stdin=stdin)
        assert out.strip() == ""

    def test_fails_open_on_empty_command(self):
        out = _run_guard("")
        assert out.strip() == ""

    def test_fails_open_on_unclosed_quotes(self):
        out = _run_guard('gh run download 123 --name "unclosed')
        assert out.strip() == "", "Unclosed quotes must fail open"

    def test_allows_gh_run_download_in_quoted_string(self):
        out = _run_guard('echo "gh run download 123"')
        assert out.strip() == "", "Quoted string should not match"

    def test_denies_env_prefix_gh_run_download(self):
        # Rectify #4941 Part B: all_evaluated_segments + command_verb_and_args
        # skip a leading POSIX assignment to find the verb, so a bare
        # env-var-prefixed invocation is now visible to the guard (same
        # decision Part A recorded for git_ops_guard's env-assignment case).
        out = _run_guard("VAR=1 gh run download 123")
        assert _is_denied(out), "Env-var prefix before gh must now be denied"
