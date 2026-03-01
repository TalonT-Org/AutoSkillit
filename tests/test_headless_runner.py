"""Tests for headless_runner.py extracted helpers."""

import json

import pytest

from autoskillit.config import AutomationConfig, ModelConfig
from autoskillit.core.types import SubprocessResult, TerminationReason


@pytest.fixture
def make_config():
    """Factory fixture that creates an AutomationConfig with custom model settings."""

    def _make(model_override=None, model_default=None):
        cfg = AutomationConfig()
        cfg.model = ModelConfig(default=model_default, override=model_override)
        return cfg

    return _make


def test_ensure_skill_prefix_prepends_use_for_slash_commands():
    from autoskillit.execution.headless import _ensure_skill_prefix

    assert _ensure_skill_prefix("/investigate foo") == "Use /investigate foo"


def test_ensure_skill_prefix_leaves_plain_text_unchanged():
    from autoskillit.execution.headless import _ensure_skill_prefix

    assert _ensure_skill_prefix("just a plain prompt") == "just a plain prompt"


def test_inject_completion_directive_appends_marker():
    from autoskillit.execution.headless import _inject_completion_directive

    result = _inject_completion_directive("/investigate foo", "%%DONE%%")
    assert "%%DONE%%" in result
    assert "/investigate foo" in result
    assert "ORCHESTRATION DIRECTIVE" in result


def test_resolve_model_prefers_override(make_config):
    from autoskillit.execution.headless import _resolve_model

    cfg = make_config(model_override="opus")
    assert _resolve_model("sonnet", cfg) == "opus"


def test_resolve_model_uses_step_model_when_no_override(make_config):
    from autoskillit.execution.headless import _resolve_model

    cfg = make_config(model_override=None, model_default=None)
    assert _resolve_model("haiku", cfg) == "haiku"


def test_resolve_model_uses_config_default_when_step_empty(make_config):
    from autoskillit.execution.headless import _resolve_model

    cfg = make_config(model_override=None, model_default="sonnet")
    assert _resolve_model("", cfg) == "sonnet"


def test_resolve_model_returns_none_when_all_empty(make_config):
    from autoskillit.execution.headless import _resolve_model

    cfg = make_config(model_override=None, model_default=None)
    assert _resolve_model("", cfg) is None


def _sr(returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT):
    """Build a minimal SubprocessResult for _build_skill_result tests."""
    return SubprocessResult(returncode, stdout, stderr, termination, pid=12345)


class TestBuildSkillResult:
    """Coverage for _build_skill_result — the primary output-routing function."""

    def test_natural_exit_with_success_json_returns_success(self):
        """COMPLETED + valid type=result success JSON → success=True, needs_retry=False."""
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
        skill = _build_skill_result(_sr(stdout=payload))
        assert skill.success is True
        assert skill.needs_retry is False

    def test_timed_out_returns_failure_no_retry(self):
        """TIMED_OUT termination → success=False, needs_retry=False (timeout is non-retriable)."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(_sr(returncode=-1, termination=TerminationReason.TIMED_OUT))
        assert skill.success is False
        assert skill.needs_retry is False

    def test_stale_with_valid_result_in_stdout_recovers(self):
        """STALE termination + valid result JSON in stdout → recovered_from_stale."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Recovered output.",
                "session_id": "sess-stale",
            }
        )
        skill = _build_skill_result(
            _sr(returncode=-15, stdout=payload, termination=TerminationReason.STALE)
        )
        assert skill.success is True
        assert skill.subtype == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure_and_retry(self):
        """STALE termination + no result in stdout → success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(
            _sr(returncode=-15, stdout="", termination=TerminationReason.STALE)
        )
        assert skill.success is False
        assert skill.needs_retry is True


class TestRunHeadlessCore:
    """Integration test for run_headless_core via the injected mock runner."""

    @pytest.mark.asyncio
    async def test_run_headless_core_returns_success_result(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed. {marker}",
                "session_id": "sess-xyz",
            }
        )
        tool_ctx.runner.push(
            SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)
        )
        result = await run_headless_core("/investigate foo", cwd="/tmp", ctx=tool_ctx)
        assert result.success is True
        assert result.needs_retry is False
        assert result.result == "Task completed."
