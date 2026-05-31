"""Tests for pipeline_step_guard.py PreToolUse advisory hook."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

SCRIPT = (
    Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/guards/pipeline_step_guard.py"
)

_TRACKER_RELPATH = ".autoskillit/temp/pipeline_tracker"


def _run(stdin_data: str, cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode, result.stdout


def _write_tracker(tmp_path, order_id, steps, dependencies):
    tracker_dir = tmp_path / _TRACKER_RELPATH
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{order_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": order_id,
                "kitchen_id": "test-kitchen",
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies,
            }
        )
    )


class TestPipelineStepGuard:
    def test_allows_when_no_tracker(self, tmp_path):
        event = json.dumps({"tool_input": {"step_name": "review", "order_id": "AB"}})
        code, stdout = _run(event, cwd=tmp_path)
        assert code == 0
        assert stdout.strip() == ""

    def test_allows_when_deps_met(self, tmp_path):
        _write_tracker(
            tmp_path,
            "AB",
            {
                "a": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                "b": {"status": "pending"},
            },
            {"b": ["a"]},
        )
        event = json.dumps({"tool_input": {"step_name": "b", "order_id": "AB"}})
        code, stdout = _run(event, cwd=tmp_path)
        assert code == 0
        assert stdout.strip() == ""

    def test_warns_on_unmet_deps(self, tmp_path):
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        event = json.dumps({"tool_input": {"step_name": "b", "order_id": "AB"}})
        code, stdout = _run(event, cwd=tmp_path)
        assert code == 0
        output = json.loads(stdout)
        hook_output = output["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "allow"
        assert "a" in hook_output["additionalContext"]
        assert "Pipeline" in hook_output["additionalContext"]

    def test_allows_empty_step_name(self, tmp_path):
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}},
            {"a": ["something"]},
        )
        event = json.dumps({"tool_input": {"step_name": "", "order_id": "AB"}})
        code, stdout = _run(event, cwd=tmp_path)
        assert code == 0
        assert stdout.strip() == ""

    def test_fails_open_on_malformed_tracker(self, tmp_path):
        tracker_dir = tmp_path / _TRACKER_RELPATH
        tracker_dir.mkdir(parents=True, exist_ok=True)
        tracker_dir.joinpath("AB.json").write_text("not valid json{{{")

        event = json.dumps({"tool_input": {"step_name": "review", "order_id": "AB"}})
        code, stdout = _run(event, cwd=tmp_path)
        assert code == 0
        assert stdout.strip() == ""
