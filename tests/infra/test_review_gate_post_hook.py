"""Tests for the review_gate_post_hook PostToolUse hook.

Captures %%REVIEW_GATE:: tags from run_skill output and tracks
check_review_loop calls from run_python.

Pattern mirrors test_quota_post_check.py.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_STATE_FILE_RELPATH = ".autoskillit/temp/review_gate_state.json"
_TOOL_RUN_SKILL = "mcp__plugin_autoskillit_autoskillit__run_skill"
_TOOL_RUN_PYTHON = "mcp__plugin_autoskillit_autoskillit__run_python"
_CALLABLE_CHECK_REVIEW_LOOP = "autoskillit.smoke_utils.check_review_loop"
_CALLABLE_OTHER = "autoskillit.smoke_utils.annotate_pr_diff"

_TAG_LOOP_REQUIRED = "%%REVIEW_GATE::LOOP_REQUIRED%%"
_TAG_CLEAR = "%%REVIEW_GATE::CLEAR%%"


def _build_run_skill_event(tool_response_text: str) -> dict:
    """Build a PostToolUse event for run_skill with given response text."""
    return {
        "tool_name": _TOOL_RUN_SKILL,
        "tool_input": {"skill_command": "/review-pr feat/1290 main", "cwd": "/tmp/work"},
        "tool_response": json.dumps({"result": json.dumps({
            "success": True,
            "result": tool_response_text,
        })}),
    }


def _build_run_python_event(callable_name: str, args: dict | None = None) -> dict:
    """Build a PostToolUse event for run_python."""
    return {
        "tool_name": _TOOL_RUN_PYTHON,
        "tool_input": {"callable": callable_name, "args": args or {}},
        "tool_response": json.dumps({"success": True, "result": "{}"}),
    }


def _run_hook(event: dict | None = None, raw_stdin: str | None = None, tmp_dir=None) -> tuple[str, int]:
    """Run review_gate_post_hook.main() and return (stdout, exit_code)."""
    from autoskillit.hooks.review_gate_post_hook import main  # noqa: PLC0415

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    buf = io.StringIO()
    exit_code = 0
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)):
            with unittest.mock.patch("pathlib.Path.cwd", return_value=tmp_dir):
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


# ---------------------------------------------------------------------------
# T2-1: LOOP_REQUIRED tag in run_skill output → state file written
# ---------------------------------------------------------------------------


def test_loop_required_tag_writes_state_file(tmp_path):
    """T2-1: State file written with gate=LOOP_REQUIRED when tag present."""
    event = _build_run_skill_event(
        f"verdict = changes_requested\n{_TAG_LOOP_REQUIRED}\n%%ORDER_UP%%"
    )
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None, "State file must be written"
    assert state["gate"] == "LOOP_REQUIRED"
    assert state["check_review_loop_called"] is False
    assert state["review_verdict"] == "changes_requested"


# ---------------------------------------------------------------------------
# T2-2: CLEAR tag in run_skill output → state file unlinked
# ---------------------------------------------------------------------------


def test_clear_tag_removes_state_file(tmp_path):
    """T2-2: State file unlinked when CLEAR tag detected."""
    event = _build_run_skill_event(
        f"verdict = approved\n{_TAG_CLEAR}\n%%ORDER_UP%%"
    )
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is None, "State file must be removed on CLEAR"


# ---------------------------------------------------------------------------
# T2-3: CLEAR tag clears existing LOOP_REQUIRED state
# ---------------------------------------------------------------------------


def test_clear_tag_removes_existing_loop_required_state(tmp_path):
    """T2-3: Transition from LOOP_REQUIRED to CLEAR removes state file."""
    # Pre-write existing state
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "gate": "LOOP_REQUIRED",
        "review_verdict": "changes_requested",
        "check_review_loop_called": False,
        "pr_number": "1290",
        "set_at": "2026-04-26T04:30:00+00:00",
    }))

    event = _build_run_skill_event(f"verdict = approved\n{_TAG_CLEAR}\n%%ORDER_UP%%")
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None


# ---------------------------------------------------------------------------
# T2-4: check_review_loop callable detected → state updated called=true
# ---------------------------------------------------------------------------


def test_check_review_loop_callable_marks_called(tmp_path):
    """T2-4: Callable detection updates check_review_loop_called=True."""
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "gate": "LOOP_REQUIRED",
        "review_verdict": "changes_requested",
        "check_review_loop_called": False,
        "pr_number": "1290",
        "set_at": "2026-04-26T04:30:00+00:00",
    }))

    event = _build_run_python_event(_CALLABLE_CHECK_REVIEW_LOOP)
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state["check_review_loop_called"] is True


# ---------------------------------------------------------------------------
# T2-5: Non-review tool call → no state file created
# ---------------------------------------------------------------------------


def test_non_review_skill_call_creates_no_state_file(tmp_path):
    """T2-5: No false positives — non-review tool call creates no state file."""
    event = _build_run_skill_event("plan_path = /tmp/plan.md\n%%ORDER_UP%%")
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None


# ---------------------------------------------------------------------------
# T2-6: Non-check_review_loop callable → no state change
# ---------------------------------------------------------------------------


def test_non_review_loop_callable_does_not_change_state(tmp_path):
    """T2-6: Callable filtering — only check_review_loop triggers state update."""
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original_state = json.dumps({
        "gate": "LOOP_REQUIRED",
        "review_verdict": "changes_requested",
        "check_review_loop_called": False,
        "pr_number": "1290",
        "set_at": "2026-04-26T04:30:00+00:00",
    })
    state_path.write_text(original_state)

    event = _build_run_python_event(_CALLABLE_OTHER)
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state["check_review_loop_called"] is False


# ---------------------------------------------------------------------------
# T2-7: Malformed stdin → exit 0, no state file
# ---------------------------------------------------------------------------


def test_malformed_stdin_exits_cleanly_and_creates_no_state_file(tmp_path):
    """T2-7: Fail-open on invalid JSON input."""
    _, exit_code = _run_hook(raw_stdin="not-json{{{", tmp_dir=tmp_path)
    assert exit_code == 0
    assert _read_state(tmp_path) is None


# ---------------------------------------------------------------------------
# T2-8: Empty tool_response → no state file change
# ---------------------------------------------------------------------------


def test_empty_tool_response_creates_no_state_file(tmp_path):
    """T2-8: Graceful handling of empty tool_response."""
    event = {
        "tool_name": _TOOL_RUN_SKILL,
        "tool_input": {"skill_command": "/review-pr feat/1290 main"},
        "tool_response": "",
    }
    _run_hook(event, tmp_dir=tmp_path)

    assert _read_state(tmp_path) is None


# ---------------------------------------------------------------------------
# T2-9: PR number extracted from run_python args
# ---------------------------------------------------------------------------


def test_pr_number_extracted_from_run_python_args(tmp_path):
    """T2-9: pr_number captured from check_review_loop args.pr_number."""
    state_path = tmp_path / _STATE_FILE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "gate": "LOOP_REQUIRED",
        "review_verdict": "changes_requested",
        "check_review_loop_called": False,
        "pr_number": "",
        "set_at": "2026-04-26T04:30:00+00:00",
    }))

    event = _build_run_python_event(
        _CALLABLE_CHECK_REVIEW_LOOP,
        args={"pr_number": 1290, "cwd": "/tmp/work"},
    )
    _run_hook(event, tmp_dir=tmp_path)

    state = _read_state(tmp_path)
    assert state is not None
    assert state["pr_number"] == "1290"
