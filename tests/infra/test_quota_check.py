"""Tests for the quota_check PreToolUse hook."""

import io
import json
import subprocess
from contextlib import redirect_stdout
from unittest.mock import patch


def _run_hook(
    event: dict | None = None,
    subprocess_stdout: str = "",
    subprocess_returncode: int = 0,
    subprocess_raises: Exception | None = None,
    raw_stdin: str | None = None,
) -> str:
    """
    Run quota_check.main() with synthetic stdin and mocked subprocess.
    Returns captured stdout (empty string = approve, JSON string = deny).
    """
    from autoskillit.hooks.quota_check import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=subprocess_returncode,
        stdout=subprocess_stdout,
        stderr="",
    )

    def mock_run(*args, **kwargs):
        if subprocess_raises is not None:
            raise subprocess_raises
        return completed

    with patch("sys.stdin", io.StringIO(stdin_text)):
        with patch("subprocess.run", side_effect=mock_run):
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    main()
                except SystemExit:
                    pass
            return buf.getvalue()


def test_deny_when_should_sleep_true():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout=json.dumps({"should_sleep": True, "sleep_seconds": 120}),
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_deny_message_contains_sleep_seconds():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout=json.dumps({"should_sleep": True, "sleep_seconds": 300}),
    )
    data = json.loads(out)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "300" in reason


def test_approve_when_should_sleep_false():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout=json.dumps({"should_sleep": False}),
    )
    assert out.strip() == ""


def test_approve_on_nonzero_returncode():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout=json.dumps({"should_sleep": True, "sleep_seconds": 60}),
        subprocess_returncode=1,
    )
    assert out.strip() == ""


def test_approve_on_malformed_json_from_subprocess():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout="not-json",
    )
    assert out.strip() == ""


def test_approve_on_subprocess_exception():
    cmd = ["python", "-m", "autoskillit", "quota-status"]
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_raises=subprocess.TimeoutExpired(cmd, timeout=15),
    )
    assert out.strip() == ""


def test_approve_on_malformed_stdin():
    out = _run_hook(raw_stdin="not-json")
    assert out.strip() == ""


def test_deny_output_is_valid_json():
    out = _run_hook(
        event={"tool_name": "run_skill"},
        subprocess_stdout=json.dumps({"should_sleep": True, "sleep_seconds": 60}),
    )
    parsed = json.loads(out)
    assert "hookSpecificOutput" in parsed
