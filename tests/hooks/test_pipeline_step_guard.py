"""Tests for pipeline_step_guard.py PreToolUse advisory hook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._hook_protocol_oracle import STATUS_COMPLETED, claude_verdict, codex_verdict, run_hook

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_TRACKER_RELPATH = ".autoskillit/temp/pipeline_tracker"


def _run(stdin_data: dict, cwd: Path):
    return run_hook("guards/pipeline_step_guard.py", stdin_data, cwd=cwd)


def _assert_context(event: dict, emission) -> str:
    assert emission.exit_code == 0, emission.stderr
    output = json.loads(emission.stdout)
    hook_output = output["hookSpecificOutput"]
    assert "permissionDecision" not in hook_output
    context = hook_output["additionalContext"]
    for verdict_fn in (codex_verdict, claude_verdict):
        verdict = verdict_fn(
            event["hook_event_name"],
            exit_code=emission.exit_code,
            stdout=emission.stdout,
            stderr=emission.stderr,
        )
        assert verdict.status == STATUS_COMPLETED
        assert verdict.contexts
    return context


def _write_tracker(tmp_path, order_id, steps, dependencies, kitchen_id="test-kitchen"):
    tracker_dir = tmp_path / _TRACKER_RELPATH
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_dir.joinpath(f"{order_id}.json").write_text(
        json.dumps(
            {
                "pipeline_id": order_id,
                "kitchen_id": kitchen_id,
                "initialized_at": "2026-05-31T01:00:00Z",
                "steps": steps,
                "dependencies": dependencies,
            }
        )
    )


def _write_hook_config(tmp_path, kitchen_id):
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath(".hook_config.json").write_text(json.dumps({"kitchen_id": kitchen_id}))


class TestPipelineStepGuard:
    def test_allows_when_no_tracker(self, tmp_path):
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "review", "order_id": "AB"},
        }
        emission = _run(event, cwd=tmp_path)
        assert emission.exit_code == 0
        assert emission.stdout.strip() == ""

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
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "b", "order_id": "AB"},
        }
        emission = _run(event, cwd=tmp_path)
        assert emission.exit_code == 0
        assert emission.stdout.strip() == ""

    def test_warns_on_unmet_deps(self, tmp_path):
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
        )
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "b", "order_id": "AB"},
        }
        context = _assert_context(event, _run(event, cwd=tmp_path))
        assert "a" in context
        assert "Pipeline" in context

    def test_allows_empty_step_name(self, tmp_path):
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}},
            {"a": ["something"]},
        )
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "", "order_id": "AB"},
        }
        emission = _run(event, cwd=tmp_path)
        assert emission.exit_code == 0
        assert emission.stdout.strip() == ""

    def test_fails_open_on_malformed_tracker(self, tmp_path):
        tracker_dir = tmp_path / _TRACKER_RELPATH
        tracker_dir.mkdir(parents=True, exist_ok=True)
        tracker_dir.joinpath("AB.json").write_text("not valid json{{{")

        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "review", "order_id": "AB"},
        }
        emission = _run(event, cwd=tmp_path)
        assert emission.exit_code == 0
        assert emission.stdout.strip() == ""

    def test_order_id_discovered_from_single_tracker_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _write_hook_config(tmp_path, "kitchen-1")
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
            kitchen_id="kitchen-1",
        )
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "b", "order_id": ""},
        }
        assert "a" in _assert_context(event, _run(event, cwd=tmp_path))

    def test_advisory_on_ambiguous_tracker_state(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        _write_hook_config(tmp_path, "kitchen-1")
        _write_tracker(
            tmp_path,
            "AB",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
            kitchen_id="kitchen-1",
        )
        _write_tracker(
            tmp_path,
            "CD",
            {"a": {"status": "pending"}, "b": {"status": "pending"}},
            {"b": ["a"]},
            kitchen_id="kitchen-1",
        )
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "b", "order_id": ""},
        }
        assert "cannot resolve tracker" in _assert_context(event, _run(event, cwd=tmp_path))

    def test_advisory_when_no_kitchen_id_available(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        event = {
            "hook_event_name": "PreToolUse",
            "tool_input": {"step_name": "b", "order_id": ""},
        }
        assert "cannot resolve tracker" in _assert_context(event, _run(event, cwd=tmp_path))
