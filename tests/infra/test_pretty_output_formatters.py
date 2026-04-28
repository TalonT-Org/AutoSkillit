"""Tests: pretty_output per-tool named formatters."""

from __future__ import annotations

import json

import pytest

from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from tests.infra._pretty_output_helpers import _make_run_skill_event, _run_hook

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


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
        "tool_response": json.dumps({"passed": True, "stdout": "...245 passed..."}),
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
        "tool_response": json.dumps({"passed": False, "stdout": pytest_output}),
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
    config_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"quota_guard": {}}')

    event = _make_run_skill_event(success=False, needs_retry=True, retry_reason="budget_exhausted")

    out, _ = _run_hook(event=event, cwd=tmp_path)

    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "run_skill:" in text
    assert "FAIL" in text
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
    config_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"quota_guard": {}}')

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


# PHK-kill-reason: formatter branches on kill_reason (1e)


def test_fmt_run_skill_success_with_kill_after_completion_annotates_exit_code() -> None:
    """kill_reason=kill_after_completion must annotate exit_code line."""
    from autoskillit.hooks._fmt_execution import _fmt_run_skill

    data = {
        "success": True,
        "result": "Implementation complete.",
        "session_id": "abc123",
        "subtype": "end_turn",
        "is_error": False,
        "exit_code": -9,
        "kill_reason": "kill_after_completion",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "",
    }
    text = _fmt_run_skill(data, pipeline=False)
    assert "exit_code: -9 (infra-terminated after completion" in text, (
        f"Expected kill_after_completion annotation in exit_code line, got: {text!r}"
    )


def test_fmt_run_skill_success_with_natural_exit_shows_bare_exit_code() -> None:
    """kill_reason=natural_exit must render bare exit_code without annotation."""
    from autoskillit.hooks._fmt_execution import _fmt_run_skill

    data = {
        "success": True,
        "result": "Done.",
        "session_id": "abc123",
        "subtype": "end_turn",
        "is_error": False,
        "exit_code": 0,
        "kill_reason": "natural_exit",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "",
    }
    text = _fmt_run_skill(data, pipeline=False)
    assert "exit_code: 0" in text
    assert "infra-terminated" not in text
    assert "infra-killed" not in text


def test_fmt_run_skill_infra_kill_annotates_reason() -> None:
    """kill_reason=infra_kill must annotate exit_code with infra-killed."""
    from autoskillit.hooks._fmt_execution import _fmt_run_skill

    data = {
        "success": False,
        "result": "",
        "session_id": "abc123",
        "subtype": "timeout",
        "is_error": True,
        "exit_code": -9,
        "kill_reason": "infra_kill",
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "",
    }
    text = _fmt_run_skill(data, pipeline=False)
    assert "infra-killed" in text, (
        f"Expected 'infra-killed' annotation for infra_kill reason, got: {text!r}"
    )


def test_fmt_run_skill_legacy_payload_without_kill_reason_renders_bare() -> None:
    """Payload without kill_reason field must render bare exit_code (backward compat)."""
    from autoskillit.hooks._fmt_execution import _fmt_run_skill

    data = {
        "success": True,
        "result": "Done.",
        "session_id": "abc123",
        "subtype": "end_turn",
        "is_error": False,
        "exit_code": 0,
        "needs_retry": False,
        "retry_reason": "none",
        "stderr": "",
    }
    text = _fmt_run_skill(data, pipeline=False)
    assert "exit_code: 0" in text
    assert "infra" not in text


def test_fmt_test_check_displays_duration():
    """Pretty output shows duration when present."""
    from autoskillit.hooks._fmt_execution import _fmt_test_check

    data = {"passed": True, "stdout": "= 10 passed =", "duration_seconds": 12.34}
    out = _fmt_test_check(data, False)
    assert "12.3s" in out or "12.34" in out


def test_fmt_test_check_displays_filter_stats():
    """Pretty output shows filter stats when present."""
    from autoskillit.hooks._fmt_execution import _fmt_test_check

    data = {
        "passed": True,
        "stdout": "",
        "filter_mode": "conservative",
        "tests_selected": 50,
        "tests_deselected": 100,
    }
    out = _fmt_test_check(data, False)
    assert "conservative" in out
    assert "50" in out
    assert "100" in out
