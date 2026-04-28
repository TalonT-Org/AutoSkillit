"""Tests for the review_loop_gate PreToolUse hook.

Blocks wait_for_ci and enqueue_pr when review_pr returned changes_requested
but check_review_loop has not yet been called.

Pattern mirrors test_pr_create_guard.py exactly.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_STATE_FILE_RELPATH = ".autoskillit/temp/review_gate_state.json"
_TOOL_WAIT_FOR_CI = "mcp__plugin_autoskillit_autoskillit__wait_for_ci"
_TOOL_ENQUEUE_PR = "mcp__plugin_autoskillit_autoskillit__enqueue_pr"


def _write_state(tmp_dir, gate: str, called: bool, pr_number: str = "1290") -> None:
    state_path = tmp_dir / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": gate,
                "review_verdict": "changes_requested",
                "check_review_loop_called": called,
                "pr_number": pr_number,
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )


def _run_gate(tool_name: str, tmp_dir, raw_stdin: str | None = None) -> str:
    """Invoke review_loop_gate.main() and return captured stdout."""
    from autoskillit.hooks.review_loop_gate import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        stdin_content = json.dumps({"tool_name": tool_name, "tool_input": {}})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
            with unittest.mock.patch(
                "autoskillit.hooks.review_loop_gate.Path.cwd", return_value=tmp_dir
            ):
                try:
                    main()
                except SystemExit as exc:
                    assert exc.code == 0, f"Gate exited non-zero: {exc.code!r}"

    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# T1-1: No state file → allow
# ---------------------------------------------------------------------------


class TestReviewLoopGateNoStateFile:
    def test_allows_when_no_state_file(self, tmp_path):
        """T1-1: Fail-open when state file does not exist."""
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert out.strip() == "", "No output means allow"


# ---------------------------------------------------------------------------
# T1-2 through T1-4: State file present
# ---------------------------------------------------------------------------


class TestReviewLoopGateWithStateFile:
    def test_allows_when_gate_is_clear(self, tmp_path):
        """T1-2: CLEAR gate state passes through."""
        _write_state(tmp_path, gate="CLEAR", called=False)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert out.strip() == ""

    def test_denies_when_loop_required_and_not_called(self, tmp_path):
        """T1-3: Core enforcement — blocks merge path when loop not executed."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=False)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert _is_denied(out)

    def test_allows_when_loop_required_and_called(self, tmp_path):
        """T1-4: Loop execution satisfies gate."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=True)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# T1-5 through T1-6: Edge cases
# ---------------------------------------------------------------------------


class TestReviewLoopGateEdgeCases:
    def test_fails_open_on_malformed_state_file(self, tmp_path):
        """T1-5: Fail-open on corrupted state JSON."""
        state_path = tmp_path / _STATE_FILE_RELPATH
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not-json{{{")
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert out.strip() == ""

    def test_deny_message_mentions_check_review_loop(self, tmp_path):
        """T1-6: Deny reason contains actionable callable reference."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=False)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert _is_denied(out)
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "check_review_loop" in reason

    def test_fails_open_on_malformed_stdin(self, tmp_path):
        """T1-9: Fail-open when stdin is not valid JSON."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=False)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path, raw_stdin="not-json{{{")
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# T1-7 through T1-8: Matcher coverage
# ---------------------------------------------------------------------------


class TestReviewLoopGateMatcherCoverage:
    def test_fires_on_wait_for_ci(self, tmp_path):
        """T1-7: Gate fires when tool_name matches wait_for_ci."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=False)
        out = _run_gate(_TOOL_WAIT_FOR_CI, tmp_path)
        assert _is_denied(out)

    def test_fires_on_enqueue_pr(self, tmp_path):
        """T1-8: Gate fires when tool_name matches enqueue_pr."""
        _write_state(tmp_path, gate="LOOP_REQUIRED", called=False)
        out = _run_gate(_TOOL_ENQUEUE_PR, tmp_path)
        assert _is_denied(out)
