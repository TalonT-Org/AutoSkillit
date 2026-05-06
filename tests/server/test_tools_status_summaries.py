"""Tests for autoskillit server status tools: token and timing summaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_status import (
    get_pipeline_report,
    get_timing_summary,
    get_token_summary,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestGetTokenSummary:
    """get_token_summary is a gated tool that returns accumulated token usage."""

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        """get_token_summary returns gate_error when kitchen gate is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await get_token_summary())
        assert result.get("success") is False
        assert result.get("subtype") == "gate_error"

    @pytest.mark.anyio
    async def test_returns_empty_steps_initially(self, tool_ctx):
        result = json.loads(await get_token_summary())
        assert result["steps"] == []
        assert result["total"]["input_tokens"] == 0
        assert result["total"]["output_tokens"] == 0
        assert result["total"]["cache_creation_input_tokens"] == 0
        assert result["total"]["cache_read_input_tokens"] == 0

    @pytest.mark.anyio
    async def test_returns_entry_per_step_name(self, tool_ctx):
        tool_ctx.token_log.record(
            "investigate",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            },
        )
        tool_ctx.token_log.record(
            "implement",
            {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 10,
            },
        )
        result = json.loads(await get_token_summary())
        assert len(result["steps"]) == 2
        assert result["steps"][0]["step_name"] == "investigate"
        assert result["steps"][1]["step_name"] == "implement"

    @pytest.mark.anyio
    async def test_multiple_invocations_same_step_are_summed(self, tool_ctx):
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("implement", usage)
        tool_ctx.token_log.record("implement", usage)
        tool_ctx.token_log.record("implement", usage)
        result = json.loads(await get_token_summary())
        assert len(result["steps"]) == 1
        assert result["steps"][0]["input_tokens"] == 300
        assert result["steps"][0]["invocation_count"] == 3

    @pytest.mark.anyio
    async def test_total_field_sums_all_steps(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
            },
        )
        tool_ctx.token_log.record(
            "implement",
            {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 10,
            },
        )
        result = json.loads(await get_token_summary())
        assert result["total"]["input_tokens"] == 300
        assert result["total"]["output_tokens"] == 130
        assert result["total"]["cache_creation_input_tokens"] == 30
        assert result["total"]["cache_read_input_tokens"] == 15

    @pytest.mark.anyio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )
        result = json.loads(await get_token_summary(clear=True))
        assert len(result["steps"]) == 1
        result2 = json.loads(await get_token_summary())
        assert result2["steps"] == []

    @pytest.mark.anyio
    async def test_response_shape(self, tool_ctx):
        tool_ctx.token_log.record(
            "plan",
            {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 1,
                "cache_read_input_tokens": 2,
            },
        )
        result = json.loads(await get_token_summary())
        assert "steps" in result
        assert "total" in result
        assert "model_totals" in result
        assert isinstance(result["steps"], list)
        assert isinstance(result["model_totals"], list)
        total_keys = set(result["total"].keys())
        assert {
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        } <= total_keys

    @pytest.mark.anyio
    async def test_step_includes_elapsed_seconds(self, tool_ctx):
        """Each step dict in the response includes elapsed_seconds."""
        start = "2026-01-01T00:00:00+00:00"
        end = "2026-01-01T00:00:30+00:00"
        tool_ctx.token_log.record("deploy", {"input_tokens": 200}, start_ts=start, end_ts=end)
        result = json.loads(await get_token_summary())
        assert result["steps"][0]["elapsed_seconds"] == pytest.approx(30.0)

    @pytest.mark.anyio
    async def test_total_includes_total_elapsed_seconds(self, tool_ctx):
        """Total dict includes total_elapsed_seconds summed across all steps."""
        tool_ctx.token_log.record(
            "a",
            {"input_tokens": 10},
            start_ts="2026-01-01T00:00:00+00:00",
            end_ts="2026-01-01T00:00:05+00:00",
        )
        tool_ctx.token_log.record(
            "b",
            {"input_tokens": 20},
            start_ts="2026-01-01T00:01:00+00:00",
            end_ts="2026-01-01T00:01:08+00:00",
        )
        result = json.loads(await get_token_summary())
        assert "total_elapsed_seconds" in result["total"]
        assert result["total"]["total_elapsed_seconds"] == pytest.approx(13.0)


class TestGetTimingSummary:
    """get_timing_summary is a gated tool that returns accumulated wall-clock timing."""

    @pytest.mark.anyio
    async def test_gate_closed_returns_gate_error(self, tool_ctx):
        """get_timing_summary returns gate_error when kitchen gate is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await get_timing_summary())
        assert result.get("success") is False
        assert result.get("subtype") == "gate_error"

    @pytest.mark.anyio
    async def test_returns_empty_steps_initially(self, tool_ctx):
        result = json.loads(await get_timing_summary())
        assert result["steps"] == []
        assert result["total"]["total_seconds"] == 0.0

    @pytest.mark.anyio
    async def test_returns_entry_per_step_name(self, tool_ctx):
        tool_ctx.timing_log.record("clone", 4.0)
        tool_ctx.timing_log.record("test_check", 12.0)
        result = json.loads(await get_timing_summary())
        assert len(result["steps"]) == 2
        step_names = {s["step_name"] for s in result["steps"]}
        assert step_names == {"clone", "test_check"}

    @pytest.mark.anyio
    async def test_multiple_invocations_same_step_are_summed(self, tool_ctx):
        tool_ctx.timing_log.record("impl", 10.0)
        tool_ctx.timing_log.record("impl", 10.0)
        tool_ctx.timing_log.record("impl", 10.0)
        result = json.loads(await get_timing_summary())
        assert len(result["steps"]) == 1
        assert result["steps"][0]["total_seconds"] == 30.0
        assert result["steps"][0]["invocation_count"] == 3

    @pytest.mark.anyio
    async def test_total_field_sums_all_steps(self, tool_ctx):
        tool_ctx.timing_log.record("a", 5.0)
        tool_ctx.timing_log.record("b", 8.0)
        result = json.loads(await get_timing_summary())
        assert result["total"]["total_seconds"] == 13.0

    @pytest.mark.anyio
    async def test_clear_true_resets_after_returning(self, tool_ctx):
        tool_ctx.timing_log.record("clone", 4.0)
        result = json.loads(await get_timing_summary(clear=True))
        assert len(result["steps"]) == 1
        result2 = json.loads(await get_timing_summary())
        assert result2["steps"] == []

    @pytest.mark.anyio
    async def test_response_shape(self, tool_ctx):
        tool_ctx.timing_log.record("plan", 3.0)
        result = json.loads(await get_timing_summary())
        assert "steps" in result
        assert "total" in result
        assert isinstance(result["steps"], list)
        assert "total_seconds" in result["total"]


class TestGetTokenSummaryFormat:
    """Tests for get_token_summary format parameter."""

    @pytest.mark.anyio
    async def test_format_json_default(self, tool_ctx):
        """format='json' (default) returns JSON dict."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = json.loads(await get_token_summary())
        assert "steps" in result
        assert "total" in result

    @pytest.mark.anyio
    async def test_format_table_returns_markdown(self, tool_ctx):
        """format='table' returns a markdown table string."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = await get_token_summary(format="table")
        assert "| Step |" in result
        assert "|---" in result
        assert "plan" in result
        assert "**Total**" in result
        # Not JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)

    @pytest.mark.anyio
    async def test_format_table_with_clear(self, tool_ctx):
        """format='table' + clear=True clears the log after returning."""
        tool_ctx.token_log.record(
            "plan",
            {"input_tokens": 100, "output_tokens": 50},
        )
        result = await get_token_summary(clear=True, format="table")
        assert "plan" in result
        result2 = json.loads(await get_token_summary())
        assert result2["steps"] == []


class TestGetTimingSummaryFormat:
    """Tests for get_timing_summary format parameter."""

    @pytest.mark.anyio
    async def test_format_json_default(self, tool_ctx):
        """format='json' (default) returns JSON dict."""
        tool_ctx.timing_log.record("plan", 3.0)
        result = json.loads(await get_timing_summary())
        assert "steps" in result
        assert "total" in result

    @pytest.mark.anyio
    async def test_format_table_returns_markdown(self, tool_ctx):
        """format='table' returns a markdown table string."""
        tool_ctx.timing_log.record("plan", 3.0)
        result = await get_timing_summary(format="table")
        assert "| Step |" in result
        assert "|---" in result
        assert "plan" in result
        assert "**Total**" in result
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


class TestTokenSummaryWallClock:
    @pytest.mark.anyio
    async def test_wall_clock_seconds_merged_from_timing_log(self, tool_ctx):
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()
        tool_ctx.token_log.record(
            "step-a",
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=8.0,
        )
        tool_ctx.timing_log.record("step-a", 12.5)

        result = json.loads(await get_token_summary())
        step = next(s for s in result["steps"] if s["step_name"] == "step-a")
        assert step["wall_clock_seconds"] == pytest.approx(12.5)
        assert step["elapsed_seconds"] == pytest.approx(8.0)

    @pytest.mark.anyio
    async def test_wall_clock_falls_back_to_elapsed_when_no_timing(self, tool_ctx):
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()
        tool_ctx.token_log.record(
            "step-b",
            {
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            elapsed_seconds=5.0,
        )

        # Verify no step-b entry was injected into timing_log (guards against parallel pollution)
        assert not any(e["step_name"] == "step-b" for e in tool_ctx.timing_log.get_report())
        result = json.loads(await get_token_summary())
        step = next(s for s in result["steps"] if s["step_name"] == "step-b")
        # No timing_log entry → falls back to elapsed_seconds
        assert step["wall_clock_seconds"] == pytest.approx(5.0)

    @pytest.mark.anyio
    async def test_merge_wall_clock_after_normalization(self, tool_ctx):
        """
        When token entries and timing entries are both normalized to canonical names,
        _merge_wall_clock_seconds must match them correctly.
        """
        from autoskillit.pipeline.timings import DefaultTimingLog
        from autoskillit.pipeline.tokens import DefaultTokenLog

        tool_ctx.token_log = DefaultTokenLog()
        tool_ctx.timing_log = DefaultTimingLog()

        # Record via suffixed names — both should normalize to "implement"
        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("implement-30", usage)
        tool_ctx.token_log.record("implement-31", usage)
        tool_ctx.timing_log.record("implement-30", 60.0)
        tool_ctx.timing_log.record("implement-31", 55.0)

        result = json.loads(await get_token_summary(clear=False, format="json"))

        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["step_name"] == "implement"
        assert step["input_tokens"] == 200
        # wall_clock_seconds from timing log should be present
        assert "wall_clock_seconds" in step
        assert step["wall_clock_seconds"] == pytest.approx(115.0)


class TestClearMarkerWritten:
    @pytest.mark.parametrize(
        "fn,kwargs",
        [
            (get_token_summary, {"clear": True}),
            (get_timing_summary, {"clear": True}),
            (get_pipeline_report, {"clear": True}),
        ],
    )
    @pytest.mark.anyio
    async def test_clear_writes_marker(self, tool_ctx, tmp_path, monkeypatch, fn, kwargs):
        from autoskillit.execution.session_log import read_telemetry_clear_marker

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        await fn(**kwargs)
        assert read_telemetry_clear_marker(log_dir) is not None


class TestGetLogRoot:
    def test_returns_resolved_log_dir(self, tool_ctx, tmp_path, monkeypatch):
        """_get_log_root() returns the resolved path for the configured log_dir."""
        from autoskillit.execution import resolve_log_dir
        from autoskillit.server.tools.tools_status import _get_log_root

        log_dir = tmp_path / "custom_logs"
        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(log_dir))
        result = _get_log_root()
        assert result == resolve_log_dir(str(log_dir))

    def test_returns_path_type(self, tool_ctx, tmp_path, monkeypatch):
        """_get_log_root() returns a Path, not a string."""
        from autoskillit.server.tools.tools_status import _get_log_root

        monkeypatch.setattr(tool_ctx.config.linux_tracing, "log_dir", str(tmp_path))
        assert isinstance(_get_log_root(), Path)


@pytest.mark.anyio
async def test_get_token_summary_not_contaminated_by_prior_pipeline(
    tmp_path, tool_ctx, monkeypatch
):
    """
    get_token_summary() must return only the current pipeline's data.
    Entries written by a prior pipeline run must not appear in the summary,
    even if the server was restarted with those sessions present in the log dir.
    """
    from unittest.mock import patch

    from autoskillit.server import _state
    from autoskillit.server._state import _initialize

    # tool_ctx fixture sets log_dir to tmp_path/"session_logs" — write sessions there
    # so _initialize reads from the same directory it is configured to use.
    log_dir = Path(tool_ctx.config.linux_tracing.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prior_cwd = str(tmp_path / "prior-pipeline-clone")
    # Use a current timestamp so it falls within _initialize's 24-hour recovery window
    now_ts = datetime.now(UTC).isoformat()

    # Write sessions from a PRIOR pipeline (different cwd) using direct file writes
    session_dir = log_dir / "sessions" / "sess-prior"
    session_dir.mkdir(parents=True)
    (session_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "step_name": "implement",
                "input_tokens": 9999,
                "output_tokens": 4444,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 120.0,
            }
        )
    )
    with (log_dir / "sessions.jsonl").open("a") as f:
        f.write(
            json.dumps({"dir_name": "sess-prior", "timestamp": now_ts, "cwd": prior_cwd}) + "\n"
        )

    # Simulate server startup with cross-pipeline sessions in the log dir
    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        _initialize(tool_ctx)
    # Register the _ctx mutation via monkeypatch so teardown is deterministic
    # rather than relying on coincidental identity with the tool_ctx fixture's patch.
    monkeypatch.setattr(_state, "_ctx", tool_ctx)

    # Now simulate the CURRENT pipeline recording its own step
    tool_ctx.token_log.record(
        "rectify",
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    )

    # get_token_summary uses _get_ctx() internally — tool_ctx fixture wires _ctx correctly
    result_json = await get_token_summary(clear=False, format="json")
    result = json.loads(result_json)
    step_names = [s["step_name"] for s in result["steps"]]

    assert "implement" not in step_names, (
        "Prior pipeline step 'implement' must not appear in current pipeline summary; "
        "cross-pipeline contamination detected"
    )
    assert "rectify" in step_names, "Current pipeline step must appear"
    assert len(step_names) == 1, f"Expected only current pipeline steps, got: {step_names}"


class TestOrderIdFilterOnSummaryTools:
    """Group D: order_id filter params on get_token_summary and get_timing_summary."""

    @pytest.mark.anyio
    async def test_get_token_summary_order_id_filter_isolates_issue(
        self, tool_ctx, monkeypatch
    ) -> None:
        """D-1: get_token_summary(order_id='issue-185') returns only that order's steps."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", tool_ctx)

        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("plan", usage, order_id="issue-185")
        tool_ctx.token_log.record("implement", usage, order_id="issue-186")

        result = json.loads(await get_token_summary(order_id="issue-185"))
        step_names = [s["step_name"] for s in result["steps"]]
        assert "plan" in step_names
        assert "implement" not in step_names

    @pytest.mark.anyio
    async def test_get_token_summary_no_order_id_returns_all(self, tool_ctx, monkeypatch) -> None:
        """D-2: get_token_summary() without order_id returns aggregated data for all orders."""
        from autoskillit.server import _state

        monkeypatch.setattr(_state, "_ctx", tool_ctx)

        usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        tool_ctx.token_log.record("plan", usage, order_id="issue-185")
        tool_ctx.token_log.record("implement", usage, order_id="issue-186")

        result = json.loads(await get_token_summary())
        step_names = [s["step_name"] for s in result["steps"]]
        assert "plan" in step_names
        assert "implement" in step_names

    @pytest.mark.anyio
    async def test_get_timing_summary_order_id_filter_isolates_issue(
        self, tool_ctx, monkeypatch
    ) -> None:
        """D-3: get_timing_summary(order_id='issue-185') returns only that order's steps."""
        from autoskillit.server import _state
        from autoskillit.server.tools.tools_status import get_timing_summary

        monkeypatch.setattr(_state, "_ctx", tool_ctx)

        tool_ctx.timing_log.record("plan", 10.0, order_id="issue-185")
        tool_ctx.timing_log.record("implement", 20.0, order_id="issue-186")

        result = json.loads(await get_timing_summary(order_id="issue-185"))
        step_names = [s["step_name"] for s in result["steps"]]
        assert "plan" in step_names
        assert "implement" not in step_names
