"""Tests for the pretty_output PostToolUse hook.

The hook reformats raw MCP tool JSON responses into Markdown-KV format
before Claude consumes them. Fails open on any error.
"""

from __future__ import annotations

import io
import json
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, int]:
    """Run pretty_output.main() with synthetic stdin.

    Returns (stdout_output, exit_code).
    """
    from autoskillit.hooks.pretty_output import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if cwd is not None:
            stack.enter_context(
                patch("autoskillit.hooks.pretty_output.Path.cwd", return_value=cwd)
            )
        try:
            main()
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0

    return buf.getvalue(), exit_code


def _make_run_skill_event(
    success: bool = True,
    result: str = "Done.",
    session_id: str = "abc",
    subtype: str = "end_turn",
    is_error: bool = False,
    exit_code: int = 0,
    needs_retry: bool = False,
    retry_reason: str = "none",
    stderr: str = "",
    token_usage: dict | None = None,
    worktree_path: str = "",
) -> dict:
    return {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_response": json.dumps(
            {
                "success": success,
                "result": result,
                "session_id": session_id,
                "subtype": subtype,
                "is_error": is_error,
                "exit_code": exit_code,
                "needs_retry": needs_retry,
                "retry_reason": retry_reason,
                "stderr": stderr,
                "token_usage": token_usage,
                "worktree_path": worktree_path,
            }
        ),
    }


# PHK-1
def test_hook_script_exists():
    """pretty_output.py must exist in the hooks directory."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "pretty_output.py").exists()


# PHK-2
def test_hook_emits_posttooluse_event_name():
    """Hook output JSON must have hookSpecificOutput.hookEventName == 'PostToolUse'."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hi", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event)
    assert out.strip(), "Expected non-empty output"
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


# PHK-3
def test_hook_emits_updated_mcp_tool_output_field():
    """Hook output must have non-empty hookSpecificOutput.updatedMCPToolOutput."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hi", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["updatedMCPToolOutput"]


# PHK-4
def test_hook_fail_open_on_invalid_json_stdin():
    """Non-JSON stdin → exit 0, no stdout output."""
    out, code = _run_hook(raw_stdin="not valid json {{{{")
    assert code == 0
    assert out.strip() == ""


# PHK-5
def test_hook_fail_open_on_missing_tool_response():
    """Valid JSON but missing tool_response key → exit 0, no stdout."""
    event = {"tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd"}
    out, code = _run_hook(event=event)
    assert code == 0
    assert out.strip() == ""


# PHK-6
def test_format_run_skill_success():
    """run_skill success response must contain tool name, checkmark, and success field."""
    event = _make_run_skill_event(success=True)
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_skill" in text
    assert "\u2713" in text  # ✓
    assert "success: True" in text


# PHK-7
def test_format_run_skill_failure_with_retry():
    """run_skill failure with retry must show cross, retry fields, and worktree_path."""
    event = _make_run_skill_event(
        success=False,
        needs_retry=True,
        retry_reason="budget_exhausted",
        worktree_path="/tmp/wt/fix-abc",
    )
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2717" in text  # ✗
    assert "needs_retry: True" in text
    assert "retry_reason: budget_exhausted" in text
    assert "worktree_path: /tmp/wt/fix-abc" in text


# PHK-8
def test_format_run_skill_gate_error():
    """run_skill gate_error subtype must show 'Gate Error' and 'gate_error'."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_response": json.dumps(
            {
                "success": False,
                "subtype": "gate_error",
                "is_error": True,
                "result": "Kitchen is closed.",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "Gate Error" in text
    assert "gate_error" in text


# PHK-9
def test_format_run_cmd_success_shows_stdout():
    """run_cmd success must show tool name, checkmark, stdout content."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hello\nworld\n", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_cmd" in text
    assert "\u2713" in text  # ✓
    assert "stdout" in text
    assert "hello" in text
    assert "world" in text


# PHK-10
def test_format_run_cmd_failure_shows_stderr():
    """run_cmd failure must show cross and stderr content."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "No such file"}
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2717" in text  # ✗
    assert "stderr" in text
    assert "No such file" in text


# PHK-11
def test_format_test_check_pass():
    """test_check pass must show tool name, checkmark, PASS, passed: True."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__test_check",
        "tool_response": json.dumps({"passed": True, "output": "...245 passed..."}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "test_check" in text
    assert "\u2713" in text  # ✓
    assert "PASS" in text
    assert "passed: True" in text


# PHK-12
def test_format_test_check_fail_shows_failure_lines():
    """test_check fail must show failure lines and filter pytest boilerplate."""
    pytest_output = (
        "platform linux -- Python 3.13.2\n"
        "rootdir: /repo\n"
        "collecting ...\n"
        "collected 100 items\n"
        "FAILED tests/foo.py::test_a - AssertionError: 1 != 2\n"
        "FAILED tests/bar.py::test_b - RuntimeError: oops\n"
        "2 failed, 98 passed\n"
    )
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__test_check",
        "tool_response": json.dumps({"passed": False, "output": pytest_output}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2717" in text  # ✗
    assert "FAIL" in text
    assert "FAILED tests/foo.py::test_a" in text
    assert "FAILED tests/bar.py::test_b" in text
    assert "platform linux" not in text
    assert "collecting ..." not in text


# PHK-13
def test_format_merge_worktree_failure():
    """merge_worktree failure must include all key fields."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "error": "Rebase failed",
                "failed_step": "rebase",
                "state": "worktree_dirty",
                "worktree_path": "/tmp/wt/fix",
                "stderr": "CONFLICT (content): Merge conflict in foo.py",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "merge_worktree" in text
    assert "\u2717" in text  # ✗
    assert "error: Rebase failed" in text
    assert "failed_step: rebase" in text
    assert "state: worktree_dirty" in text
    assert "CONFLICT" in text


# PHK-14
def test_format_merge_worktree_success():
    """merge_worktree success must show checkmark and merged: True."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "merged": True,
                "worktree_path": "/tmp/wt/fix",
                "branch": "impl-fix-20260101",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "merge_worktree" in text
    assert "\u2713" in text  # ✓
    assert "merged: True" in text


# PHK-15
def test_format_get_token_summary_compact():
    """get_token_summary must show compact per-step lines and totals."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "investigate",
                        "invocation_count": 1,
                        "input_tokens": 45200,
                        "output_tokens": 12800,
                        "cache_read_input_tokens": 1200000,
                        "cache_creation_input_tokens": 0,
                    },
                    {
                        "step_name": "make_plan",
                        "invocation_count": 2,
                        "input_tokens": 30000,
                        "output_tokens": 8000,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 500000,
                    },
                    {
                        "step_name": "implement",
                        "invocation_count": 1,
                        "input_tokens": 60000,
                        "output_tokens": 15000,
                        "cache_read_input_tokens": 2000000,
                        "cache_creation_input_tokens": 0,
                    },
                ],
                "total": {
                    "input_tokens": 135200,
                    "output_tokens": 35800,
                    "cache_read_input_tokens": 3200000,
                    "cache_creation_input_tokens": 500000,
                },
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "token_summary" in text
    # One line per step in compact format
    assert "investigate" in text
    assert "make_plan" in text
    assert "implement" in text
    assert "total_in:" in text
    assert "total_out:" in text
    assert "total_cached:" in text


# PHK-16
def test_format_kitchen_status():
    """kitchen_status must show package_version and tools_enabled."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__kitchen_status",
        "tool_response": json.dumps(
            {
                "package_version": "0.2.0",
                "plugin_json_version": "0.2.0",
                "versions_match": True,
                "tools_enabled": True,
                "token_usage_verbosity": "summary",
                "quota_guard_enabled": True,
                "github_token_configured": True,
                "github_default_repo": "acme/myrepo",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "kitchen_status" in text
    assert "package_version: 0.2.0" in text
    assert "tools_enabled: True" in text


# PHK-17
def test_pipeline_mode_compact_run_skill(tmp_path):
    """In pipeline mode (hook config present), run_skill output uses compact format."""
    # Create the hook config file to signal pipeline mode
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".autoskillit_hook_config.json").write_text('{"quota_guard": {}}')

    event = _make_run_skill_event(success=False, needs_retry=True, retry_reason="budget_exhausted")

    with patch("autoskillit.hooks.pretty_output.Path.cwd", return_value=tmp_path):
        out, _ = _run_hook(event=event, cwd=tmp_path)

    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    # Compact format: single-line header, not full ## header
    assert "run_skill:" in text
    assert "FAIL" in text
    # Should not have the full interactive ## header format
    assert "## run_skill" not in text


# PHK-18
def test_unknown_tool_passes_through():
    """Unknown tool name gets generic key-value rendering."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__some_unknown_tool",
        "tool_response": json.dumps({"result": "ok", "status": "done"}),
    }
    out, code = _run_hook(event=event)
    assert code == 0
    # Should produce output (generic formatter)
    assert out.strip()
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "some_unknown_tool" in text
