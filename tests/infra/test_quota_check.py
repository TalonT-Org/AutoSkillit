"""Tests for the quota_check PreToolUse hook."""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import autoskillit.hooks.quota_check as quota_mod


def _run_quota_hook(stdin_data: str) -> tuple[str, str]:
    """Run main() with given stdin; return (stdout, stderr) as stripped strings."""
    buf_out = io.StringIO()
    buf_err = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(stdin_data)),
        redirect_stdout(buf_out),
        redirect_stderr(buf_err),
    ):
        try:
            quota_mod.main()
        except SystemExit:
            pass
    return buf_out.getvalue().strip(), buf_err.getvalue().strip()


# QC1
def test_malformed_stdin_approves():
    """Malformed JSON stdin → fail-open (empty stdout)."""
    stdout, _ = _run_quota_hook("not-json{{{")
    assert stdout == ""


# QC2
def test_subprocess_timeout_approves():
    """subprocess.TimeoutExpired → fail-open (empty stdout)."""
    with patch.object(
        quota_mod.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd="autoskillit", timeout=15),
    ):
        stdout, _ = _run_quota_hook("{}")
    assert stdout == ""


# QC3
def test_subprocess_oserror_approves():
    """OSError (e.g., module not found) → fail-open (empty stdout)."""
    with patch.object(quota_mod.subprocess, "run", side_effect=OSError("not found")):
        stdout, _ = _run_quota_hook("{}")
    assert stdout == ""


# QC4
def test_subprocess_nonzero_returncode_approves():
    """Non-zero subprocess returncode → fail-open (empty stdout)."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    with patch.object(quota_mod.subprocess, "run", return_value=mock_proc):
        stdout, _ = _run_quota_hook("{}")
    assert stdout == ""


# QC5
def test_quota_not_exceeded_approves():
    """should_sleep=False → approve (empty stdout)."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"should_sleep": False})
    with patch.object(quota_mod.subprocess, "run", return_value=mock_proc):
        stdout, _ = _run_quota_hook("{}")
    assert stdout == ""


# QC6
def test_quota_exceeded_denies():
    """should_sleep=True → deny with sleep instruction."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"should_sleep": True, "sleep_seconds": 30})
    with patch.object(quota_mod.subprocess, "run", return_value=mock_proc):
        stdout, _ = _run_quota_hook("{}")
    assert stdout != ""
    data = json.loads(stdout)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


# QC7
def test_subprocess_unexpected_error_logs_stderr_and_approves():
    """RuntimeError from subprocess → logs to stderr, approves (fail-open for quota)."""
    with patch.object(quota_mod.subprocess, "run", side_effect=RuntimeError("boom")):
        stdout, stderr = _run_quota_hook("{}")
    assert stdout == ""  # approve — no deny output
    assert stderr != ""  # unexpected error logged to stderr
