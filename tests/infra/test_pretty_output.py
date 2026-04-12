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

from autoskillit.core.types import ChannelConfirmation, TerminationReason
from autoskillit.execution.headless import _build_skill_result
from autoskillit.hooks.pretty_output_hook import _format_response
from tests.conftest import _make_result

# Realistic recipe YAML that mirrors actual bundled recipes.
# Must include an `ingredients:` block to reproduce the raw/derived duplication scenario.
# All formatter tests using `content` should reference this constant.
REALISTIC_RECIPE_YAML = """\
name: implementation
description: Full implementation pipeline
autoskillit_version: "0.3.0"
ingredients:
  task:
    description: What to implement
    required: true
  source_dir:
    description: Path to source directory
    default: ""
  review_approach:
    description: Run review-approach before planning
    default: "false"
kitchen_rules:
  - Always commit before merging
steps:
  implement:
    tool: run_skill
    skill_command: /implement
"""


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    cwd: Path | None = None,
) -> tuple[str, int]:
    """Run pretty_output.main() with synthetic stdin.

    Returns (stdout_output, exit_code).
    """
    from autoskillit.hooks.pretty_output_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    exit_code = 0
    buf = io.StringIO()

    with ExitStack() as stack:
        stack.enter_context(patch("sys.stdin", io.StringIO(stdin_text)))
        stack.enter_context(redirect_stdout(buf))
        if cwd is not None:
            stack.enter_context(
                patch("autoskillit.hooks.pretty_output_hook.Path.cwd", return_value=cwd)
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

    assert (pkg_root() / "hooks" / "pretty_output_hook.py").exists()


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
def test_format_run_skill_success(tmp_path):
    """run_skill success response must contain tool name, checkmark, and success field."""
    event = _make_run_skill_event(success=True)
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_skill" in text
    assert "\u2713" in text  # ✓
    assert "success: True" in text


# PHK-7
def test_format_run_skill_failure_with_retry(tmp_path):
    """run_skill failure with retry must show cross, retry fields, and worktree_path."""
    event = _make_run_skill_event(
        success=False,
        needs_retry=True,
        retry_reason="budget_exhausted",
        worktree_path="/tmp/wt/fix-abc",
    )
    out, _ = _run_hook(event=event, cwd=tmp_path)
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
def test_format_run_cmd_success_shows_stdout(tmp_path):
    """run_cmd success must show tool name, checkmark, stdout content."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": True, "exit_code": 0, "stdout": "hello\nworld\n", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_cmd" in text
    assert "\u2713" in text  # ✓
    assert "stdout" in text
    assert "hello" in text
    assert "world" in text


# PHK-10
def test_format_run_cmd_failure_shows_stderr(tmp_path):
    """run_cmd failure must show cross and stderr content."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {"success": False, "exit_code": 1, "stdout": "", "stderr": "No such file"}
        ),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
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
    """merge_worktree success must show checkmark and merge_succeeded: True."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "merge_succeeded": True,
                "merged_branch": "impl-fix-20260101",
                "into_branch": "main",
                "worktree_removed": True,
                "branch_deleted": True,
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "merge_worktree" in text
    assert "\u2713" in text  # ✓
    assert "merge_succeeded: True" in text


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
    # Assert specific compact line format: name xN [uc:Xk out:Xk cr:XM cw:XM t:Xs]
    assert "investigate x1 [uc:45.2k out:12.8k cr:1.2M cw:0 t:0.0s]" in text
    assert "make_plan x2 [uc:30.0k out:8.0k cr:0 cw:500.0k t:0.0s]" in text
    assert "implement x1 [uc:60.0k out:15.0k cr:2.0M cw:0 t:0.0s]" in text
    assert "total_uncached:" in text
    assert "total_out:" in text
    assert "total_cache_read:" in text
    assert "total_cache_write:" in text


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
    (config_dir / ".hook_config.json").write_text('{"quota_guard": {}}')

    event = _make_run_skill_event(success=False, needs_retry=True, retry_reason="budget_exhausted")

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


# PHK-19
def test_fmt_run_cmd_tool_exception_shows_diagnostic():
    """run_cmd tool_exception subtype must show error, not FAIL []."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": json.dumps(
            {
                "error": "RuntimeError: boom",
                "exit_code": -1,
                "subtype": "tool_exception",
                "success": False,
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "tool exception" in text.lower()
    assert "RuntimeError: boom" in text
    assert "FAIL []" not in text


# PHK-20
def test_fmt_clone_repo_uncommitted_changes_warning():
    """clone_repo uncommitted_changes must show WARNING, not OK."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__clone_repo",
        "tool_response": json.dumps(
            {
                "uncommitted_changes": "true",
                "source_dir": "/src",
                "branch": "main",
                "changed_files": "M file.py",
                "total_changed": "1",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "uncommitted_changes" in text
    assert "changed_files" in text
    assert "\u2713 OK" not in text


# PHK-21
def test_fmt_clone_repo_unpublished_branch_warning():
    """clone_repo unpublished_branch must show WARNING, not OK."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__clone_repo",
        "tool_response": json.dumps(
            {
                "unpublished_branch": "true",
                "branch": "feat/x",
                "source_dir": "/src",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "unpublished_branch" in text
    assert "branch" in text
    assert "\u2713 OK" not in text


# PHK-22
def test_fmt_clone_repo_success_includes_clone_path():
    """clone_repo success must include clone_path, source_dir, remote_url."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__clone_repo",
        "tool_response": json.dumps(
            {
                "clone_path": "/tmp/clone",
                "source_dir": "/src",
                "remote_url": "https://example.com",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "/tmp/clone" in text
    assert "/src" in text
    assert "https://example.com" in text


# PHK-23
def test_fmt_clone_repo_remote_url_failure_includes_stderr():
    """clone_repo remote_url failure must include stderr."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__clone_repo",
        "tool_response": json.dumps(
            {
                "error": "remote_url_rewrite_failed",
                "clone_path": "/tmp/clone",
                "remote_url": "https://example.com",
                "stderr": "fatal: unable to set url",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "fatal: unable to set url" in text


# PHK-24
def test_fmt_merge_worktree_success_shows_metadata():
    """merge_worktree success must show merge_succeeded and merged_branch."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "merge_succeeded": True,
                "merged_branch": "feat/x",
                "into_branch": "main",
                "worktree_removed": True,
                "branch_deleted": True,
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "merge_succeeded" in text
    assert "merged_branch" in text
    assert "\u2713" in text


# PHK-25
def test_fmt_merge_worktree_dirty_tree_shows_files():
    """merge_worktree dirty tree must show dirty_files content."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "error": "dirty working tree",
                "state": "DIRTY_TREE",
                "dirty_files": ["M a.py", "M b.py"],
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "M a.py" in text
    assert "M b.py" in text


# PHK-26
def test_fmt_run_skill_pipeline_includes_stderr(tmp_path):
    """run_skill pipeline mode must include stderr."""
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    (config_dir / ".hook_config.json").write_text('{"quota_guard": {}}')

    event = _make_run_skill_event(
        success=False,
        subtype="execution_failed",
        stderr="ImportError: no module named foo",
        exit_code=1,
        result="",
    )
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "ImportError: no module named foo" in text


# PHK-27
def test_fmt_test_check_error_key_visible():
    """test_check error key must be visible in output."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__test_check",
        "tool_response": json.dumps({"passed": False, "error": "Test runner not configured"}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "error: Test runner not configured" in text


# PHK-28
def test_tool_exception_subtype_routed_before_formatter():
    """tool_exception subtype must be handled before per-tool dispatch."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__merge_worktree",
        "tool_response": json.dumps(
            {
                "success": False,
                "error": "OSError: disk full",
                "exit_code": -1,
                "subtype": "tool_exception",
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "tool exception" in text.lower()
    assert "OSError: disk full" in text


# PHK-29
def test_fmt_generic_preserves_list_values():
    """Generic formatter must render list values, not silently drop them."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__some_tool",
        "tool_response": json.dumps({"total": 3, "items": ["a", "b", "c"]}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "items" in text
    assert "a" in text
    assert "b" in text
    assert "c" in text


# PHK-30
def test_fmt_generic_preserves_dict_values():
    """Generic formatter must render dict values, not silently drop them."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__some_tool",
        "tool_response": json.dumps(
            {"status": "ok", "metadata": {"version": "1.0", "author": "test"}}
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "metadata" in text
    assert "version" in text
    assert "1.0" in text
    assert "author" in text


# PHK-31
def test_fmt_generic_read_db_rows_visible():
    """read_db rows and columns must be visible through generic formatter."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__read_db",
        "tool_response": json.dumps(
            {
                "rows": [["id1", "value1"], ["id2", "value2"]],
                "columns": ["id", "val"],
                "row_count": 2,
                "truncated": False,
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "rows" in text
    assert "id1" in text
    assert "value2" in text
    assert "columns" in text
    assert "id" in text


# PHK-32
def test_fmt_generic_pipeline_report_failures_visible():
    """get_pipeline_report failures must be visible through generic formatter."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_pipeline_report",
        "tool_response": json.dumps(
            {
                "total_failures": 2,
                "failures": [
                    {"step": "test", "error": "assertion failed"},
                    {"step": "build", "error": "compile error"},
                ],
            }
        ),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "failures" in text
    assert "assertion failed" in text
    assert "compile error" in text


# PHK-33
def test_fmt_generic_deeply_nested_truncated():
    """Deeply nested structures must be rendered as truncated compact JSON."""
    deep = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__some_tool",
        "tool_response": json.dumps({"info": "top", "nested": deep}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "nested" in text
    assert "deep" in text


# ---------------------------------------------------------------------------
# Production-realistic tests: Claude Code wraps MCP text in {"result": "..."}
# ---------------------------------------------------------------------------


def _wrap_for_claude_code(payload: dict) -> str:
    """Simulate Claude Code's PostToolUse wrapping of MCP text content.

    Claude Code takes the MCP text response and nests it inside
    {"result": "<json-string>"} before passing to PostToolUse hooks.
    """
    return json.dumps({"result": json.dumps(payload)})


def _wrap_plain_str_for_claude_code(text: str) -> str:
    """Simulate Claude Code's PostToolUse wrapping of a plain-text MCP response.

    Claude Code wraps the tool's return value as {"result": "<text>"}. When the
    tool returns a pre-formatted string (not JSON), this is the resulting shape.
    """
    return json.dumps({"result": text})


# PHK-34
def test_wrapped_run_cmd_success(tmp_path):
    """Wrapped run_cmd success must unwrap and show checkmark, exit_code, stdout."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": _wrap_for_claude_code(
            {"success": True, "exit_code": 0, "stdout": "file1.py\nfile2.py\n", "stderr": ""}
        ),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2713" in text
    assert "success: True" in text
    assert "exit_code: 0" in text
    assert "file1.py" in text
    assert "file2.py" in text


# PHK-35
def test_wrapped_run_cmd_failure(tmp_path):
    """Wrapped run_cmd failure must unwrap and show cross, exit_code, stderr."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": _wrap_for_claude_code(
            {"success": False, "exit_code": 127, "stdout": "", "stderr": "command not found"}
        ),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2717" in text
    assert "FAIL" in text
    assert "exit_code: 127" in text
    assert "command not found" in text


# PHK-36
def test_wrapped_run_skill_success(tmp_path):
    """Wrapped run_skill success must unwrap and show all fields."""
    payload = {
        "success": True,
        "result": "Implementation complete.",
        "session_id": "abc123",
        "subtype": "end_turn",
        "is_error": False,
        "exit_code": 0,
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "",
        "token_usage": None,
        "worktree_path": "",
    }
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "\u2713" in text
    assert "success: True" in text
    assert "session_id: abc123" in text


# PHK-37
def test_wrapped_kitchen_status(tmp_path):
    """Wrapped kitchen_status must unwrap and show status fields."""
    payload = {
        "package_version": "0.4.0",
        "plugin_json_version": "0.4.0",
        "versions_match": True,
        "tools_enabled": True,
        "token_usage_verbosity": "summary",
        "quota_guard_enabled": True,
        "github_token_configured": True,
        "github_default_repo": "acme/myrepo",
    }
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__kitchen_status",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "kitchen_status" in text
    assert "package_version" in text
    assert "tools_enabled" in text


# PHK-38
def test_wrapped_plain_text_result_passes_through(tmp_path):
    """Plain text result envelope must not crash — passes through to generic."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__open_kitchen",
        "tool_response": json.dumps(
            {"result": "Kitchen is open. AutoSkillit tools are ready for service."}
        ),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "open_kitchen" in text
    assert "Kitchen is open" in text


# PHK-50: open_kitchen combined formatter
def test_fmt_open_kitchen_combined_response():
    """open_kitchen with recipe returns combined kitchen+recipe format."""
    payload = {
        "content": "name: my-recipe\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "1.2.3",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "v1.2.3" in formatted
    assert "--- RECIPE ---" in formatted
    assert "my-recipe" in formatted


def test_fmt_open_kitchen_combined_includes_ingredients_table():
    """Combined open_kitchen response includes pre-formatted ingredients table."""
    payload = {
        "content": "name: my-recipe\nsteps: ...",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "1.0.0",
        "ingredients_table": "  Name  Description  Default\n  task  The task     (required)",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "--- INGREDIENTS TABLE" in formatted
    assert "The task" in formatted


def test_fmt_open_kitchen_combined_error():
    """Combined open_kitchen with recipe error shows error with kitchen status."""
    payload = {
        "error": "No recipe named 'bad' found",
        "kitchen": "open",
        "version": "1.0.0",
    }
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps(payload),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "No recipe named 'bad' found" in formatted
    assert "\u2717" in formatted


def test_fmt_open_kitchen_plain_text():
    """open_kitchen without recipe returns plain text format."""
    formatted = _format_response(
        "mcp__autoskillit__open_kitchen",
        json.dumps({"result": "Kitchen is open. AutoSkillit 1.2.3."}),
        pipeline=False,
    )
    assert formatted is not None
    assert "open_kitchen" in formatted
    assert "Kitchen is open" in formatted


# PHK-39
def test_wrapped_gate_error_still_detected(tmp_path):
    """Wrapped gate_error subtype must unwrap and route to gate_error formatter."""
    payload = {
        "subtype": "gate_error",
        "result": "Kitchen is closed.",
        "success": False,
        "is_error": True,
    }
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "Gate Error" in text


# PHK-40
def test_wrapped_tool_exception_still_detected(tmp_path):
    """Wrapped tool_exception subtype must unwrap and route to exception formatter."""
    payload = {
        "subtype": "tool_exception",
        "error": "TimeoutError: process hung",
        "exit_code": -1,
        "success": False,
    }
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_cmd",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, _ = _run_hook(event=event, cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "tool exception" in text.lower()
    assert "TimeoutError: process hung" in text


# T7
def test_fmt_get_token_summary_prefers_wall_clock_seconds():
    """_fmt_get_token_summary prefers wall_clock_seconds over elapsed_seconds."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "implement",
                        "input_tokens": 5000,
                        "output_tokens": 1200,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 3000,
                        "invocation_count": 2,
                        "wall_clock_seconds": 150.0,
                        "elapsed_seconds": 123.4,
                    }
                ],
                "total": {
                    "input_tokens": 5000,
                    "output_tokens": 1200,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 3000,
                    "total_elapsed_seconds": 123.4,
                },
                "mcp_responses": {"steps": [], "total": {}},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "implement" in rendered
    # Should use wall_clock_seconds (150.0), not elapsed_seconds (123.4)
    assert "t:150.0s" in rendered


# T7b
def test_fmt_get_token_summary_falls_back_to_elapsed():
    """_fmt_get_token_summary falls back to elapsed_seconds when no wall_clock."""
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {
                        "step_name": "plan",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "invocation_count": 1,
                        "elapsed_seconds": 42.5,
                    }
                ],
                "total": {"input_tokens": 100, "output_tokens": 50},
                "mcp_responses": {"steps": [], "total": {}},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "t:42.5s" in rendered


# ---------------------------------------------------------------------------
# Helper: generic event factory for new formatter tests
# ---------------------------------------------------------------------------


def _make_event(tool_name: str, payload: dict) -> dict:
    """Build a minimal PostToolUse hook event for a given tool and payload dict."""
    return {
        "tool_name": f"mcp__autoskillit__{tool_name}",
        "tool_response": json.dumps(payload),
    }


# ---------------------------------------------------------------------------
# PHK-41: Formatter coverage contract
# ---------------------------------------------------------------------------


def test_formatter_coverage_contract():
    """PHK-41: Every MCP tool is either in _FORMATTERS or explicitly in _UNFORMATTED_TOOLS.

    This prevents silent fallthrough to _fmt_generic for tools that need dedicated
    formatters, and forces an explicit choice when adding new tools.
    """
    from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
    from autoskillit.hooks.pretty_output_hook import _FORMATTERS, _UNFORMATTED_TOOLS

    all_tools = GATED_TOOLS | UNGATED_TOOLS
    covered = set(_FORMATTERS.keys()) | _UNFORMATTED_TOOLS
    uncovered = all_tools - covered
    assert uncovered == set(), (
        f"Tools have no formatter and are not in _UNFORMATTED_TOOLS: {sorted(uncovered)}. "
        "Either add a dedicated formatter or add to _UNFORMATTED_TOOLS."
    )


# ---------------------------------------------------------------------------
# PHK-42/43/44: _fmt_load_recipe tests
# ---------------------------------------------------------------------------


def test_fmt_load_recipe_suppresses_diagram():
    """Diagram is suppressed — user sees it in terminal, agent doesn't need it."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": "name: my-recipe\nsteps: ...",
                "diagram": "## my-recipe\nsome diagram graph",
                "valid": True,
                "suggestions": [],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "some diagram graph" not in formatted
    assert "--- RECIPE ---" in formatted


def test_fmt_load_recipe_includes_raw_yaml_content():
    """load_recipe response includes the raw YAML so the agent can execute steps."""
    yaml_content = "name: my-recipe\nsteps:\n  do_thing:\n    tool: run_cmd\n"
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": yaml_content,
                "diagram": "## my-recipe\nsome diagram",
                "valid": True,
                "suggestions": [],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "do_thing" in formatted
    assert "--- RECIPE ---" in formatted


def test_fmt_load_recipe_shows_finding_count():
    """Findings are summarized as a count, not individual bullets."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "content": "name: x",
                "diagram": "## x",
                "valid": False,
                "suggestions": [
                    {
                        "rule": "missing-step",
                        "message": "Step 'done' not found",
                        "severity": "error",
                    },
                    {"rule": "unknown-tool", "message": "Tool 'badtool'", "severity": "warning"},
                ],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "2 finding(s)" in formatted


# ---------------------------------------------------------------------------
# PHK-45/46: _fmt_list_recipes tests
# ---------------------------------------------------------------------------


def test_fmt_list_recipes_shows_all_names():
    """PHK-45: list_recipes response renders all recipe names — no 500-char truncation."""
    recipes = [
        {"name": f"recipe-{i:02d}", "description": f"Description for recipe {i}", "summary": "..."}
        for i in range(10)
    ]
    event = _make_event("list_recipes", {"recipes": recipes, "count": 10})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    for i in range(10):
        assert f"recipe-{i:02d}" in text, f"recipe-{i:02d} missing from output"
    assert '{"name"' not in text
    assert "10" in text


def test_fmt_list_recipes_compact_representation():
    """PHK-46: list_recipes renders one line per recipe in 'name: description' format."""
    event = _make_event(
        "list_recipes",
        {
            "recipes": [
                {
                    "name": "implementation",
                    "description": "Implement a plan in a worktree",
                    "summary": "...",
                },
                {
                    "name": "smoke-test",
                    "description": "Run a smoke-test pipeline",
                    "summary": "...",
                },
            ],
            "count": 2,
        },
    )
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "implementation" in text
    assert "Implement a plan in a worktree" in text
    assert "smoke-test" in text
    assert "Run a smoke-test pipeline" in text


# ---------------------------------------------------------------------------
# PHK-47/48: _fmt_generic list-of-dicts hardening tests
# ---------------------------------------------------------------------------


def test_fmt_generic_list_of_dicts_renders_per_item_not_blob():
    """PHK-47: Generic formatter renders list-of-dicts per item, not a truncated JSON blob."""
    failures = [
        {"step": f"step_{i}", "error": f"Error message {i}", "exit_code": 1} for i in range(10)
    ]
    event = _make_event("some_tool", {"failures": failures})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    for i in range(10):
        assert f"step_{i}" in text, f"step_{i} missing — possible truncation"
    assert "... and" not in text, "Output was truncated"


def test_fmt_generic_list_of_dicts_caps_at_20_items():
    """PHK-48: Generic formatter caps list-of-dicts at 20 items with overflow note."""
    items = [{"key": f"item-{i}", "val": f"value-{i}"} for i in range(25)]
    event = _make_event("some_tool", {"items": items})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "item-0" in text
    assert "item-19" in text
    assert "item-20" not in text
    assert "and 5 more" in text


# ---------------------------------------------------------------------------
# Output-equivalence: hook inline formatter ≡ TelemetryFormatter.format_compact_kv
# ---------------------------------------------------------------------------


def test_hook_token_summary_output_equivalent_to_canonical():
    """1g: Hook inline _fmt_get_token_summary produces identical output to
    TelemetryFormatter.format_compact_kv for the same input data."""
    from autoskillit.hooks.pretty_output_hook import _fmt_get_token_summary
    from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

    data = {
        "steps": [
            {
                "step_name": "investigate",
                "input_tokens": 7000,
                "output_tokens": 5939,
                "cache_creation_input_tokens": 8495,
                "cache_read_input_tokens": 252179,
                "invocation_count": 1,
                "wall_clock_seconds": 45.0,
                "elapsed_seconds": 40.0,
            },
            {
                "step_name": "implement",
                "input_tokens": 2031000,
                "output_tokens": 122306,
                "cache_creation_input_tokens": 280601,
                "cache_read_input_tokens": 19071323,
                "invocation_count": 3,
                "wall_clock_seconds": 492.0,
                "elapsed_seconds": 480.0,
            },
        ],
        "total": {
            "input_tokens": 2038000,
            "output_tokens": 128245,
            "cache_creation_input_tokens": 289096,
            "cache_read_input_tokens": 19323502,
            "total_elapsed_seconds": 537.0,
        },
        "mcp_responses": {
            "steps": [],
            "total": {"total_invocations": 42, "total_estimated_response_tokens": 5000},
        },
    }

    hook_output = _fmt_get_token_summary(data, _pipeline=False)
    canonical_output = TelemetryFormatter.format_compact_kv(
        data["steps"], data["total"], mcp_responses=data["mcp_responses"]
    )
    assert hook_output == canonical_output, (
        f"Hook and canonical formatter produce different output:\n"
        f"HOOK:\n{hook_output}\n\nCANONICAL:\n{canonical_output}"
    )


def test_fmt_run_skill_interactive_shows_four_token_fields():
    """_fmt_run_skill interactive mode shows all 4 token fields."""
    data = {
        "success": True,
        "subtype": "COMPLETED",
        "exit_code": 0,
        "needs_retry": False,
        "result": "done",
        "token_usage": {
            "input_tokens": 5000,
            "output_tokens": 3000,
            "cache_read_input_tokens": 200000,
            "cache_creation_input_tokens": 8000,
        },
    }
    rendered = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps({"result": json.dumps(data)}),
        pipeline=False,
    )
    assert rendered is not None
    assert "tokens_uncached:" in rendered
    assert "tokens_out:" in rendered
    assert "tokens_cache_read:" in rendered
    assert "tokens_cache_write:" in rendered


def test_fmt_run_skill_suppresses_zero_cache_fields():
    """_fmt_run_skill suppresses tokens_cache_read and tokens_cache_write when both are 0."""
    data = {
        "success": True,
        "subtype": "COMPLETED",
        "exit_code": 0,
        "needs_retry": False,
        "result": "done",
        "token_usage": {
            "input_tokens": 5000,
            "output_tokens": 3000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
    rendered = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps({"result": json.dumps(data)}),
        pipeline=False,
    )
    assert rendered is not None
    assert "tokens_uncached:" in rendered
    assert "tokens_out:" in rendered
    assert "tokens_cache_read:" not in rendered
    assert "tokens_cache_write:" not in rendered


# ---------------------------------------------------------------------------
# Timing summary dedicated formatter
# ---------------------------------------------------------------------------


def test_fmt_get_timing_summary_renders_compact():
    """get_timing_summary dedicated formatter renders compact Markdown-KV."""
    event = {
        "tool_name": "mcp__autoskillit__get_timing_summary",
        "tool_response": json.dumps(
            {
                "steps": [
                    {"step_name": "clone", "total_seconds": 4.0, "invocation_count": 1},
                    {"step_name": "implement", "total_seconds": 492.0, "invocation_count": 3},
                ],
                "total": {"total_seconds": 496.0},
            }
        ),
    }
    out, _ = _run_hook(event=event)
    data = json.loads(out)
    rendered = data["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## timing_summary" in rendered
    assert "clone x1" in rendered
    assert "implement x3" in rendered
    assert "dur:4s" in rendered
    assert "dur:8m 12s" in rendered
    assert "total:" in rendered


# Issue #346
def test_fmt_run_skill_contradictory_subtype_never_renders_fail_success():
    """Test A: full pipeline — COMPLETED+empty never renders 'FAIL [success]'.

    This captures Issue #346: when adjudication overrides the CLI's reported
    outcome (CLI says 'success', but result is empty → failure), the rendered
    status tag must reflect the adjudicated subtype, not the raw CLI value.

    Before the fix: sr.subtype = "success" → hook renders "FAIL [success]".
    After the fix:  sr.subtype = "empty_result" → hook renders "FAIL [empty_result]".
    """

    # Build a SubprocessResult that triggers the COMPLETED+empty-result path.
    # The CLI reports subtype="success" but result is empty — adjudication sees failure.
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "session_id": "s1",
        }
    )
    result = _make_result(
        returncode=0,
        stdout=stdout,
        termination_reason=TerminationReason.COMPLETED,
        channel_confirmation=ChannelConfirmation.UNMONITORED,
    )
    sr = _build_skill_result(result, completion_marker="", skill_command="/test")
    assert sr.success is False, "Precondition: this path must produce a failure"

    payload = json.loads(sr.to_json())

    # Pipeline mode: must NOT render "FAIL [success]"
    pipeline_out = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps(payload),
        pipeline=True,
    )
    assert pipeline_out is not None
    assert "FAIL [success]" not in pipeline_out, (
        f"Pipeline mode rendered contradictory 'FAIL [success]': {pipeline_out!r}"
    )
    # Must render the normalized subtype instead
    assert "FAIL [empty_result]" in pipeline_out, (
        f"Expected 'FAIL [empty_result]' in pipeline output: {pipeline_out!r}"
    )

    # Interactive mode: cross mark must not be paired with "success" as status
    interactive_out = _format_response(
        "mcp__plugin_autoskillit_autoskillit__run_skill",
        json.dumps(payload),
        pipeline=False,
    )
    assert interactive_out is not None
    cross = "\u2717"
    assert f"{cross} success" not in interactive_out, (
        f"Interactive mode rendered contradictory '{cross} success': {interactive_out!r}"
    )


# ---------------------------------------------------------------------------
# Field coverage contract: LoadRecipeResult
# ---------------------------------------------------------------------------


def test_fmt_load_recipe_field_coverage():
    """Every LoadRecipeResult field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.pretty_output_hook import (
        _FMT_LOAD_RECIPE_RENDERED,
        _FMT_LOAD_RECIPE_SUPPRESSED,
    )
    from autoskillit.recipe._api import LoadRecipeResult

    all_fields = set(LoadRecipeResult.__annotations__)
    covered = _FMT_LOAD_RECIPE_RENDERED | _FMT_LOAD_RECIPE_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"LoadRecipeResult fields have no coverage decision: {sorted(uncovered)}. "
        "Add each to _FMT_LOAD_RECIPE_RENDERED or _FMT_LOAD_RECIPE_SUPPRESSED."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_load_recipe_derivation_map_coverage():
    """Every key/value in _LOAD_RECIPE_CONTENT_DERIVED_FROM must be in _FMT_LOAD_RECIPE_RENDERED.
    Catches when a new derived field is added without declaring its source relationship."""
    from autoskillit.hooks.pretty_output_hook import (
        _FMT_LOAD_RECIPE_RENDERED,
        _LOAD_RECIPE_CONTENT_DERIVED_FROM,
    )

    for derived_field, source_field in _LOAD_RECIPE_CONTENT_DERIVED_FROM.items():
        assert derived_field in _FMT_LOAD_RECIPE_RENDERED, (
            f"Derived field '{derived_field}' must be in _FMT_LOAD_RECIPE_RENDERED. "
            f"If it was moved to SUPPRESSED, remove it from _LOAD_RECIPE_CONTENT_DERIVED_FROM."
        )
        assert source_field in _FMT_LOAD_RECIPE_RENDERED, (
            f"Source field '{source_field}' must be in _FMT_LOAD_RECIPE_RENDERED "
            f"(it is the source for derived field '{derived_field}'). "
            f"If the source is suppressed, the derivation map entry is stale."
        )


def test_fmt_load_recipe_renders_error():
    """When error is present, it appears in output with cross mark."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps({"error": "Recipe 'x' not found", "valid": False}),
        pipeline=False,
    )
    assert formatted is not None
    assert "\u2717" in formatted
    assert "Recipe 'x' not found" in formatted


def test_fmt_load_recipe_suppresses_kitchen_rules():
    """Kitchen rules are suppressed — they're in the YAML content."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "valid": True,
                "diagram": "## test diagram",
                "suggestions": [],
                "kitchen_rules": ["no raw SQL", "use run_cmd for shell"],
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "no raw SQL" not in formatted


def test_fmt_load_recipe_suppresses_greeting():
    """Greeting is suppressed — delivered via positional CLI arg instead."""
    formatted = _format_response(
        "mcp__autoskillit__load_recipe",
        json.dumps(
            {
                "valid": True,
                "diagram": "## test diagram",
                "suggestions": [],
                "greeting": "Welcome to Good Burger!",
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "Welcome to Good Burger!" not in formatted


# ---------------------------------------------------------------------------
# Field coverage contract: ListRecipesResult + RecipeListItem
# ---------------------------------------------------------------------------


def test_fmt_list_recipes_field_coverage():
    """Every ListRecipesResult field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.pretty_output_hook import (
        _FMT_LIST_RECIPES_RENDERED,
        _FMT_LIST_RECIPES_SUPPRESSED,
    )
    from autoskillit.recipe._api import ListRecipesResult

    all_fields = set(ListRecipesResult.__annotations__)
    covered = _FMT_LIST_RECIPES_RENDERED | _FMT_LIST_RECIPES_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"ListRecipesResult fields have no coverage decision: {sorted(uncovered)}. "
        "Add each to _FMT_LIST_RECIPES_RENDERED or _FMT_LIST_RECIPES_SUPPRESSED."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_recipe_list_item_field_coverage():
    """Every RecipeListItem field must be in RENDERED or SUPPRESSED."""
    from autoskillit.hooks.pretty_output_hook import (
        _FMT_RECIPE_LIST_ITEM_RENDERED,
        _FMT_RECIPE_LIST_ITEM_SUPPRESSED,
    )
    from autoskillit.recipe._api import RecipeListItem

    all_fields = set(RecipeListItem.__annotations__)
    covered = _FMT_RECIPE_LIST_ITEM_RENDERED | _FMT_RECIPE_LIST_ITEM_SUPPRESSED
    uncovered = all_fields - covered
    assert uncovered == set(), (
        f"RecipeListItem fields have no coverage decision: {sorted(uncovered)}. "
        "Add each to _FMT_RECIPE_LIST_ITEM_RENDERED or _FMT_RECIPE_LIST_ITEM_SUPPRESSED."
    )
    extra = covered - all_fields
    assert extra == set(), (
        f"Coverage registry references non-existent fields: {sorted(extra)}. Remove stale entries."
    )


def test_fmt_list_recipes_renders_summary():
    """Recipe summary appears on the line below each recipe name."""
    formatted = _format_response(
        "mcp__autoskillit__list_recipes",
        json.dumps(
            {
                "recipes": [
                    {"name": "test-recipe", "description": "A test", "summary": "step1 -> done"},
                ],
                "count": 1,
            }
        ),
        pipeline=False,
    )
    assert formatted is not None
    assert "test-recipe" in formatted
    assert "step1 -> done" in formatted


# ---------------------------------------------------------------------------
# T-1/T-2: Typed payload dispatch — plain-text envelope must pass through
# ---------------------------------------------------------------------------


def test_get_token_summary_format_table_passes_through_unmodified(tmp_path):
    """get_token_summary(format='table') returns pre-formatted markdown;
    the hook must pass it through unchanged, not eat it via the named formatter."""
    table = (
        "## Token Usage Summary\n\n| Step | input | output |\n|---|---|---|\n| impl | 45k | 12k |"
    )
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_token_summary",
        "tool_response": json.dumps({"result": table}),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## Token Usage Summary" in text
    assert "| impl |" in text
    # Must NOT produce the empty named-formatter output
    assert text.strip() != "## token_summary"


def test_get_timing_summary_format_table_passes_through_unmodified(tmp_path):
    """get_timing_summary(format='table') returns pre-formatted markdown;
    the hook must pass it through unchanged, not eat it via the named formatter."""
    table = (
        "## Step Timing Summary\n\n"
        "| Step | Duration | Invocations |\n|---|---|---|\n| clone | 4s | 1 |"
    )
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_timing_summary",
        "tool_response": json.dumps({"result": table}),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "## Step Timing Summary" in text
    assert "| clone |" in text
    assert text.strip() != "## timing_summary"


# ---------------------------------------------------------------------------
# T-3: _wrap_plain_str_for_claude_code helper shape
# ---------------------------------------------------------------------------


def test_wrap_plain_str_helper_produces_correct_shape():
    """_wrap_plain_str_for_claude_code produces the real hook event shape
    for tools returning pre-formatted strings."""
    raw = _wrap_plain_str_for_claude_code("hello world")
    parsed = json.loads(raw)
    assert parsed == {"result": "hello world"}


# ---------------------------------------------------------------------------
# T-4/T-5: _UNFORMATTED_TOOLS behavioral gate
# ---------------------------------------------------------------------------


def test_unformatted_tools_and_formatters_are_disjoint():
    """_UNFORMATTED_TOOLS and _FORMATTERS must be mutually exclusive."""
    from autoskillit.hooks.pretty_output_hook import _FORMATTERS, _UNFORMATTED_TOOLS

    overlap = set(_FORMATTERS) & _UNFORMATTED_TOOLS
    assert not overlap, f"Tools in both dispatch tables: {overlap}"


def test_unformatted_tool_routes_to_generic_not_named_formatter(tmp_path):
    """A tool in _UNFORMATTED_TOOLS must reach _fmt_generic even if
    coincidentally also added to _FORMATTERS (enforcement test)."""
    # Verify get_pipeline_report (in _UNFORMATTED_TOOLS) uses generic rendering
    payload = {"total_failures": 1, "failures": [{"step": "impl", "reason": "red"}]}
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__get_pipeline_report",
        "tool_response": _wrap_for_claude_code(payload),
    }
    out, code = _run_hook(event=event, cwd=tmp_path)
    assert code == 0
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "get_pipeline_report" in text


# ---------------------------------------------------------------------------
# Raw/derived field deduplication: ingredients_table vs content
# ---------------------------------------------------------------------------


def test_fmt_recipe_body_ingredients_not_duplicated_when_table_present():
    """When ingredients_table is present, ingredient names must not appear
    in the --- RECIPE --- section. This is the canonical test for the
    raw/derived field duplication bug."""
    from autoskillit.hooks.pretty_output_hook import _fmt_recipe_body

    data = {
        "content": REALISTIC_RECIPE_YAML,
        "ingredients_table": (
            "| Name | Description | Default |\n"
            "| task | What to implement | (required) |\n"
            "| review_approach | Run review-approach before planning | false |\n"
        ),
        "valid": True,
        "suggestions": [],
    }
    result = "\n".join(_fmt_recipe_body(data))
    assert "--- INGREDIENTS TABLE" in result, (
        "_fmt_recipe_body did not emit the INGREDIENTS TABLE header — "
        "deduplication guarantee cannot be tested."
    )
    recipe_section = result.split("--- INGREDIENTS TABLE")[0]
    # Ingredient names must appear ONLY in the table, not in the raw YAML block.
    # `task:` and `review_approach:` as YAML keys indicate the ingredients: block.
    assert "review_approach:" not in recipe_section
    assert "  task:" not in recipe_section
    # But the steps and kitchen_rules sections should remain in the RECIPE block.
    assert "implement" in recipe_section
    assert "kitchen_rules" in recipe_section
    # And the table section must contain the ingredient data.
    table_section = result.split("--- INGREDIENTS TABLE")[1]
    assert "review_approach" in table_section
    assert "task" in table_section


def test_strip_yaml_ingredients_block_removes_ingredients_section():
    from autoskillit.hooks.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\ningredients:\n  task:\n    description: a task\nsteps:\n  do: {}\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "  task:" not in result
    assert "steps:" in result
    assert "name: test" in result


def test_strip_yaml_ingredients_block_noop_when_no_ingredients_key():
    from autoskillit.hooks.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\nsteps:\n  do: {}\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert result == yaml


def test_strip_yaml_ingredients_block_at_end_of_file():
    from autoskillit.hooks.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = "name: test\nsteps:\n  do: {}\ningredients:\n  foo:\n    description: bar\n"
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "steps:" in result


def test_strip_yaml_ingredients_block_multiline_description():
    from autoskillit.hooks.pretty_output_hook import _strip_yaml_ingredients_block

    yaml = (
        "name: test\n"
        "ingredients:\n"
        "  task:\n"
        "    description: >\n"
        "      Long description\n"
        "      spanning multiple lines\n"
        "    required: true\n"
        "steps:\n"
        "  do: {}\n"
    )
    result = _strip_yaml_ingredients_block(yaml)
    assert "ingredients:" not in result
    assert "steps:" in result


def test_fmt_open_kitchen_ingredients_not_duplicated_when_table_present():
    """open_kitchen routes through _fmt_recipe_body — verify same deduplication applies."""
    from autoskillit.hooks.pretty_output_hook import _fmt_open_kitchen

    data = {
        "content": REALISTIC_RECIPE_YAML,
        "ingredients_table": "| task | What to implement | (required) |",
        "valid": True,
        "suggestions": [],
        "kitchen": "open",
        "version": "0.6.0",
    }
    # _fmt_open_kitchen returns str (not list[str]); no join needed.
    result = _fmt_open_kitchen(data, pipeline=False)
    assert "--- INGREDIENTS TABLE" in result, (
        "_fmt_open_kitchen did not emit the INGREDIENTS TABLE header — "
        "deduplication guarantee cannot be tested."
    )
    recipe_section = result.split("--- INGREDIENTS TABLE")[0]
    assert "  task:" not in recipe_section
    assert "review_approach:" not in recipe_section


def test_pretty_output_public_surface_unchanged() -> None:
    """T-5 (audit finding 8.3): the hook entrypoint and the format router are
    the only public surface; the four-way split must preserve them."""
    import autoskillit.hooks.pretty_output_hook as p

    assert callable(p.main)
    assert callable(p._format_response)
