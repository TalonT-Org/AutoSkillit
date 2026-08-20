"""Tests for debug logging instrumentation in headless.py."""

from __future__ import annotations

import json

import pytest
import structlog.testing

from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
            _build_skill_result(_sr(stdout=payload), backend=ClaudeCodeBackend())
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
            _build_skill_result(_sr(stdout=payload), backend=ClaudeCodeBackend())
        exit_logs = [r for r in logs if r.get("event") == "build_skill_result_exit"]
        assert exit_logs, (
            f"Expected build_skill_result_exit, got: {[r.get('event') for r in logs]}"
        )
        assert exit_logs[0]["success"] is True
        assert exit_logs[0]["needs_retry"] is False

    def test_logs_stale_branch(self):
        from autoskillit.execution.headless import _build_skill_result

        with structlog.testing.capture_logs() as logs:
            _build_skill_result(
                _sr(stdout="", termination=TerminationReason.STALE), backend=ClaudeCodeBackend()
            )
        entry_logs = [r for r in logs if r.get("event") == "build_skill_result_entry"]
        assert entry_logs
        assert entry_logs[0]["branch"] == "stale"

    def test_logs_timed_out_branch(self):
        from autoskillit.execution.headless import _build_skill_result

        with structlog.testing.capture_logs() as logs:
            _build_skill_result(
                _sr(stdout="", returncode=-1, termination=TerminationReason.TIMED_OUT),
                backend=ClaudeCodeBackend(),
            )
        entry_logs = [r for r in logs if r.get("event") == "build_skill_result_entry"]
        assert entry_logs
        assert entry_logs[0]["branch"] == "timed_out"


class TestResolveModelLogging:
    """Verify resolve_model_pin logs which priority tier resolved the model."""

    def _make_config(self, *, override=None, default=None):
        from tests._helpers import make_model_config, make_test_config

        cfg = make_test_config()
        cfg.model = make_model_config(default_model=default, model_override=override)
        return cfg

    def test_logs_override_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("sonnet", self._make_config(override="opus"))
        assert pin.model == "opus"
        assert pin.source.kind == LaunchValueSourceKind.GLOBAL
        assert pin.source.key_path == "model.model_override"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "override"
        assert model_logs[0]["model"] == "opus"

    def test_logs_step_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("sonnet", self._make_config())
        assert pin.model == "sonnet"
        assert pin.source.kind == LaunchValueSourceKind.CALLER
        assert pin.source.key_path == "run_skill.model"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "step"

    def test_logs_default_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("", self._make_config(default="haiku"))
        assert pin.model == "haiku"
        assert pin.source.kind == LaunchValueSourceKind.DEFAULT
        assert pin.source.key_path == "model.default_model"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "default"

    def test_logs_none_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("", self._make_config())
        assert pin.model == ""
        assert pin.source.kind == LaunchValueSourceKind.DEFAULT
        assert pin.source.key_path == "run_skill.defaults"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "none"

    def test_logs_recipe_override_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config()
        cfg.model.recipe_overrides = {"impl": {"plan": "opus"}}
        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("", cfg, step_name="plan", recipe_name="impl")
        assert pin.model == "opus"
        assert pin.source.kind == LaunchValueSourceKind.RECIPE
        assert pin.source.key_path == "model.recipe_overrides.impl.plan"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "recipe_override"

    def test_logs_step_override_tier(self):
        from autoskillit.core import LaunchValueSourceKind
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config()
        cfg.model.step_overrides = {"plan": "opus"}
        with structlog.testing.capture_logs() as logs:
            pin = resolve_model_pin("", cfg, step_name="plan")
        assert pin.model == "opus"
        assert pin.source.kind == LaunchValueSourceKind.STEP
        assert pin.source.key_path == "model.step_overrides.plan"
        model_logs = [r for r in logs if r.get("event") == "model_resolved"]
        assert model_logs
        assert model_logs[0]["tier"] == "step_override"

    def test_recipe_override_beats_step_override(self):
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config()
        cfg.model.step_overrides = {"plan": "haiku"}
        cfg.model.recipe_overrides = {"impl": {"plan": "opus"}}
        pin = resolve_model_pin("", cfg, step_name="plan", recipe_name="impl")
        assert pin.model == "opus"

    def test_step_override_beats_step_model(self):
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config()
        cfg.model.step_overrides = {"plan": "opus"}
        pin = resolve_model_pin("sonnet", cfg, step_name="plan")
        assert pin.model == "opus"

    def test_override_beats_recipe_override(self):
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config(override="haiku")
        cfg.model.recipe_overrides = {"impl": {"plan": "opus"}}
        pin = resolve_model_pin("", cfg, step_name="plan", recipe_name="impl")
        assert pin.model == "haiku"

    def test_recipe_override_no_leak(self):
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config(default="sonnet")
        cfg.model.recipe_overrides = {"impl": {"plan": "opus"}}
        pin = resolve_model_pin("", cfg, step_name="plan", recipe_name="other")
        assert pin.model == "sonnet"

    def test_recipe_override_miss_falls_through_to_default(self):
        """A recipe-name hit with a step-key miss does not jump straight to
        default_model — resolution still falls through the step_overrides
        and step_model (caller-arg) tiers first. This case only lands on
        default_model because the fixture leaves both of those tiers empty.
        """
        from autoskillit.execution.headless import resolve_model_pin

        cfg = self._make_config(default="haiku")
        cfg.model.recipe_overrides = {"impl": {"plan": "opus"}}
        pin = resolve_model_pin("", cfg, step_name="other-step", recipe_name="impl")
        assert pin.model == "haiku"
        assert pin.source.key_path == "model.default_model"
