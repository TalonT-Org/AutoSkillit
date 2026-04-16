"""Tests for TelemetryFormatter — canonical telemetry formatting."""

from __future__ import annotations

import pytest

from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_STEPS = [
    {
        "step_name": "investigate",
        "input_tokens": 7000,
        "output_tokens": 5939,
        "cache_creation_input_tokens": 8495,
        "cache_read_input_tokens": 252179,
        "invocation_count": 1,
        "wall_clock_seconds": 45.0,
        "elapsed_seconds": 40.0,
    },
    {
        "step_name": "implement",
        "input_tokens": 2031000,
        "output_tokens": 122306,
        "cache_creation_input_tokens": 280601,
        "cache_read_input_tokens": 19071323,
        "invocation_count": 3,
        "wall_clock_seconds": 492.0,
        "elapsed_seconds": 480.0,
    },
]

_TOTAL = {
    "input_tokens": 2038000,
    "output_tokens": 128245,
    "cache_creation_input_tokens": 289096,
    "cache_read_input_tokens": 19323502,
    "total_elapsed_seconds": 537.0,
}

_TIMING_STEPS = [
    {"step_name": "clone", "total_seconds": 4.0, "invocation_count": 1},
    {"step_name": "implement", "total_seconds": 492.0, "invocation_count": 3},
]

_TIMING_TOTAL = {"total_seconds": 496.0}


# ---------------------------------------------------------------------------
# format_token_table
# ---------------------------------------------------------------------------


class TestFormatTokenTable:
    def test_produces_markdown_table(self) -> None:
        result = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        assert "| Step |" in result
        assert "|---" in result
        assert "| uncached |" in result
        assert "| cache_read |" in result
        assert "| cache_write |" in result
        assert "- input_tokens:" not in result
        assert "# Token Summary" not in result

    def test_contains_step_names(self) -> None:
        result = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        assert "investigate" in result
        assert "implement" in result

    def test_contains_humanized_numbers(self) -> None:
        result = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        assert "7.0k" in result  # 7000 input_tokens
        assert "2.0M" in result  # 2031000 input_tokens

    def test_contains_bold_total_row(self) -> None:
        result = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        assert "**Total**" in result

    def test_prefers_wall_clock_seconds(self) -> None:
        """wall_clock_seconds should be used over elapsed_seconds."""
        result = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        # investigate has wall_clock=45s, elapsed=40s; should show 45s
        assert "45s" in result

    def test_falls_back_to_elapsed_when_no_wall_clock(self) -> None:
        steps = [
            {
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "invocation_count": 1,
                "elapsed_seconds": 30.0,
            },
        ]
        total = {"input_tokens": 100, "output_tokens": 50}
        result = TelemetryFormatter.format_token_table(steps, total)
        assert "30s" in result

    def test_snapshot(self) -> None:
        """Golden snapshot test for token table format."""
        steps = [
            {
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "invocation_count": 1,
                "wall_clock_seconds": 45.7,
            },
        ]
        total = {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 200,
            "total_elapsed_seconds": 45.7,
        }
        result = TelemetryFormatter.format_token_table(steps, total)
        expected = "\n".join(
            [
                "## Token Usage Summary",
                "",
                "| Step | uncached | output | cache_read | cache_write | count | time |",
                "|------|----------|--------|------------|-------------|-------|------|",
                "| plan | 1.0k | 500 | 200 | 100 | 1 | 46s |",
                "| **Total** | 1.0k | 500 | 200 | 100 | | 46s |",
            ]
        )
        assert result == expected


# ---------------------------------------------------------------------------
# format_timing_table
# ---------------------------------------------------------------------------


class TestFormatTimingTable:
    def test_produces_markdown_table(self) -> None:
        result = TelemetryFormatter.format_timing_table(_TIMING_STEPS, _TIMING_TOTAL)
        assert "| Step |" in result
        assert "|---" in result
        assert "- total_seconds:" not in result
        assert "# Timing Summary" not in result

    def test_contains_step_names(self) -> None:
        result = TelemetryFormatter.format_timing_table(_TIMING_STEPS, _TIMING_TOTAL)
        assert "clone" in result
        assert "implement" in result

    def test_formats_duration(self) -> None:
        result = TelemetryFormatter.format_timing_table(_TIMING_STEPS, _TIMING_TOTAL)
        assert "4s" in result  # 4.0 seconds
        assert "8m 12s" in result  # 492.0 seconds

    def test_contains_bold_total_row(self) -> None:
        result = TelemetryFormatter.format_timing_table(_TIMING_STEPS, _TIMING_TOTAL)
        assert "**Total**" in result


# ---------------------------------------------------------------------------
# format_token_table_terminal
# ---------------------------------------------------------------------------


class TestFormatTokenTableTerminal:
    def test_contains_no_markdown_syntax(self) -> None:
        """Terminal token table must not contain Markdown pipe tables or headings."""
        result = TelemetryFormatter.format_token_table_terminal(_STEPS, _TOTAL)
        assert "<!--" not in result
        assert "|---" not in result
        assert not any(line.lstrip().startswith("## ") for line in result.splitlines())
        assert "**Total**" not in result

    def test_contains_step_data(self) -> None:
        """Terminal token table preserves all data from the Markdown version."""
        result = TelemetryFormatter.format_token_table_terminal(_STEPS, _TOTAL)
        assert "investigate" in result
        assert "7.0k" in result


# ---------------------------------------------------------------------------
# format_timing_table_terminal
# ---------------------------------------------------------------------------


class TestFormatTimingTableTerminal:
    def test_contains_no_markdown_syntax(self) -> None:
        """Terminal timing table must not contain Markdown pipe tables or headings."""
        result = TelemetryFormatter.format_timing_table_terminal(_TIMING_STEPS, _TIMING_TOTAL)
        assert "<!--" not in result
        assert "|---" not in result
        assert not any(line.lstrip().startswith("## ") for line in result.splitlines())
        assert "**Total**" not in result

    def test_contains_step_data(self) -> None:
        """Terminal timing table preserves all data from the Markdown version."""
        result = TelemetryFormatter.format_timing_table_terminal(_TIMING_STEPS, _TIMING_TOTAL)
        assert "clone" in result
        assert "implement" in result


def test_format_token_table_terminal_output_has_leading_indent() -> None:
    """_render_terminal_table prefixes each line with 2 spaces.

    The current inline implementation does not add leading spaces. After
    migration to _render_terminal_table, every output line must start with
    two spaces.
    """
    result = TelemetryFormatter.format_token_table_terminal(_STEPS, _TOTAL)
    for line in result.splitlines():
        assert line.startswith("  "), f"Expected 2-space indent, got: {line!r}"


def test_format_timing_table_terminal_output_has_leading_indent() -> None:
    """_render_terminal_table prefixes each line with 2 spaces."""
    result = TelemetryFormatter.format_timing_table_terminal(_TIMING_STEPS, _TIMING_TOTAL)
    for line in result.splitlines():
        assert line.startswith("  "), f"Expected 2-space indent, got: {line!r}"


# ---------------------------------------------------------------------------
# format_compact_kv
# ---------------------------------------------------------------------------


def test_terminal_table_has_four_token_columns() -> None:
    """Terminal table must show UNCACHED, CACHE_RD, CACHE_WR column headers."""
    result = TelemetryFormatter.format_token_table_terminal(_STEPS, _TOTAL)
    assert "UNCACHED" in result
    assert "CACHE_RD" in result
    assert "CACHE_WR" in result


def test_compact_kv_four_token_prefixes() -> None:
    """Compact KV format must use uc:, cr:, cw: prefixes and split totals."""
    result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
    assert "uc:" in result
    assert "cr:" in result
    assert "cw:" in result
    assert "total_uncached:" in result
    assert "total_cache_read:" in result
    assert "total_cache_write:" in result
    assert "in:" not in result
    assert " cached:" not in result
    assert "total_in:" not in result
    assert "total_cached:" not in result


class TestFormatCompactKv:
    def test_produces_compact_lines(self) -> None:
        result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
        assert "## token_summary" in result
        assert "investigate x1" in result
        assert "implement x3" in result

    def test_includes_humanized_tokens(self) -> None:
        result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
        assert "uc:7.0k" in result
        assert "out:5.9k" in result

    def test_includes_total_lines(self) -> None:
        result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
        assert "total_uncached:" in result
        assert "total_out:" in result
        assert "total_cache_read:" in result
        assert "total_cache_write:" in result

    def test_prefers_wall_clock_seconds(self) -> None:
        result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
        # investigate: wall_clock=45.0, elapsed=40.0 → t:45.0s
        assert "t:45.0s" in result

    def test_includes_mcp_responses(self) -> None:
        mcp = {"total": {"total_invocations": 42, "total_estimated_response_tokens": 5000}}
        result = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL, mcp_responses=mcp)
        assert "mcp_invocations: 42" in result
        assert "mcp_response_tokens: ~5.0k" in result

    def test_consistent_data_with_token_table(self) -> None:
        """Both formats contain the same step names and data."""
        table = TelemetryFormatter.format_token_table(_STEPS, _TOTAL)
        compact = TelemetryFormatter.format_compact_kv(_STEPS, _TOTAL)
        for step in _STEPS:
            assert step["step_name"] in table
            assert step["step_name"] in compact


# ---------------------------------------------------------------------------
# _humanize
# ---------------------------------------------------------------------------


class TestHumanize:
    def test_none_returns_zero(self) -> None:
        assert TelemetryFormatter._humanize(None) == "0"

    def test_zero_returns_zero(self) -> None:
        assert TelemetryFormatter._humanize(0) == "0"

    def test_small_number(self) -> None:
        assert TelemetryFormatter._humanize(500) == "500"

    def test_thousands(self) -> None:
        assert TelemetryFormatter._humanize(45200) == "45.2k"

    def test_millions(self) -> None:
        assert TelemetryFormatter._humanize(1200000) == "1.2M"


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------


class TestFmtDuration:
    def test_seconds(self) -> None:
        assert TelemetryFormatter._fmt_duration(4.0) == "4s"

    def test_minutes(self) -> None:
        assert TelemetryFormatter._fmt_duration(492.0) == "8m 12s"

    def test_hours(self) -> None:
        assert TelemetryFormatter._fmt_duration(3720.0) == "1h 2m"
