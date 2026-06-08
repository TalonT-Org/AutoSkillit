"""Tests for recipe_confirmed_post_hook.py PostToolUse hook."""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(session_id: str, success: bool = True) -> dict:
    inner_result = json.dumps({"success": success, "result": "done"})
    outer_response = json.dumps({"result": inner_result})
    return {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_input": {"skill_command": "/some-step task"},
        "tool_response": outer_response,
        "session_id": session_id,
    }


def _run_hook(
    event: dict | None = None,
    raw_stdin: str | None = None,
    tmp_dir: Path | None = None,
    env: dict | None = None,
) -> tuple[str, int]:
    from autoskillit.hooks.recipe_confirmed_post_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    buf = io.StringIO()
    exit_code: int = 0
    patches: list = [
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)),
    ]
    if tmp_dir is not None:
        patches.append(
            unittest.mock.patch(
                "autoskillit.hooks.recipe_confirmed_post_hook.Path.cwd",
                return_value=tmp_dir,
            )
        )
    if env is not None:
        patches.append(unittest.mock.patch.dict("os.environ", env, clear=True))

    with contextlib.redirect_stdout(buf):
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            try:
                main()
            except SystemExit as exc:
                code = exc.code
                exit_code = code if isinstance(code, int) else 0

    return buf.getvalue(), exit_code


def _read_marker(tmp_dir: Path, session_id: str) -> dict | None:
    marker_path = tmp_dir / "kitchen_state" / f"{session_id}_recipe_confirmed.json"
    if not marker_path.exists():
        return None
    return json.loads(marker_path.read_text())


# T1-1: Writes marker on first successful run_skill
def test_writes_marker_on_first_successful_run_skill(tmp_path: Path) -> None:
    event = _build_event("sess-abc", success=True)
    _, exit_code = _run_hook(event, tmp_dir=tmp_path, env={"AUTOSKILLIT_STATE_DIR": str(tmp_path)})
    assert exit_code == 0
    marker = _read_marker(tmp_path, "sess-abc")
    assert marker is not None
    assert marker["session_id"] == "sess-abc"
    assert "confirmed_at" in marker
    # ISO format: has a 'T' separating date and time
    assert "T" in marker["confirmed_at"]


# T1-2: No marker on failed run_skill
def test_no_marker_on_failed_run_skill(tmp_path: Path) -> None:
    event = _build_event("sess-abc", success=False)
    _, exit_code = _run_hook(event, tmp_dir=tmp_path, env={"AUTOSKILLIT_STATE_DIR": str(tmp_path)})
    assert exit_code == 0
    assert _read_marker(tmp_path, "sess-abc") is None


# T1-3: Idempotent — pre-existing marker is not overwritten
def test_idempotent_no_double_write(tmp_path: Path) -> None:
    state_dir = tmp_path / "kitchen_state"
    state_dir.mkdir(parents=True)
    marker_path = state_dir / "sess-abc_recipe_confirmed.json"
    known_ts = "2026-01-01T00:00:00+00:00"
    marker_path.write_text(json.dumps({"session_id": "sess-abc", "confirmed_at": known_ts}))

    event = _build_event("sess-abc", success=True)
    _, exit_code = _run_hook(event, tmp_dir=tmp_path, env={"AUTOSKILLIT_STATE_DIR": str(tmp_path)})
    assert exit_code == 0
    marker = _read_marker(tmp_path, "sess-abc")
    assert marker is not None
    assert marker["confirmed_at"] == known_ts


# T1-4: No marker when session_id is missing
def test_no_marker_when_session_id_missing(tmp_path: Path) -> None:
    inner_result = json.dumps({"success": True, "result": "done"})
    outer_response = json.dumps({"result": inner_result})
    event = {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_input": {"skill_command": "/some-step task"},
        "tool_response": outer_response,
        # no session_id
    }
    _, exit_code = _run_hook(event, tmp_dir=tmp_path, env={"AUTOSKILLIT_STATE_DIR": str(tmp_path)})
    assert exit_code == 0
    state_dir = tmp_path / "kitchen_state"
    if state_dir.exists():
        # No files should have been created
        assert not any(state_dir.iterdir())


# T1-5: Malformed stdin exits cleanly
def test_malformed_stdin_exits_cleanly(tmp_path: Path) -> None:
    _, exit_code = _run_hook(
        raw_stdin="not-json{{{", tmp_dir=tmp_path, env={"AUTOSKILLIT_STATE_DIR": str(tmp_path)}
    )
    assert exit_code == 0
    state_dir = tmp_path / "kitchen_state"
    if state_dir.exists():
        assert not any(state_dir.iterdir())


# T1-6: Campaign-namespaced marker
def test_campaign_namespaced_marker(tmp_path: Path) -> None:
    event = _build_event("sess-abc", success=True)
    # Unset AUTOSKILLIT_STATE_DIR, set AUTOSKILLIT_CAMPAIGN_ID, cwd=tmp_path
    _, exit_code = _run_hook(
        event,
        tmp_dir=tmp_path,
        env={"AUTOSKILLIT_CAMPAIGN_ID": "camp-99"},
    )
    assert exit_code == 0
    marker_path = (
        tmp_path
        / ".autoskillit"
        / "temp"
        / "kitchen_state"
        / "camp-99"
        / "sess-abc_recipe_confirmed.json"
    )
    assert marker_path.exists()
