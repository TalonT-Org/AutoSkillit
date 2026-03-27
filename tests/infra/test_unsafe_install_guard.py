"""Tests for the unsafe_install_guard PreToolUse hook."""
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest


def _run_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process and return captured stdout."""
    from autoskillit.hooks.unsafe_install_guard import main

    tool_input = {"cmd": cmd, "cwd": "/some/path"}
    stdin_content = raw_stdin if raw_stdin is not None else json.dumps({
        "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
        "tool_input": tool_input,
    })
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return (
        data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


class TestUnsafeInstallGuardDenied:
    """Commands that should be blocked."""

    def test_pip_install_editable_without_python_venv(self):
        assert _is_denied(_run_guard("pip install -e ."))

    def test_pip_install_editable_long_flag(self):
        assert _is_denied(_run_guard("pip install --editable ."))

    def test_uv_pip_install_editable_without_python_venv(self):
        assert _is_denied(_run_guard("uv pip install -e ."))

    def test_uv_pip_install_editable_without_python_venv_subdir(self):
        assert _is_denied(_run_guard("uv pip install -e '.[dev]'"))

    def test_uv_pip_install_editable_with_wrong_python_target(self):
        """--python pointing at system Python (not .venv) is still blocked."""
        assert _is_denied(_run_guard(
            "uv pip install -e '.[dev]' --python /usr/bin/python3"
        ))

    def test_pip_install_editable_with_python_system(self):
        """Explicit system Python target is blocked."""
        assert _is_denied(_run_guard(
            "pip install -e . --python /usr/local/micromamba/bin/python3.13"
        ))


class TestUnsafeInstallGuardAllowed:
    """Commands that should be allowed through."""

    def test_uv_pip_install_editable_with_venv_python(self):
        """Editable install targeting .venv is safe — allowed."""
        assert not _is_denied(_run_guard(
            "uv pip install -e '.[dev]' --python .venv/bin/python"
        ))

    def test_uv_pip_install_editable_with_venv_python_absolute(self):
        """Editable install targeting .venv (absolute path) is safe — allowed."""
        assert not _is_denied(_run_guard(
            "uv pip install -e '.[dev]' --python /some/worktree/.venv/bin/python"
        ))

    def test_pip_install_non_editable_allowed(self):
        """Non-editable pip install does not create dangling entry points — allowed."""
        assert not _is_denied(_run_guard("pip install requests"))

    def test_task_install_worktree_allowed(self):
        """task install-worktree always uses --python .venv — allowed."""
        assert not _is_denied(_run_guard("task install-worktree"))

    def test_uv_sync_allowed(self):
        assert not _is_denied(_run_guard("uv sync --all-extras"))

    def test_unrelated_command_allowed(self):
        assert not _is_denied(_run_guard("pytest tests/"))


class TestUnsafeInstallGuardEdgeCases:

    def test_malformed_json_fail_open(self):
        """Malformed stdin → fail-open (no output, no denial)."""
        output = _run_guard("irrelevant", raw_stdin="not-json{{{")
        assert output == ""

    def test_missing_cmd_field_fail_open(self):
        """Missing cmd in tool_input → fail-open."""
        stdin = json.dumps({
            "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
            "tool_input": {},
        })
        output = _run_guard("irrelevant", raw_stdin=stdin)
        assert output == ""
