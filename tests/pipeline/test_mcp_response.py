"""Tests for autoskillit.pipeline.mcp_response — MCP tool response size tracking."""

from __future__ import annotations

from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog, McpResponseEntry


class TestMcpResponseEntry:
    def test_defaults_are_zero(self):
        e = McpResponseEntry(tool_name="run_skill")
        assert e.response_bytes == 0
        assert e.estimated_response_tokens == 0
        assert e.invocation_count == 0

    def test_to_dict_contains_all_fields(self):
        e = McpResponseEntry(
            tool_name="run_skill",
            response_bytes=400,
            estimated_response_tokens=100,
            invocation_count=2,
        )
        d = e.to_dict()
        assert d == {
            "tool_name": "run_skill",
            "response_bytes": 400,
            "estimated_response_tokens": 100,
            "invocation_count": 2,
        }


class TestDefaultMcpResponseLog:
    def test_empty_report(self):
        log = DefaultMcpResponseLog()
        assert log.get_report() == []

    def test_record_single_call(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "a" * 400)
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["tool_name"] == "run_skill"
        assert report[0]["response_bytes"] == 400
        assert report[0]["estimated_response_tokens"] == 100  # 400 // 4
        assert report[0]["invocation_count"] == 1

    def test_record_accumulates_same_tool(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "a" * 400)
        log.record("run_skill", "b" * 200)
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["response_bytes"] == 600
        assert report[0]["estimated_response_tokens"] == 150  # 600 // 4
        assert report[0]["invocation_count"] == 2

    def test_record_separate_tools(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "a" * 400)
        log.record("load_recipe", "b" * 800)
        report = log.get_report()
        assert len(report) == 2
        names = {r["tool_name"] for r in report}
        assert names == {"run_skill", "load_recipe"}

    def test_token_estimate_uses_integer_division(self):
        log = DefaultMcpResponseLog()
        log.record("tool", "a" * 7)  # 7 bytes → 7 // 4 = 1 token
        report = log.get_report()
        assert report[0]["estimated_response_tokens"] == 1

    def test_threshold_not_exceeded_returns_false(self):
        log = DefaultMcpResponseLog()
        exceeded = log.record("tool", "a" * 40, alert_threshold_tokens=100)
        assert exceeded is False

    def test_threshold_exceeded_returns_true(self):
        log = DefaultMcpResponseLog()
        # 40000 chars = 40000 bytes → 10000 estimated tokens > 2000 threshold
        exceeded = log.record("load_recipe", "x" * 40000, alert_threshold_tokens=2000)
        assert exceeded is True

    def test_threshold_zero_never_triggers(self):
        log = DefaultMcpResponseLog()
        exceeded = log.record("tool", "x" * 100000, alert_threshold_tokens=0)
        assert exceeded is False

    def test_compute_total_aggregates_all_entries(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "a" * 400)
        log.record("load_recipe", "b" * 800)
        total = log.compute_total()
        assert total["total_response_bytes"] == 1200
        assert total["total_estimated_response_tokens"] == 300  # 1200 // 4
        assert total["total_invocations"] == 2

    def test_compute_total_empty(self):
        log = DefaultMcpResponseLog()
        total = log.compute_total()
        assert total["total_response_bytes"] == 0
        assert total["total_estimated_response_tokens"] == 0
        assert total["total_invocations"] == 0

    def test_clear_resets_state(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "data")
        log.clear()
        assert log.get_report() == []
        assert log.compute_total()["total_invocations"] == 0

    def test_get_report_is_defensive_copy(self):
        log = DefaultMcpResponseLog()
        log.record("run_skill", "data")
        report = log.get_report()
        report.clear()  # mutating the returned list
        assert len(log.get_report()) == 1  # original log unaffected
