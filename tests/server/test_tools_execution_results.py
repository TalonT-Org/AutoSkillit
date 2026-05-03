"""Tests for run_skill result shapes, failure paths, timing, flush telemetry, and gate checks."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing
from autoskillit.server.tools_execution import run_skill

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
)
from tests.conftest import _make_result
from tests.server.conftest import _SUCCESS_JSON, assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestGateErrorSchemaNormalization:
    """Gate errors use the standard 9-field response schema."""

    def test_require_enabled_gate_returns_standard_schema(self, tool_ctx):
        """Gate errors must use the same schema as normal responses."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._guards import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        gate_result = _require_enabled()
        assert gate_result is not None
        response = json.loads(gate_result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["needs_retry"] is False
        assert "result" in response

    def test_dry_walkthrough_gate_returns_standard_schema(self, tool_ctx, tmp_path):
        """Dry-walkthrough gate errors must use the standard response schema."""
        from autoskillit.server._guards import _check_dry_walkthrough

        plan = tmp_path / "plan.md"
        plan.write_text("No marker here")
        skill_cmd = f"/implement-worktree {plan}"
        result = _check_dry_walkthrough(skill_cmd, str(tmp_path))
        assert result is not None
        response = json.loads(result)
        assert response["success"] is False
        assert response["is_error"] is True
        assert response["subtype"] == "gate_error"


class TestRunSkillFailurePaths:
    """run_skill surfaces session outcome on failure."""

    @pytest.mark.anyio
    async def test_returns_subtype_on_incomplete_session(self, tool_ctx):
        """run_skill includes subtype when session didn't finish."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["session_id"] == "s1"
        assert result["subtype"] == "error_max_turns"

    @pytest.mark.anyio
    async def test_returns_is_error_on_context_limit(self, tool_ctx):
        """run_skill includes is_error when context limit is hit."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["is_error"] is True
        assert result["subtype"] == "context_exhausted"
        assert result["cli_subtype"] == "success"

    @pytest.mark.anyio
    async def test_handles_empty_stdout(self, tool_ctx):
        """run_skill returns error result when stdout is empty."""
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(1, "", "segfault", channel_confirmation=ChannelConfirmation.UNMONITORED)
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["exit_code"] == 1
        assert result["is_error"] is True
        assert result["subtype"] == "empty_output"
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_empty_stdout_exit_zero_is_retriable(self, tool_ctx):
        """Infrastructure failure (empty stdout, exit 0) is retriable with stderr."""
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(
            _make_result(
                0, "", "session dropped", channel_confirmation=ChannelConfirmation.UNMONITORED
            )
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["subtype"] == "empty_output"
        assert result["success"] is False
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.EMPTY_OUTPUT
        assert result["stderr"] == "session dropped"


class TestRunSkillStepName:
    """step_name param drives token_log accumulation."""

    def _make_ndjson(self) -> str:
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task complete.",
                "session_id": "sess-abc",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 3,
                },
            }
        )
        return result_rec

    @pytest.mark.anyio
    async def test_step_name_records_token_usage(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot (not a git repo)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.anyio
    async def test_no_step_name_does_not_record(self, tool_ctx):
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot (not a git repo)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.anyio
    async def test_null_token_usage_does_not_record(self, tool_ctx):
        # Return NDJSON with no usage field → token_usage will be null
        no_usage_ndjson = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot (not a git repo)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=no_usage_ndjson))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        assert tool_ctx.token_log.get_report() == []

    @pytest.mark.anyio
    async def test_step_name_run_skill_long_running(self, tool_ctx):
        """run_skill accumulates token usage by step_name (run_skill_retry test replacement)."""
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot (not a git repo)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate the test failures",
            cwd="/tmp",
            step_name="implement",
        )
        report = tool_ctx.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "implement"
        assert report[0]["input_tokens"] == 200


class TestGatedToolObservability:
    """Each gated tool binds structlog contextvars and calls ctx.info/ctx.error."""

    @pytest.fixture
    def mock_ctx(self):
        """AsyncMock ctx for verifying ctx.info/ctx.error calls."""
        ctx = AsyncMock()
        ctx.info = AsyncMock()
        ctx.error = AsyncMock()
        return ctx

    @pytest.mark.anyio
    async def test_run_skill_binds_tool_contextvar_and_calls_ctx_info(self, tool_ctx, mock_ctx):
        """run_skill binds tool='run_skill' contextvar and calls ctx.info on success."""
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        with structlog.testing.capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as logs:
            await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx)
        assert any(entry.get("tool") == "run_skill" for entry in logs)

    @pytest.mark.anyio
    async def test_run_skill_returns_failure_result_on_error_output(self, tool_ctx, mock_ctx):
        """run_skill reports failure (success=false) when headless session fails."""
        tool_ctx.runner.push(
            _make_result(
                1,
                '{"type": "result", "subtype": "error", "is_error": true,'
                ' "result": "failed", "session_id": "s1"}',
                "",
                channel_confirmation=ChannelConfirmation.UNMONITORED,
            )
        )
        result = json.loads(await run_skill("/autoskillit:investigate task", "/tmp", ctx=mock_ctx))
        assert result["success"] is False


class TestNotifyHelper:
    """Unit tests for the centralized _notify() notification helper."""

    @pytest.mark.anyio
    async def test_notify_raises_value_error_for_reserved_key_name(self):
        """The 'name' key that caused the original bug must be rejected."""
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        with pytest.raises(ValueError, match="reserved LogRecord"):
            await _notify(
                ctx,
                "info",
                "migrate_recipe: foo",
                "autoskillit.migrate_recipe",
                extra={"name": "foo"},
            )
        ctx.info.assert_not_awaited()

    @pytest.mark.anyio
    async def test_notify_raises_for_all_reserved_keys(self):
        """Every key in RESERVED_LOG_RECORD_KEYS must be rejected."""
        from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        for reserved_key in RESERVED_LOG_RECORD_KEYS:
            with pytest.raises(ValueError, match="reserved LogRecord"):
                await _notify(ctx, "info", "msg", "logger", extra={reserved_key: "value"})

    @pytest.mark.anyio
    async def test_notify_accepts_safe_key_recipe_name(self):
        """'recipe_name' (the corrected key for migrate_recipe) must be accepted."""
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(
            ctx,
            "info",
            "migrate_recipe: foo",
            "autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )
        ctx.info.assert_awaited_once_with(
            "migrate_recipe: foo",
            logger_name="autoskillit.migrate_recipe",
            extra={"recipe_name": "foo"},
        )

    @pytest.mark.anyio
    async def test_notify_accepts_none_extra(self):
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger")  # no extra
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_accepts_empty_extra(self):
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock()
        await _notify(ctx, "info", "msg", "logger", extra={})
        ctx.info.assert_awaited_once()

    @pytest.mark.anyio
    async def test_notify_swallows_attribute_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises AttributeError
        (e.g. _CurrentContext sentinel). Test completion is the assertion."""
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=AttributeError("no info"))
        # Must not raise
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_runtime_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises RuntimeError
        (no active MCP session). Test completion is the assertion."""
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=RuntimeError("session not available"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_swallows_key_error_from_ctx(self):
        """Contract: must not raise even when ctx.info raises KeyError
        (FastMCP stdlib logging path). Test completion is the assertion."""
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.info = AsyncMock(side_effect=KeyError("Attempt to overwrite 'name' in LogRecord"))
        await _notify(ctx, "info", "msg", "logger", extra={"cwd": "/tmp"})

    @pytest.mark.anyio
    async def test_notify_dispatches_error_level(self):
        from autoskillit.server._notify import _notify

        ctx = AsyncMock()
        ctx.error = AsyncMock()
        await _notify(
            ctx,
            "error",
            "run_cmd failed",
            "autoskillit.run_cmd",
            extra={"exit_code": 1},
        )
        ctx.error.assert_awaited_once_with(
            "run_cmd failed",
            logger_name="autoskillit.run_cmd",
            extra={"exit_code": 1},
        )


class TestHeadlessGateEnforcement:
    """T_HGE: run_skill, run_cmd, run_python each return headless_error
    when the session is running with AUTOSKILLIT_HEADLESS=1 and SESSION_TYPE=leaf.

    The gate is open (tool_ctx default), so _require_enabled() passes.
    _require_orchestrator_or_higher() fires first and returns subtype='headless_error'.
    """

    @pytest.fixture(autouse=True)
    def _set_headless_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")

    @pytest.mark.anyio
    async def test_run_skill_blocked_in_headless_session(self, tool_ctx):
        """run_skill returns headless_error when AUTOSKILLIT_HEADLESS=1 and SESSION_TYPE=leaf."""
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result["subtype"] == "headless_error"


@pytest.mark.feature("fleet")
class TestTierAwareGateEnforcement:
    """T_TAGE: tier-aware guard permits orchestrator, denies leaf and fleet as appropriate."""

    @pytest.mark.anyio
    async def test_run_skill_permitted_for_orchestrator_tier(self, tool_ctx, monkeypatch):
        """run_skill does NOT return headless_error for orchestrator-tier headless sessions."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot (not a git repo)
        tool_ctx.runner.push(
            _make_result(
                returncode=0,
                stdout=json.dumps({"type": "result", "subtype": "success", "is_error": False}),
            )
        )
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result.get("cli_subtype") == "success"

    @pytest.mark.anyio
    async def test_run_skill_denied_for_leaf_tier(self, tool_ctx, monkeypatch):
        """run_skill returns headless_error for leaf-tier headless sessions."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        result = json.loads(await run_skill("/autoskillit:investigate some-error", "/tmp"))
        assert result["subtype"] == "headless_error"

    @pytest.mark.anyio
    async def test_open_kitchen_denied_for_fleet_tier(self, tool_ctx, monkeypatch):
        """open_kitchen returns HeadlessDenied for fleet-tier sessions."""
        from autoskillit.server.tools_kitchen import open_kitchen

        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        result = json.loads(await open_kitchen())
        assert result.get("error") == "HeadlessDenied"
        msg = result.get("user_visible_message", "").lower()
        assert "fleet" in msg

    @pytest.mark.anyio
    async def test_close_kitchen_denied_for_fleet_tier(self, tool_ctx, monkeypatch):
        """close_kitchen returns headless_error for fleet-tier sessions."""
        from autoskillit.server.tools_kitchen import close_kitchen

        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
        result = json.loads(await close_kitchen())
        assert result["subtype"] == "headless_error"


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.anyio
    async def test_retry_reason_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "num_turns": 200,
                "errors": [],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.anyio
    async def test_retry_reason_none_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
                "num_turns": 50,
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}


class TestRunSkillTiming:
    """run_skill accumulates wall-clock timing when step_name is provided."""

    @pytest.mark.anyio
    async def test_run_skill_records_timing_via_step_name(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert_step_timed(tool_ctx.timing_log, "implement")

    @pytest.mark.anyio
    async def test_run_skill_empty_step_name_skips_timing(self, tool_ctx):
        tool_ctx.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate foo", "/tmp")
        assert_no_timing(tool_ctx.timing_log)


class TestRunHeadlessCoreFlushTelemetry:
    """flush_session_log receives telemetry kwargs when step_name is provided."""

    def _make_ndjson_with_usage(self) -> str:
        asst = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    }
                },
                "model": "claude-opus-4-6",
            }
        )
        result = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
        )
        return asst + "\n" + result

    @pytest.mark.anyio
    async def test_passes_step_telemetry_to_flush(self, tool_ctx, monkeypatch):
        """flush_session_log is called with step_name, token_usage, and timing_seconds."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson_with_usage()))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert calls[0]["step_name"] == "implement"
        assert calls[0]["telemetry"].token_usage is not None
        assert calls[0]["telemetry"].timing_seconds is not None

    @pytest.mark.anyio
    async def test_flush_session_log_session_id_matches_returned_skill_result(
        self, tool_ctx, monkeypatch
    ):
        """flush_session_log receives the same session_id as the returned SkillResult."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.core.types import SubprocessResult, TerminationReason

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=1))  # clone guard snapshot
        # Stale result with session_id resolved from Channel B
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
            session_id="test-uuid-coherence-check",
        )
        tool_ctx.runner.push(stale_result)
        result_json = json.loads(
            await run_skill("/investigate foo", "/tmp", step_name="implement")
        )
        assert len(calls) == 1
        # flush and returned SkillResult must carry the same session_id
        assert calls[0]["session_id"] == result_json["session_id"]
        assert result_json["session_id"] != ""

    @pytest.mark.anyio
    async def test_flushes_on_success_when_step_name_set(self, tool_ctx, monkeypatch):
        """Successful sessions without proc_snapshots still flush when step_name is provided."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_records_timing_in_timing_log(self, tool_ctx):
        """ctx.timing_log.record() is called with step_name and computed timing_seconds."""
        tool_ctx.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        report = tool_ctx.timing_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["total_seconds"] >= 0.0

    @pytest.mark.anyio
    async def test_passes_github_api_log_to_flush(self, tool_ctx, monkeypatch):
        """headless.py drains github_api_log into telemetry.github_api_usage."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog

        log = DefaultGitHubApiLog()
        await log.record_gh_cli(
            subcommand="gh issue list",
            exit_code=0,
            latency_ms=50.0,
            timestamp="2026-05-02T10:00:00Z",
        )
        tool_ctx.github_api_log = log

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson_with_usage()))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert calls[0]["telemetry"].github_api_usage is not None
        assert calls[0]["telemetry"].github_api_requests > 0

    @pytest.mark.anyio
    async def test_flush_telemetry_kwargs_exhaustive(self, tool_ctx, monkeypatch):
        """headless.py passes a SessionTelemetry bundle covering all telemetry fields."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.core.types._type_results import SessionTelemetry

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx.runner.push(_make_result(returncode=0, stdout=self._make_ndjson_with_usage()))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert "telemetry" in calls[0], "flush_session_log must receive telemetry= kwarg"
        assert isinstance(calls[0]["telemetry"], SessionTelemetry), (
            "telemetry must be a SessionTelemetry instance"
        )


@pytest.mark.anyio
async def test_run_skill_returns_structured_error_when_executor_raises(
    tool_ctx, monkeypatch, tmp_path
) -> None:
    """run_skill returns SkillResult-shaped JSON even if executor.run() raises unexpectedly."""
    from autoskillit.core import SkillResult

    class ExplodingExecutor:
        async def run(self, *args, **kwargs) -> SkillResult:
            raise RuntimeError("unexpected executor failure")

    tool_ctx.executor = ExplodingExecutor()
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

    result_json = await run_skill("/test cmd", str(tmp_path))
    data = json.loads(result_json)
    assert data["success"] is False
    assert data["subtype"] == "crashed"
    assert data["needs_retry"] is False
    assert "unexpected executor failure" in data["result"]
