"""Tests for generated_file_write_guard.py PreToolUse hook."""

import json
import subprocess
import sys

from autoskillit.core.paths import pkg_root


def _run_guard(event: dict) -> dict | None:
    script = pkg_root() / "hooks" / "guards" / "generated_file_write_guard.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    if result.stdout.strip():
        return json.loads(result.stdout.strip())
    return None


def test_write_guard_denies_hooks_json_write():
    event = {"tool_name": "Write", "tool_input": {"file_path": "/any/path/hooks/hooks.json"}}
    output = _run_guard(event)
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_guard_denies_settings_json_write():
    event = {"tool_name": "Write", "tool_input": {"file_path": "/repo/.claude/settings.json"}}
    output = _run_guard(event)
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_guard_denies_edit_targeting_hooks_json():
    event = {"tool_name": "Edit", "tool_input": {"file_path": "/any/path/hooks/hooks.json"}}
    output = _run_guard(event)
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_write_guard_allows_unrelated_hooks_json():
    """Ensure files like 'my-hooks.json' are not false-positive denied."""
    event = {"tool_name": "Write", "tool_input": {"file_path": "/repo/my-hooks.json"}}
    output = _run_guard(event)
    assert output is None, "Unrelated *hooks.json file must not be denied"


def test_write_guard_allows_other_files():
    event = {"tool_name": "Write", "tool_input": {"file_path": "/repo/src/foo.py"}}
    output = _run_guard(event)
    assert output is None, "Non-generated-file Write must pass through (no stdout)"


def test_write_guard_fail_open_on_invalid_json():
    script = pkg_root() / "hooks" / "guards" / "generated_file_write_guard.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", "Fail-open: invalid stdin must produce no output"
