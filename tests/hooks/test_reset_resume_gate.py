"""Tests for the reset_resume_gate PreToolUse guard.

Denies reset_dispatch unless a resume attempt was previously recorded
by resume_gate_post_hook, or the caller passes force=True.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_STATE_FILE_RELPATH = ".autoskillit/temp/resume_gate_state.json"
_DISPATCHES_DIR_RELPATH = ".autoskillit/temp/dispatches"
_TOOL_RESET = "mcp__plugin_autoskillit_autoskillit__reset_dispatch"
_TOOL_OTHER = "mcp__plugin_autoskillit_autoskillit__run_skill"
_DISPATCH_UUID = "abc-dispatch-uuid-1"
_DISPATCH_NAME = "my-dispatch"


def _write_state(tmp_dir: Path, resume_attempted: dict[str, bool]) -> None:
    state_path = tmp_dir / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"resume_attempted": resume_attempted}))


def _write_campaign_state(
    tmp_dir: Path,
    dispatches: list[dict],
    campaign_id: str = "cmp-1",
) -> Path:
    dispatches_dir = tmp_dir / _DISPATCHES_DIR_RELPATH
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    state_file = dispatches_dir / f"{_DISPATCH_UUID}.json"
    state_file.write_text(
        json.dumps(
            {
                "campaign_id": campaign_id,
                "campaign_name": "test",
                "manifest_path": "/m.yaml",
                "started_at": 1.0,
                "dispatches": dispatches,
            }
        )
    )
    return state_file


def _run_guard(
    tool_name: str = _TOOL_RESET,
    tool_input: dict | None = None,
    tmp_dir: Path | None = None,
    raw_stdin: str | None = None,
) -> str:
    from autoskillit.hooks.guards.reset_resume_gate import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        stdin_content = json.dumps({"tool_name": tool_name, "tool_input": tool_input or {}})

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
            with unittest.mock.patch(
                "autoskillit.hooks.guards.reset_resume_gate.Path.cwd", return_value=tmp_dir
            ):
                try:
                    main()
                except SystemExit as exc:
                    assert exc.code == 0, f"Guard exited non-zero: {exc.code!r}"
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# T2.1 — Guard allows when resume was attempted (direct UUID match)


def test_allows_when_resume_attempted_for_uuid(tmp_path: Path) -> None:
    """T2.1: Allow when resume_attempted[dispatch_id] is true."""
    _write_state(tmp_path, {_DISPATCH_UUID: True})
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.2 — Guard denies when no resume was attempted


def test_denies_when_no_resume_attempted(tmp_path: Path) -> None:
    """T2.2: Deny when no resume was recorded for the dispatch_id."""
    _write_state(tmp_path, {"some-other-id": True})
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert _is_denied(out)


# T2.3 — Guard allows when force=True, even without resume


def test_allows_when_force_true(tmp_path: Path) -> None:
    """T2.3: Allow when force is truthy, even without a resume attempt recorded."""
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID, "force": True}, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.4 — Fail-open when state file does not exist


def test_fails_open_when_no_state_file(tmp_path: Path) -> None:
    """T2.4: Fail-open (allow) when state file does not exist."""
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.5 — Fail-open when state file is corrupted JSON


def test_fails_open_on_corrupt_state(tmp_path: Path) -> None:
    """T2.5: Fail-open (allow) when state file is corrupted JSON."""
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not-json{{{")
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.6 — Fail-open on malformed stdin


def test_fails_open_on_malformed_stdin(tmp_path: Path) -> None:
    """T2.6: Fail-open (allow) when stdin is not valid JSON."""
    _write_state(tmp_path, {_DISPATCH_UUID: True})
    out = _run_guard(tmp_dir=tmp_path, raw_stdin="not-json{{{")
    assert out.strip() == ""


# T2.7 — Allow when tool name does not match reset_dispatch


def test_allows_when_tool_name_does_not_match(tmp_path: Path) -> None:
    """T2.7: Allow when tool name is not reset_dispatch."""
    _write_state(tmp_path, {})  # state exists, no resume → would normally deny
    out = _run_guard(tool_name=_TOOL_OTHER, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.8 — Deny reason contains RESET_RESUME_DENY_TRIGGER constant


def test_deny_reason_contains_trigger(tmp_path: Path) -> None:
    """T2.8: Deny reason string contains the RESET_RESUME_DENY_TRIGGER constant."""
    from autoskillit.hooks.guards.reset_resume_gate import RESET_RESUME_DENY_TRIGGER

    _write_state(tmp_path, {"some-other-id": True})
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert _is_denied(out)
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert RESET_RESUME_DENY_TRIGGER in reason


# T2.9 — Name→UUID resolution: allow when name resolves to a UUID with a recorded resume


def test_allows_when_name_resolves_to_uuid_with_resume(tmp_path: Path) -> None:
    """T2.9: Allow when dispatch_id is a name that resolves to a UUID with a recorded resume."""
    _write_campaign_state(
        tmp_path,
        [{"name": _DISPATCH_NAME, "dispatch_id": _DISPATCH_UUID, "status": "FAILURE"}],
    )
    _write_state(tmp_path, {_DISPATCH_UUID: True})
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_NAME}, tmp_dir=tmp_path)
    assert out.strip() == ""


# T2.10 — REFUSED dispatches can be reset without a resume attempt


def test_allows_when_refused_status(tmp_path: Path) -> None:
    """T2.10: Allow reset for REFUSED dispatches without a resume attempt recorded."""
    _write_campaign_state(
        tmp_path,
        [{"name": _DISPATCH_NAME, "dispatch_id": _DISPATCH_UUID, "status": "REFUSED"}],
    )
    out = _run_guard(tool_input={"dispatch_id": _DISPATCH_UUID}, tmp_dir=tmp_path)
    assert out.strip() == ""


# Additional — empty dispatch_id is allowed (fail-open, edge case)


def test_allows_when_no_dispatch_id(tmp_path: Path) -> None:
    """Empty/missing dispatch_id is allowed (fail-open on invalid input)."""
    out = _run_guard(tool_input={}, tmp_dir=tmp_path)
    assert out.strip() == ""
