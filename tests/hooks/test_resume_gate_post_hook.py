"""Tests for the resume_gate_post_hook PostToolUse hook.

Records resume attempts to the resume_gate_state.json state file for the
reset_resume_gate PreToolUse guard to consume.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_STATE_FILE_RELPATH = ".autoskillit/temp/resume_gate_state.json"
_TOOL_DISPATCH = "mcp__plugin_autoskillit_autoskillit__dispatch_food_truck"
_TOOL_OTHER = "mcp__plugin_autoskillit_autoskillit__run_skill"


def _build_event(tool_name: str, tool_input: dict, tool_response: str = "") -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
    }


def _run_hook(
    event: dict | None = None, raw_stdin: str | None = None, tmp_dir=None
) -> tuple[str, int]:
    from autoskillit.hooks.resume_gate_post_hook import main  # noqa: PLC0415

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    buf = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with unittest.mock.patch(
                "autoskillit.hooks.resume_gate_post_hook.Path.cwd", return_value=tmp_dir
            ):
                try:
                    main()
                except SystemExit as exc:
                    exit_code = exc.code if exc.code is not None else 0

    return buf.getvalue(), exit_code


def _read_state(tmp_dir) -> dict | None:
    state_path = tmp_dir / _STATE_FILE_RELPATH
    if not state_path.exists():
        return None
    return json.loads(state_path.read_text())


# T1.1 — PostToolUse hook writes resume_attempted[dispatch_id]=true


def test_resume_with_prior_dispatch_writes_state(tmp_path: Path) -> None:
    """T1.1: State file written when resume_session_id and prior_dispatch_id both present."""
    event = _build_event(
        _TOOL_DISPATCH,
        {
            "recipe": "my-recipe",
            "task": "do stuff",
            "resume_session_id": "sess-abc",
            "prior_dispatch_id": "did-uuid-1",
        },
    )
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state.get("resume_attempted", {}).get("did-uuid-1") is True


# T1.2 — No write when resume_session_id is absent


def test_fresh_dispatch_does_not_write_state(tmp_path: Path) -> None:
    """T1.2: No state file written when resume_session_id is absent."""
    event = _build_event(
        _TOOL_DISPATCH,
        {"recipe": "my-recipe", "task": "do stuff", "prior_dispatch_id": "did-uuid-1"},
    )
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None


# T1.3 — No write when prior_dispatch_id is absent


def test_resume_without_prior_dispatch_does_not_write(tmp_path: Path) -> None:
    """T1.3: No state file written when prior_dispatch_id is absent."""
    event = _build_event(
        _TOOL_DISPATCH,
        {"recipe": "my-recipe", "task": "do stuff", "resume_session_id": "sess-abc"},
    )
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None


# T1.4 — Fail-open on malformed stdin


def test_malformed_stdin_fails_open(tmp_path: Path) -> None:
    """T1.4: Malformed JSON on stdin does not crash and writes no state."""
    _, exit_code = _run_hook(raw_stdin="not-json{{{", tmp_dir=tmp_path)
    assert exit_code == 0
    assert _read_state(tmp_path) is None


# T1.5 — Sequential writes with different dispatch IDs both succeed


def test_sequential_writes_both_succeed(tmp_path: Path) -> None:
    """T1.5: Two sequential writes to the same state file both persist under flock."""
    event1 = _build_event(
        _TOOL_DISPATCH,
        {
            "recipe": "r1",
            "task": "t1",
            "resume_session_id": "sess-1",
            "prior_dispatch_id": "did-A",
        },
    )
    _run_hook(event1, tmp_dir=tmp_path)

    event2 = _build_event(
        _TOOL_DISPATCH,
        {
            "recipe": "r2",
            "task": "t2",
            "resume_session_id": "sess-2",
            "prior_dispatch_id": "did-B",
        },
    )
    _run_hook(event2, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state["resume_attempted"]["did-A"] is True
    assert state["resume_attempted"]["did-B"] is True


# T1.6 — Existing entries preserved when new entry is added


def test_existing_entries_preserved(tmp_path: Path) -> None:
    """T1.6: New write does not clobber existing entries in the state file."""
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"resume_attempted": {"did-existing": True}, "other_key": "preserved"})
    )

    event = _build_event(
        _TOOL_DISPATCH,
        {
            "recipe": "r",
            "task": "t",
            "resume_session_id": "sess-new",
            "prior_dispatch_id": "did-new",
        },
    )
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state["resume_attempted"]["did-existing"] is True
    assert state["resume_attempted"]["did-new"] is True
    assert state["other_key"] == "preserved"


# Additional — no write for non-matching tool


def test_other_tool_does_not_write_state(tmp_path: Path) -> None:
    """PostToolUse hook ignores tools that are not dispatch_food_truck."""
    event = _build_event(
        _TOOL_OTHER,
        {
            "skill_command": "/something",
            "resume_session_id": "sess-1",
            "prior_dispatch_id": "did-1",
        },
    )
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None
