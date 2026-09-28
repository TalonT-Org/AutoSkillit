"""Contract tests: no PostToolUse hook forwards raw tool_response in output."""

from __future__ import annotations

import io
import json
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._hook_channel_scan import all_registered_hook_defs, scan_script_channels

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


def _posttooluse_hooks_with_output() -> list[tuple[str, str]]:
    output: set[tuple[str, str]] = set()
    for hook_def in all_registered_hook_defs():
        if hook_def.event_type != "PostToolUse":
            continue
        for script in hook_def.scripts:
            channels = scan_script_channels(script)
            if "context" in channels:
                output.add((script, "additionalContext"))
            if "rewrite_mcp_output" in channels:
                output.add((script, "updatedMCPToolOutput"))
    return sorted(output)


def _build_posttooluse_event(
    tool_name: str = "Edit",
    file_path: str = "/tmp/test.py",
    tool_response: str = "The file was edited.",
    session_id: str = "session-aaa",
) -> dict:
    return {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


# Each tuple is derived from registered PostToolUse scripts and their scanned channels.
_POSTTOOLUSE_HOOKS_WITH_OUTPUT = _posttooluse_hooks_with_output()


def test_hook_output_excludes_tool_response(tmp_path: Path) -> None:
    """No PostToolUse hook may emit raw tool_response content in its output.

    A marker string placed in tool_response must not appear in any output field.
    This catches accidental forwarding of raw input data.
    """
    assert _POSTTOOLUSE_HOOKS_WITH_OUTPUT, (
        "No registered PostToolUse script was found through the output channel scanner."
    )
    from tests._hook_protocol_oracle import run_hook

    canary = "CANARY_ORIGINAL_FILE_CONTENT_ZZZ123"
    marker_tool_response = f"The file was edited. {canary}"
    outputs_checked = 0
    for script_rel, output_field in _POSTTOOLUSE_HOOKS_WITH_OUTPUT:
        script_name = Path(script_rel).stem
        if script_name == "lint_after_edit_hook":
            f = tmp_path / "bad_fmt.py"
            f.write_text("x=1\n")
            event = _build_posttooluse_event(file_path=str(f), tool_response=marker_tool_response)
        else:
            event = _build_posttooluse_event(tool_response=marker_tool_response)

        emission = run_hook(
            script_rel,
            event,
            env={"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SKILL_NAME": "implement-worktree"},
        )
        stdout = emission.stdout
        if not stdout.strip():
            continue

        parsed = json.loads(stdout)
        hook_output = parsed.get("hookSpecificOutput", {})
        output_value = hook_output.get(output_field, "")
        outputs_checked += 1

        assert canary not in output_value, (
            f"{script_name} forwarded raw tool_response content into {output_field}. "
            "Hook output must contain only the hook's own generated content, "
            "never raw input data."
        )
        assert canary not in stdout, (
            f"{script_name} emitted the canary marker anywhere in stdout. "
            "No part of tool_response may appear in hook output."
        )

    assert outputs_checked, (
        "No registered PostToolUse emitter produced output for the canary event."
    )


def test_quota_guard_state_post_hook_failure_rewrite_excludes_raw_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker-failure rewrite from the quota_guard_state_post_hook must not echo tool_response.

    The hook emits ``updatedMCPToolOutput`` only when atomic marker mutation fails.
    The rewrite must contain only the hook's diagnostic, never the raw
    ``tool_response`` payload (success envelope / outer wrapper / content field).
    """
    hook_mod = import_module("autoskillit.hooks.quota_guard_state_post_hook")

    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))

    def _raise(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(hook_mod, "write_quota_disable_marker", _raise)

    canary = "CANARY_DISABLE_RESPONSE_PAYLOAD_ZZZ456"
    inner = json.dumps({"success": True, "content": canary})
    tool_response = json.dumps({"result": inner})

    event = {
        "session_id": "session-aaa",
        "tool_name": "disable_quota_guard",
        "tool_response": tool_response,
    }

    stdin_text = json.dumps(event)
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_text)), patch("sys.stdout", buf):
        try:
            hook_mod.main()
        except SystemExit:
            pass

    stdout = buf.getvalue()
    if not stdout.strip():
        pytest.skip("quota_guard_state_post_hook produced no output (atomic write did not fail)")

    parsed = json.loads(stdout)
    context = parsed["hookSpecificOutput"]["additionalContext"]
    assert canary not in context, (
        "quota_guard_state_post_hook must not echo the raw tool_response "
        "content into its failure context."
    )
    assert '"result"' not in context
    assert '"content"' not in context
