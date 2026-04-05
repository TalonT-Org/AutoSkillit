"""Canonical telemetry formatter — single source of truth for token & timing display.

Both MCP tools (get_token_summary, get_timing_summary, write_telemetry_files) and
the PostToolUse hook delegate formatting to this module. The hook's inline formatter
(pretty_output.py) cannot import this module (stdlib-only constraint), so it
maintains an output-equivalent inline implementation guarded by test 1g.
"""

from __future__ import annotations

from autoskillit.core import TerminalColumn, _render_terminal_table

_TOKEN_COLUMNS = (
    TerminalColumn("STEP", max_width=40, align="<"),
    TerminalColumn("UNCACHED", max_width=10, align=">"),
    TerminalColumn("OUTPUT", max_width=10, align=">"),
    TerminalColumn("CACHE_RD", max_width=10, align=">"),
    TerminalColumn("CACHE_WR", max_width=10, align=">"),
    TerminalColumn("COUNT", max_width=7, align=">"),
    TerminalColumn("TIME", max_width=8, align=">"),
)

_TIMING_COLUMNS = (
    TerminalColumn("STEP", max_width=40, align="<"),
    TerminalColumn("DURATION", max_width=10, align=">"),
    TerminalColumn("INVOCATIONS", max_width=11, align=">"),
)


class TelemetryFormatter:
    """Stateless formatter for token and timing telemetry data."""

    @staticmethod
    def _humanize(n: int | float | None) -> str:
        """Format a number as compact string (45.2k, 1.2M, etc.)."""
        if n is None or n == 0:
            return "0"
        if not isinstance(n, (int, float)):
            return "0"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        """Format seconds as human-readable duration."""
        seconds = float(seconds)
        if seconds < 60:
            return f"{seconds:.0f}s"
        if seconds < 3600:
            m, s = divmod(int(seconds), 60)
            return f"{m}m {s}s"
        h, remainder = divmod(int(seconds), 3600)
        m = remainder // 60
        return f"{h}h {m}m"

    @staticmethod
    def format_token_table(steps: list[dict], total: dict) -> str:
        """Produce a markdown table for token usage with timing column."""
        h = TelemetryFormatter._humanize
        fmt_dur = TelemetryFormatter._fmt_duration

        lines = [
            "## Token Usage Summary",
            "",
            "| Step | uncached | output | cache_read | cache_write | count | time |",
            "|------|----------|--------|------------|-------------|-------|------|",
        ]
        for step in steps:
            name = step.get("step_name", "?")
            inp = h(step.get("input_tokens", 0))
            out = h(step.get("output_tokens", 0))
            cache_rd = h(step.get("cache_read_input_tokens", 0))
            cache_wr = h(step.get("cache_creation_input_tokens", 0))
            count = step.get("invocation_count", 1)
            wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
            lines.append(
                f"| {name} | {inp} | {out} | {cache_rd} | {cache_wr} | {count} | {fmt_dur(wc)} |"
            )

        total_in = h(total.get("input_tokens", 0))
        total_out = h(total.get("output_tokens", 0))
        total_cached_rd = h(total.get("cache_read_input_tokens", 0))
        total_cached_wr = h(total.get("cache_creation_input_tokens", 0))
        total_time = total.get("total_elapsed_seconds", 0.0)
        lines.append(
            f"| **Total** | {total_in} | {total_out} | {total_cached_rd}"
            f" | {total_cached_wr} | | {fmt_dur(total_time)} |"
        )
        return "\n".join(lines)

    @staticmethod
    def format_timing_table(steps: list[dict], total: dict) -> str:
        """Produce a markdown table for step timing."""
        fmt_dur = TelemetryFormatter._fmt_duration

        lines = [
            "## Step Timing Summary",
            "",
            "| Step | Duration | Invocations |",
            "|------|----------|-------------|",
        ]
        for step in steps:
            name = step.get("step_name", "?")
            dur = fmt_dur(step.get("total_seconds", 0.0))
            count = step.get("invocation_count", 1)
            lines.append(f"| {name} | {dur} | {count} |")

        total_seconds = total.get("total_seconds", 0.0)
        lines.append(f"| **Total** | {fmt_dur(total_seconds)} | |")
        return "\n".join(lines)

    @staticmethod
    def format_token_table_terminal(steps: list[dict], total: dict) -> str:
        """Produce a padded-column plain text table for token usage."""
        h = TelemetryFormatter._humanize
        fmt_dur = TelemetryFormatter._fmt_duration

        rows: list[tuple[str, str, str, str, str, str, str]] = []
        for step in steps:
            rows.append(
                (
                    step.get("step_name", "?"),
                    h(step.get("input_tokens", 0)),
                    h(step.get("output_tokens", 0)),
                    h(step.get("cache_read_input_tokens", 0)),
                    h(step.get("cache_creation_input_tokens", 0)),
                    str(step.get("invocation_count", 1)),
                    fmt_dur(step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))),
                )
            )

        total_row = (
            "Total",
            h(total.get("input_tokens", 0)),
            h(total.get("output_tokens", 0)),
            h(total.get("cache_read_input_tokens", 0)),
            h(total.get("cache_creation_input_tokens", 0)),
            "",
            fmt_dur(total.get("total_elapsed_seconds", 0.0)),
        )

        return _render_terminal_table(_TOKEN_COLUMNS, rows + [total_row])

    @staticmethod
    def format_timing_table_terminal(steps: list[dict], total: dict) -> str:
        """Produce a padded-column plain text table for step timing."""
        fmt_dur = TelemetryFormatter._fmt_duration

        rows: list[tuple[str, str, str]] = []
        for step in steps:
            rows.append(
                (
                    step.get("step_name", "?"),
                    fmt_dur(step.get("total_seconds", 0.0)),
                    str(step.get("invocation_count", 1)),
                )
            )
        total_row = ("Total", fmt_dur(total.get("total_seconds", 0.0)), "")

        return _render_terminal_table(_TIMING_COLUMNS, rows + [total_row])

    @staticmethod
    def format_compact_kv(
        steps: list[dict], total: dict, mcp_responses: dict | None = None
    ) -> str:
        """Produce compact Markdown-KV one-liners for PostToolUse hook display."""
        h = TelemetryFormatter._humanize

        lines = ["## token_summary", ""]
        for step in steps:
            name = step.get("step_name", "?")
            count = step.get("invocation_count", 1)
            inp = h(step.get("input_tokens", 0))
            out = h(step.get("output_tokens", 0))
            cache_rd = h(step.get("cache_read_input_tokens", 0))
            cache_wr = h(step.get("cache_creation_input_tokens", 0))
            wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
            lines.append(
                f"{name} x{count} [uc:{inp} out:{out} cr:{cache_rd} cw:{cache_wr} t:{wc:.1f}s]"
            )
        if total:
            lines.append("")
            lines.append(f"total_uncached: {h(total.get('input_tokens', 0))}")
            lines.append(f"total_out: {h(total.get('output_tokens', 0))}")
            lines.append(f"total_cache_read: {h(total.get('cache_read_input_tokens', 0))}")
            lines.append(f"total_cache_write: {h(total.get('cache_creation_input_tokens', 0))}")
        if mcp_responses:
            mcp_total = mcp_responses.get("total", {})
            if mcp_total:
                lines.append("")
                lines.append(f"mcp_invocations: {mcp_total.get('total_invocations', 0)}")
                est_tokens = mcp_total.get("total_estimated_response_tokens", 0)
                lines.append(f"mcp_response_tokens: ~{h(est_tokens)}")
        return "\n".join(lines)
