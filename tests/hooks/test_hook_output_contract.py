"""Contract tests: no PostToolUse hook forwards raw tool_response in output."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _run_hook_script(script_name: str, stdin_data: dict) -> tuple[str, int]:
    """Run a hook script with given stdin data and return (stdout, exit_code)."""
    import importlib

    module_path = f"autoskillit.hooks.{script_name}"
    mod = importlib.import_module(module_path)

    stdin_text = json.dumps(stdin_data)
    buf = io.StringIO()
    exit_code = 0
    with patch("sys.stdin", io.StringIO(stdin_text)):
        try:
            mod.main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
    return buf.getvalue(), exit_code


def _build_posttooluse_event(
    tool_name: str = "Edit",
    file_path: str = "/tmp/test.py",
    tool_response: str = "The file was edited.",
) -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


# PostToolUse hooks that emit hookSpecificOutput with tool result replacement.
# Each tuple: (module_name, output_field, extra_env_setup_fn or None)
_POSTTOOLUSE_HOOKS_WITH_OUTPUT = [
    ("lint_after_edit_hook", "updatedToolResult", None),
    ("quota_post_hook", "updatedMCPToolOutput", None),
]


@pytest.mark.parametrize(
    ("script_name", "output_field", "extra_setup"),
    _POSTTOOLUSE_HOOKS_WITH_OUTPUT,
    ids=[h[0] for h in _POSTTOOLUSE_HOOKS_WITH_OUTPUT],
)
def test_hook_output_excludes_tool_response(
    script_name: str,
    output_field: str,
    extra_setup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PostToolUse hook may emit raw tool_response content in its output.

    A marker string placed in tool_response must not appear in any output field.
    This catches accidental forwarding of raw input data.
    """
    canary = "CANARY_ORIGINAL_FILE_CONTENT_ZZZ123"
    marker_tool_response = f"The file was edited. {canary}"

    if script_name == "lint_after_edit_hook":
        f = tmp_path / "bad_fmt.py"
        f.write_text("x=1\n")
        event = _build_posttooluse_event(tool_response=marker_tool_response)
    elif script_name == "quota_post_hook":
        event = _build_posttooluse_event(tool_response=marker_tool_response)
    else:
        event = _build_posttooluse_event(tool_response=marker_tool_response)

    if extra_setup is not None:
        extra_setup(monkeypatch)

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "implement-worktree")

    stdout, _ = _run_hook_script(script_name, event)

    if not stdout.strip():
        pytest.skip(f"{script_name} emitted no output for this event type")

    parsed = json.loads(stdout)
    hook_output = parsed.get("hookSpecificOutput", {})
    output_value = hook_output.get(output_field, "")

    assert canary not in output_value, (
        f"{script_name} forwarded raw tool_response content into {output_field}. "
        f"Hook output must contain only the hook's own generated content, "
        f"never raw input data."
    )
    assert canary not in stdout, (
        f"{script_name} emitted the canary marker anywhere in stdout. "
        f"No part of tool_response may appear in hook output."
    )
