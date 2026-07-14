"""Tests for the quota_guard_state_post_hook PostToolUse hook.

The hook writes / clears a session-scoped marker file that downstream quota
hooks read to bypass enforcement for the calling session only. Two tool
names trigger state mutations:

* ``disable_quota_guard`` success → write the per-session marker.
* ``close_kitchen`` success → delete the per-session marker.

All other events (failed responses, missing session_id, unrelated tool
names, malformed payloads, atomic-write failures) leave state untouched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _run_hook(stdin_data: dict) -> tuple[str, int]:
    """Run quota_guard_state_post_hook.main() with synthetic stdin.

    Returns (stdout, exit_code). The hook always exits 0 on success and
    emits an ``updatedMCPToolOutput`` rewrite only when the marker mutation
    failed for a recognized tool event.
    """

    import autoskillit.hooks.quota_guard_state_post_hook as hook_mod

    stdin_text = json.dumps(stdin_data)
    buf = io.StringIO()
    exit_code = 0
    with patch("sys.stdin", io.StringIO(stdin_text)), patch("sys.stdout", buf):
        try:
            hook_mod.main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
    return buf.getvalue(), exit_code


def _disable_response(*, success: bool = True, content: str = "ok") -> str:
    """Build a successful / failed disable_quota_guard response envelope."""
    inner = json.dumps({"success": success, "content": content})
    return json.dumps({"result": inner})


def _close_response(*, success: bool = True, content: str = "Kitchen is closed.") -> str:
    """Build a successful / failed close_kitchen response envelope."""
    if success:
        # close_kitchen success returns a plain string, NOT a JSON envelope.
        return content
    inner = json.dumps({"success": False, "error": content})
    return json.dumps({"result": inner})


def _kitchen_state(tmp_path: Path) -> Path:
    return tmp_path / "kitchen_state"


# T1: successful disable_quota_guard writes the marker for the event session_id
def test_disable_writes_marker_for_event_session(tmp_path, monkeypatch):
    """Successful disable_quota_guard response writes the marker under the event session_id."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    event = {
        "session_id": "session-aaa",
        "tool_name": "disable_quota_guard",
        "tool_response": _disable_response(success=True),
    }
    _run_hook(event)
    marker = _kitchen_state(tmp_path) / "session-aaa_quota_guard_disabled.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["session_id"] == "session-aaa"
    assert "disabled_at" in payload
    assert payload["marker_version"] == 1


# T2: failed disable response writes nothing
def test_disable_failed_response_writes_no_marker(tmp_path, monkeypatch):
    """A failed disable_quota_guard response must NOT write a marker."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    event = {
        "session_id": "session-aaa",
        "tool_name": "disable_quota_guard",
        "tool_response": _disable_response(success=False),
    }
    _run_hook(event)
    marker = _kitchen_state(tmp_path) / "session-aaa_quota_guard_disabled.json"
    assert not marker.exists()


# T3: malformed response writes nothing
def test_disable_malformed_response_writes_no_marker(tmp_path, monkeypatch):
    """A malformed disable_quota_guard response must NOT write a marker."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    event = {
        "session_id": "session-aaa",
        "tool_name": "disable_quota_guard",
        "tool_response": "not-json-garbage",
    }
    _run_hook(event)
    marker = _kitchen_state(tmp_path) / "session-aaa_quota_guard_disabled.json"
    assert not marker.exists()


# T4: missing session_id writes nothing
def test_disable_missing_session_id_writes_no_marker(tmp_path, monkeypatch):
    """An empty / missing session_id must NOT produce a marker."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    event = {
        "session_id": "",
        "tool_name": "disable_quota_guard",
        "tool_response": _disable_response(success=True),
    }
    _run_hook(event)
    assert not _kitchen_state(tmp_path).exists() or not list(
        _kitchen_state(tmp_path).glob("*.json")
    )


# T5: unrelated tool name writes nothing
def test_unrelated_tool_writes_no_marker(tmp_path, monkeypatch):
    """Tools other than disable_quota_guard / close_kitchen must NOT write markers."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    event = {
        "session_id": "session-aaa",
        "tool_name": "run_skill",
        "tool_response": _disable_response(success=True),
    }
    _run_hook(event)
    assert not (_kitchen_state(tmp_path) / "session-aaa_quota_guard_disabled.json").exists()


# T6: successful close_kitchen removes the marker for that session only
def test_close_kitchen_success_clears_matching_marker(tmp_path, monkeypatch):
    """Successful close_kitchen removes only the matching session's marker."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = _kitchen_state(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "session-aaa_quota_guard_disabled.json"
    other = state_dir / "session-bbb_quota_guard_disabled.json"
    target.write_text(
        json.dumps(
            {
                "session_id": "session-aaa",
                "disabled_at": "2026-01-01T00:00:00+00:00",
                "marker_version": 1,
            }
        )
    )
    other.write_text(
        json.dumps(
            {
                "session_id": "session-bbb",
                "disabled_at": "2026-01-01T00:00:00+00:00",
                "marker_version": 1,
            }
        )
    )

    event = {
        "session_id": "session-aaa",
        "tool_name": "close_kitchen",
        "tool_response": _close_response(success=True),
    }
    _run_hook(event)

    assert not target.exists(), "close_kitchen must remove the matching marker"
    assert other.exists(), "close_kitchen must NOT remove a foreign session's marker"


# T7: failed close leaves the marker intact
def test_close_kitchen_failed_response_leaves_marker(tmp_path, monkeypatch):
    """A failed close_kitchen response must NOT clear the marker."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    state_dir = _kitchen_state(tmp_path)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "session-aaa_quota_guard_disabled.json"
    target.write_text(
        json.dumps(
            {
                "session_id": "session-aaa",
                "disabled_at": "2026-01-01T00:00:00+00:00",
                "marker_version": 1,
            }
        )
    )

    event = {
        "session_id": "session-aaa",
        "tool_name": "close_kitchen",
        "tool_response": _close_response(success=False),
    }
    _run_hook(event)
    assert target.exists()


# T8: two session IDs sharing one project root remain independent
def test_two_sessions_share_one_project_root(tmp_path, monkeypatch):
    """Two distinct session IDs sharing a project root write distinct markers."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    _run_hook(
        {
            "session_id": "session-aaa",
            "tool_name": "disable_quota_guard",
            "tool_response": _disable_response(success=True),
        }
    )
    _run_hook(
        {
            "session_id": "session-bbb",
            "tool_name": "disable_quota_guard",
            "tool_response": _disable_response(success=True),
        }
    )

    state_dir = _kitchen_state(tmp_path)
    assert (state_dir / "session-aaa_quota_guard_disabled.json").exists()
    assert (state_dir / "session-bbb_quota_guard_disabled.json").exists()

    payload_a = json.loads((state_dir / "session-aaa_quota_guard_disabled.json").read_text())
    payload_b = json.loads((state_dir / "session-bbb_quota_guard_disabled.json").read_text())
    assert payload_a["session_id"] == "session-aaa"
    assert payload_b["session_id"] == "session-bbb"


# T9: atomic-write failure surfaces via updatedMCPToolOutput, never a partial marker
def test_atomic_write_failure_surfaces_diagnostic_and_leaves_no_marker(
    tmp_path, monkeypatch, capsys
):
    """If atomic marker write fails, hook emits a diagnostic and never produces a partial marker.

    The hook must NOT echo the raw tool_response; it must rewrite only its own diagnostic.
    """
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))

    def _raise(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "autoskillit.hooks.quota_guard_state_post_hook.write_quota_disable_marker", _raise
    )

    event = {
        "session_id": "session-aaa",
        "tool_name": "disable_quota_guard",
        "tool_response": _disable_response(success=True),
    }
    out, exit_code = _run_hook(event)
    assert exit_code == 0

    state_dir = _kitchen_state(tmp_path)
    marker = state_dir / "session-aaa_quota_guard_disabled.json"
    assert not marker.exists(), "No partial marker must remain after a failed write"

    # Failure must surface as updatedMCPToolOutput rewrite so the caller sees the failure.
    parsed = json.loads(out)
    assert "hookSpecificOutput" in parsed
    rewrite = parsed["hookSpecificOutput"].get("updatedMCPToolOutput", "")
    assert rewrite, "Failure must surface via updatedMCPToolOutput"
    # Must not echo raw tool_response content
    assert '"content"' not in rewrite
    assert '"result"' not in rewrite


# T10: malformed event JSON exits silently with no marker
def test_malformed_event_exits_silently(tmp_path, monkeypatch):
    """A non-JSON event payload exits 0 with no state change."""
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    buf = io.StringIO()
    exit_code = 0
    with patch("sys.stdin", io.StringIO("not-json-{{{")), patch("sys.stdout", buf):
        try:
            import autoskillit.hooks.quota_guard_state_post_hook as hook_mod

            hook_mod.main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
    assert exit_code == 0
    assert buf.getvalue() == ""
    assert not _kitchen_state(tmp_path).exists()
