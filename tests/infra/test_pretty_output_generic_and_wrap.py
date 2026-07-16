"""Tests: pretty_output generic formatter and Claude Code double-wrap."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.infra._pretty_output_helpers import (
    _run_hook,
    _wrap_for_claude_code,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def _valid_spill_metadata(project_root: Path, artifact_text: str) -> dict[str, object]:
    artifact = project_root / ".autoskillit" / "temp" / "responses" / "full.log"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(artifact_text, encoding="utf-8")
    encoded = artifact_text.encode("utf-8")
    return {
        "schema_version": 1,
        "artifact_path": str(artifact.resolve()),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "original_utf8_bytes": len(encoded),
        "projected_utf8_bytes": 4096,
        "omitted_chars": 5000,
        "omitted_items": 5,
        "reason": "oversized_values",
    }


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
def test_fmt_generic_deeply_nested_lossless():
    """Deeply nested structures must be rendered as complete compact JSON."""
    deep = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__some_tool",
        "tool_response": json.dumps({"info": "top", "nested": deep}),
    }
    out, _ = _run_hook(event=event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "nested" in text
    assert "deep" in text


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


# PHK-47/48: _fmt_generic list-of-dicts hardening tests


def test_fmt_generic_list_of_dicts_renders_per_item_not_blob():
    """PHK-47: Generic formatter renders list-of-dicts per item, not a truncated JSON blob."""
    from tests.infra._pretty_output_helpers import _make_event

    failures = [
        {"step": f"step_{i}", "error": f"Error message {i}", "exit_code": 1} for i in range(10)
    ]
    event = _make_event("some_tool", {"failures": failures})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    for i in range(10):
        assert f"step_{i}" in text, f"step_{i} missing — possible truncation"
    assert "... and" not in text, "Output was truncated"


def test_fmt_generic_list_of_dicts_renders_all_items_without_artifact():
    """Generic rendering cannot omit list items without a recovery artifact."""
    from tests.infra._pretty_output_helpers import _make_event

    items = [{"key": f"item-{i}", "val": f"value-{i}"} for i in range(25)]
    event = _make_event("some_tool", {"items": items})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "item-0" in text
    assert "item-19" in text
    assert "item-20" in text
    assert "item-24" in text
    assert "and 5 more" not in text


# PHK-49: list-of-dicts must render all fields, not just first 2
def test_fmt_generic_list_of_dicts_renders_all_fields():
    """_fmt_generic must render all dict fields, not just the first 2."""
    from tests.infra._pretty_output_helpers import _make_event

    failures = [
        {
            "timestamp": "2026-06-09T12:00:00Z",
            "skill_command": "/dispatch",
            "exit_code": 1,
            "subtype": "tool_exception",
            "needs_retry": True,
            "retry_reason": "timeout",
            "stderr": "Connection refused",
        }
    ]
    event = _make_event("get_pipeline_report", {"failures": failures})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "timestamp" in text
    assert "skill_command" in text
    assert "exit_code" in text
    assert "subtype" in text
    assert "needs_retry" in text
    assert "retry_reason" in text
    assert "stderr" in text


# PHK-50: non-string list items must include ellipsis when truncated
def test_fmt_generic_non_string_list_items_are_lossless_without_artifact():
    """Nested JSON cannot be clipped unless a complete artifact is available."""
    from tests.infra._pretty_output_helpers import _make_event

    big_nested = list(range(2000))
    event = _make_event("some_tool", {"items": [big_nested]})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "1999" in text
    assert "..." not in text


# PHK-51: nested dict/list values render in full when under the raised limit
def test_fmt_generic_nested_dict_value_renders_in_full():
    """_fmt_generic must render nested dict values fully when under the configurable limit."""
    from tests.infra._pretty_output_helpers import _make_event

    nested = {f"key_{i}": f"value_{i}" for i in range(20)}
    event = _make_event("some_tool", {"metadata": nested})
    out, _ = _run_hook(event)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]
    for i in range(20):
        assert f"key_{i}" in text, f"key_{i} missing from nested dict output"


def test_fmt_generic_unspilled_path_crosses_all_former_clip_boundaries():
    """Unspilled generic output retains long strings, all items, and nested JSON."""
    from tests.infra._pretty_output_helpers import _make_event

    long_value = "LONG-HEAD::" + ("x" * 2500) + "::LONG-TAIL"
    payload = {
        "strings": [f"string-item-{i}" for i in range(25)],
        "dict_items": [{"key": f"dict-item-{i}", "value": long_value} for i in range(25)],
        "nested": {"long": long_value, "numbers": list(range(600))},
    }
    out, _ = _run_hook(_make_event("some_tool", payload))
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]

    assert "string-item-24" in text
    assert "dict-item-24" in text
    assert "::LONG-TAIL" in text
    assert "599" in text
    assert "... and" not in text
    assert "..." not in text


def test_fmt_generic_valid_artifact_allows_bounded_projection(tmp_path: Path):
    """Trusted spill metadata permits reduction because the notice routes to full data."""
    from tests.infra._pretty_output_helpers import _make_event

    metadata = _valid_spill_metadata(tmp_path, "complete authoritative response")
    payload = {
        "items": [f"artifact-item-{i}" for i in range(25)],
        "_autoskillit_response_spill": metadata,
    }
    out, _ = _run_hook(_make_event("some_tool", payload), cwd=tmp_path)
    text = json.loads(out)["hookSpecificOutput"]["updatedMCPToolOutput"]

    assert "artifact-item-19" in text
    assert "artifact-item-20" not in text
    assert "... and 5 more" in text
    assert text.count("response_spilled:") == 1
    assert text.count(f"artifact_path: {metadata['artifact_path']}") == 1
