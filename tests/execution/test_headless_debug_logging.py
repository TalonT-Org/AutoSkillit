"""Tests for debug logging instrumentation in headless.py."""

from __future__ import annotations

import json

import structlog.testing

from autoskillit.core.types import SubprocessResult, TerminationReason


def _sr(returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT):
    """Build a minimal SubprocessResult for _build_skill_result tests."""
    return SubprocessResult(returncode, stdout, stderr, termination, pid=12345)


class TestBuildSkillResultLogging:
    """Verify _build_skill_result logs entry and exit."""

    def test_logs_entry(self):
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "sess-abc",
            }
        )
        with structlog.testing.capture_logs() as logs:
            _build_skill_result(_sr(stdout=payload))
        entry_logs = [r for r in logs if r.get("event") == "build_skill_result_entry"]
        assert entry_logs, (
            f"Expected build_skill_result_entry, got: {[r.get('event') for r in logs]}"
        )
        assert entry_logs[0]["branch"] == "normal"
        assert entry_logs[0]["pid"] == 12345

    def test_logs_exit(self):
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "sess-abc",
            }
        )
        with structlog.testing.capture_logs() as logs:
            _build_skill_result(_sr(stdout=payload))
        exit_logs = [r for r in logs if r.get("event") == "build_skill_result_exit"]
        assert exit_logs, (
            f"Expected build_skill_result_exit, got: {[r.get('event') for r in logs]}"
        )
        assert exit_logs[0]["success"] is True
        assert exit_logs[0]["needs_retry"] is False

    def test_logs_stale_branch(self):
        from autoskillit.execution.headless import _build_skill_result

        with structlog.testing.capture_logs() as logs:
            _build_skill_result(_sr(stdout="", termination=TerminationReason.STALE))
        entry_logs = [r for r in logs if r.get("event") == "build_skill_result_entry"]
        assert entry_logs
        assert entry_logs[0]["branch"] == "stale"

    def test_logs_timed_out_branch(self):
        from autoskillit.execution.headless import _build_skill_result

        with structlog.testing.capture_logs() as logs:
            _build_skill_result(
                _sr(stdout="", returncode=-1, termination=TerminationReason.TIMED_OUT)
            )
        entry_logs = [r for r in logs if r.get("event") == "build_skill_result_entry"]
        assert entry_logs
        assert entry_logs[0]["branch"] == "timed_out"


class TestResolveModelLogging:
    """Verify _resolve_model logs which priority tier resolved the model."""

    def _make_config(self, *, override=None, default=None):
        from tests._helpers import make_model_config, make_test_config

        cfg = make_test_config()
        cfg.model = make_model_config(default=default, override=override)
        return cfg

    def test_logs_override_tier(self):
        from autoskillit.execution.headless import _resolve_model

        with structlog.testing.capture_logs() as logs:
            result = _resolve_model("sonnet", self._make_config(override="opus"))
        assert result == "opus"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "override"
        assert model_logs[0]["model"] == "opus"

    def test_logs_step_tier(self):
        from autoskillit.execution.headless import _resolve_model

        with structlog.testing.capture_logs() as logs:
            result = _resolve_model("sonnet", self._make_config())
        assert result == "sonnet"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "step"

    def test_logs_default_tier(self):
        from autoskillit.execution.headless import _resolve_model

        with structlog.testing.capture_logs() as logs:
            result = _resolve_model("", self._make_config(default="haiku"))
        assert result == "haiku"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "default"

    def test_logs_none_tier(self):
        from autoskillit.execution.headless import _resolve_model

        with structlog.testing.capture_logs() as logs:
            result = _resolve_model("", self._make_config())
        assert result is None
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "none"
