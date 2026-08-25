"""run_skill telemetry, observability, timing, response typing tests (#4796)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import structlog.contextvars
import structlog.testing

from autoskillit.core.types import ChannelConfirmation, RetryReason
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result
from tests.server.conftest import _SUCCESS_JSON, assert_no_timing, assert_step_timed

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
                    "cache_write_tokens": 8,
                    "cache_read_tokens": 3,
                },
            }
        )
        return result_rec

    @pytest.mark.anyio
    async def test_step_name_records_token_usage(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot (not a git repo)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="plan"
        )
        report = tool_ctx_kitchen_open.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.anyio
    async def test_no_step_name_records_with_ad_hoc_label(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot (not a git repo)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        report = tool_ctx_kitchen_open.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "(ad-hoc)"
        assert report[0]["input_tokens"] == 200

    @pytest.mark.anyio
    async def test_dispatch_id_env_records_with_dispatch_label(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """step_name='' with AUTOSKILLIT_DISPATCH_ID set records under dispatch:{id} label."""
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "abc-123")
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot (not a git repo)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(skill_command="/autoskillit:investigate topic", cwd="/tmp", step_name="")
        report = tool_ctx_kitchen_open.token_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "dispatch:abc-123"
        assert report[0]["input_tokens"] == 200

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
    async def test_step_name_run_skill_long_running(self, tool_ctx_kitchen_open):
        """run_skill accumulates token usage by step_name (run_skill_retry test replacement)."""
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=1)
        )  # clone guard snapshot (not a git repo)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=self._make_ndjson()))
        await run_skill(
            skill_command="/autoskillit:investigate the test failures",
            cwd="/tmp",
            step_name="implement",
        )
        report = tool_ctx_kitchen_open.token_log.get_report()
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
    async def test_run_skill_binds_tool_contextvar_and_calls_ctx_info(
        self, tool_ctx_kitchen_open, mock_ctx
    ):
        """run_skill binds tool='run_skill' contextvar and calls ctx.info on success."""
        tool_ctx_kitchen_open.runner.push(
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
        assert logs, "Expected at least one log record"
        assert all(entry.get("tool") == "run_skill" for entry in logs)

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
    async def test_run_skill_records_timing_via_step_name(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, _SUCCESS_JSON, ""))
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert_step_timed(tool_ctx_kitchen_open.timing_log, "implement")

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
                        "cache_write_tokens": 0,
                        "cache_read_tokens": 0,
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
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                },
            }
        )
        return asst + "\n" + result

    @pytest.mark.anyio
    async def test_passes_step_telemetry_to_flush(self, tool_ctx_kitchen_open, monkeypatch):
        """flush_session_log is called with step_name, token_usage, and timing_seconds."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=0, stdout=self._make_ndjson_with_usage())
        )
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert calls[0]["step_name"] == "implement"
        assert calls[0]["telemetry"].token_usage is not None
        assert calls[0]["telemetry"].timing_seconds is not None

    @pytest.mark.anyio
    async def test_flush_session_log_session_id_matches_returned_skill_result(
        self, tool_ctx_kitchen_open, monkeypatch
    ):
        """flush_session_log receives the same session_id as the returned SkillResult."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.core.types import SubprocessResult, TerminationReason

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        # Stale result with session_id resolved from Channel B
        stale_result = SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="",
            termination=TerminationReason.STALE,
            pid=12345,
            session_id="test-uuid-coherence-check",
        )
        tool_ctx_kitchen_open.runner.push(stale_result)
        result_json = json.loads(
            await run_skill("/investigate foo", "/tmp", step_name="implement")
        )
        assert len(calls) == 1
        # flush and returned SkillResult must carry the same session_id
        assert calls[0]["session_id"] == result_json["session_id"]
        assert result_json["session_id"] != ""

    @pytest.mark.anyio
    async def test_flushes_on_success_when_step_name_set(self, tool_ctx_kitchen_open, monkeypatch):
        """Successful sessions without proc_snapshots still flush when step_name is provided."""
        import autoskillit.execution.session_log as sl_mod

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        assert len(calls) == 1

    @pytest.mark.anyio
    async def test_records_timing_in_timing_log(self, tool_ctx_kitchen_open):
        """ctx.timing_log.record() is called with step_name and computed timing_seconds."""
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=_SUCCESS_JSON))
        await run_skill("/investigate foo", "/tmp", step_name="plan")
        report = tool_ctx_kitchen_open.timing_log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["total_seconds"] >= 0.0

    @pytest.mark.anyio
    async def test_passes_github_api_log_to_flush(self, tool_ctx_kitchen_open, monkeypatch):
        """headless.py drains github_api_log into telemetry.github_api_usage."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog

        log = DefaultGitHubApiLog()
        await log.record_gh_cli(
            subcommand="gh issue list",
            exit_code=0,
            latency_ms=50.0,
            timestamp="2026-05-02T10:00:00Z",
            step_name="implement",
        )
        tool_ctx_kitchen_open.github_api_log = log

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=0, stdout=self._make_ndjson_with_usage())
        )
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert calls[0]["telemetry"].github_api_usage is not None
        assert calls[0]["telemetry"].github_api_requests > 0

    @pytest.mark.anyio
    async def test_flush_telemetry_kwargs_exhaustive(self, tool_ctx_kitchen_open, monkeypatch):
        """headless.py passes a SessionTelemetry bundle covering all telemetry fields."""
        import autoskillit.execution.session_log as sl_mod
        from autoskillit.core.types._type_results_execution import SessionTelemetry

        calls = []

        def mock_flush(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(sl_mod, "flush_session_log", mock_flush)
        tool_ctx_kitchen_open.runner.push(
            _make_result(returncode=0, stdout=self._make_ndjson_with_usage())
        )
        await run_skill("/investigate foo", "/tmp", step_name="implement")
        assert len(calls) == 1
        assert "telemetry" in calls[0], "flush_session_log must receive telemetry= kwarg"
        assert isinstance(calls[0]["telemetry"], SessionTelemetry), (
            "telemetry must be a SessionTelemetry instance"
        )
