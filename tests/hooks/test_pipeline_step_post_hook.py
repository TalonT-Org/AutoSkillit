"""Tests for pipeline_step_post_hook.py PostToolUse hook."""

from __future__ import annotations

import contextlib
import io
import json
import unittest.mock

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_TRACKER_RELPATH = ".autoskillit/temp/pipeline_tracker"


def _build_event(step_name: str, order_id: str, success: bool = True) -> dict:
    inner_result = json.dumps({"success": success, "result": "done"})
    outer_response = json.dumps({"result": inner_result})
    return {
        "tool_name": "mcp__plugin_autoskillit_autoskillit__run_skill",
        "tool_input": {
            "skill_command": f"/{step_name} task",
            "cwd": "/tmp/work",
            "step_name": step_name,
            "order_id": order_id,
        },
        "tool_response": outer_response,
    }


def _run_hook(event: dict | None = None, raw_stdin: str | None = None, tmp_dir=None, env=None):
    from autoskillit.hooks.pipeline_step_post_hook import main

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event or {})

    buf = io.StringIO()
    exit_code = 0
    patches = [
        unittest.mock.patch("sys.stdin", io.StringIO(stdin_text)),
        unittest.mock.patch(
            "autoskillit.hooks.pipeline_step_post_hook.Path.cwd", return_value=tmp_dir
        ),
    ]
    if env:
        patches.append(unittest.mock.patch.dict("os.environ", env))

    with contextlib.redirect_stdout(buf):
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            try:
                main()
            except SystemExit as exc:
                exit_code = exc.code if exc.code is not None else 0

    return buf.getvalue(), exit_code


def _write_tracker(tmp_path, order_id, steps, dependencies=None):
    tracker_dir = tmp_path / _TRACKER_RELPATH
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{order_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": order_id,
                "kitchen_id": "test-kitchen",
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies or {},
            }
        )
    )


def _read_tracker(tmp_path, order_id):
    return json.loads((tmp_path / _TRACKER_RELPATH / f"{order_id}.json").read_text())


class TestPipelineStepPostHook:
    def test_marks_step_complete_on_success(self, tmp_path):
        _write_tracker(
            tmp_path, "AB", {"review": {"status": "pending"}, "implement": {"status": "pending"}}
        )
        event = _build_event("review", "AB", success=True)
        _run_hook(event, tmp_dir=tmp_path)

        tracker = _read_tracker(tmp_path, "AB")
        assert tracker["steps"]["review"]["status"] == "complete"
        assert "completed_at" in tracker["steps"]["review"]

    def test_does_not_mark_on_failure(self, tmp_path):
        _write_tracker(tmp_path, "AB", {"review": {"status": "pending"}})
        event = _build_event("review", "AB", success=False)
        _run_hook(event, tmp_dir=tmp_path)

        tracker = _read_tracker(tmp_path, "AB")
        assert tracker["steps"]["review"]["status"] == "pending"

    def test_appends_progress_banner(self, tmp_path):
        _write_tracker(
            tmp_path, "AB", {"review": {"status": "pending"}, "implement": {"status": "pending"}}
        )
        event = _build_event("review", "AB", success=True)
        stdout, _ = _run_hook(event, tmp_dir=tmp_path)

        output = json.loads(stdout)
        banner = output["hookSpecificOutput"]["updatedMCPToolOutput"]
        assert "Pipeline Tracker" in banner
        assert "review" in banner
        assert "1/2" in banner

    def test_no_tracker_file_exits_cleanly(self, tmp_path):
        event = _build_event("review", "AB", success=True)
        stdout, exit_code = _run_hook(event, tmp_dir=tmp_path)
        assert exit_code == 0
        assert stdout.strip() == ""

    def test_empty_step_name_exits_cleanly(self, tmp_path):
        _write_tracker(tmp_path, "AB", {"review": {"status": "pending"}})
        event = _build_event("", "AB", success=True)
        event["tool_input"]["step_name"] = ""
        stdout, exit_code = _run_hook(event, tmp_dir=tmp_path)
        assert exit_code == 0
        assert stdout.strip() == ""

    def test_malformed_stdin_exits_cleanly(self, tmp_path):
        stdout, exit_code = _run_hook(raw_stdin="not-json{{{", tmp_dir=tmp_path)
        assert exit_code == 0
        assert stdout.strip() == ""

    def test_order_id_env_fallback(self, tmp_path):
        _write_tracker(tmp_path, "AB", {"review": {"status": "pending"}})
        event = _build_event("review", "", success=True)
        event["tool_input"]["order_id"] = ""
        _run_hook(event, tmp_dir=tmp_path, env={"AUTOSKILLIT_DISPATCH_ID": "AB"})

        tracker = _read_tracker(tmp_path, "AB")
        assert tracker["steps"]["review"]["status"] == "complete"

    def test_step_not_in_tracker_exits_cleanly(self, tmp_path):
        """T3-8: canonical step not in tracker steps -> no banner, no crash."""
        _write_tracker(tmp_path, "AB", {"review": {"status": "pending"}})
        event = _build_event("nonexistent_step", "AB", success=True)
        _, exit_code = _run_hook(event, tmp_dir=tmp_path)
        assert exit_code == 0
        tracker = _read_tracker(tmp_path, "AB")
        assert tracker["steps"]["review"]["status"] == "pending"
