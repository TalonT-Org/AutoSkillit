"""Canonical telemetry formatter — single source of truth for token & timing display.

Both MCP tools (get_token_summary, get_timing_summary, write_telemetry_files) and
the PostToolUse hook delegate formatting to this module. The hook's inline formatter
(pretty_output.py) cannot import this module (stdlib-only constraint), so it
maintains an output-equivalent inline implementation guarded by test 1g.
"""

from __future__ import annotations

from autoskillit.core import ModelTotalEntry, TerminalColumn, _render_terminal_table

_TOKEN_COLUMNS = (
    TerminalColumn("STEP", max_width=40, align="<"),
    TerminalColumn("MODEL", max_width=30, align="<"),
    TerminalColumn("COUNT", max_width=5, align=">"),
    TerminalColumn("UNCACHED", max_width=10, align=">"),
    TerminalColumn("OUTPUT", max_width=10, align=">"),
    TerminalColumn("CACHE_RD", max_width=10, align=">"),
    TerminalColumn("PEAK_CTX", max_width=10, align=">"),
    TerminalColumn("TURNS", max_width=7, align=">"),
    TerminalColumn("CACHE_WR", max_width=10, align=">"),
    TerminalColumn("TIME", max_width=8, align=">"),
)

_TIMING_COLUMNS = (
    TerminalColumn("STEP", max_width=40, align="<"),
    TerminalColumn("DURATION", max_width=10, align=">"),
    TerminalColumn("INVOCATIONS", max_width=11, align=">"),
)

_EFFICIENCY_COLUMNS = (
    TerminalColumn("STEP", max_width=40, align="<"),
    TerminalColumn("LOC_CHG", max_width=8, align=">"),
    TerminalColumn("RD/LOC", max_width=8, align=">"),
    TerminalColumn("WR/LOC", max_width=8, align=">"),
    TerminalColumn("OUT/LOC", max_width=8, align=">"),
)


_EFFICIENCY_MD_LABELS: dict[str, str] = {
    "STEP": "Step",
    "LOC_CHG": "LoC Changed",
    "RD/LOC": "cache_read/LoC",
    "WR/LOC": "cache_write/LoC",
    "OUT/LOC": "output/LoC",
}

_eff_md_headers = [_EFFICIENCY_MD_LABELS[c.label] for c in _EFFICIENCY_COLUMNS]
_EFFICIENCY_MD_HEADER = "| " + " | ".join(_eff_md_headers) + " |"
_EFFICIENCY_MD_SEP = "|" + "|".join("-" * (len(h) + 2) for h in _eff_md_headers) + "|"

_TOKEN_MD_LABELS: dict[str, str] = {
    "STEP": "Step",
    "MODEL": "Model",
    "COUNT": "count",
    "UNCACHED": "uncached",
    "OUTPUT": "output",
    "CACHE_RD": "cache_read",
    "PEAK_CTX": "peak_ctx",
    "TURNS": "turns",
    "CACHE_WR": "cache_write",
    "TIME": "time",
}

_tok_md_headers = [_TOKEN_MD_LABELS[c.label] for c in _TOKEN_COLUMNS]
_TOKEN_MD_HEADER = "| " + " | ".join(_tok_md_headers) + " |"
_TOKEN_MD_SEP = "|" + "|".join("-" * (len(h) + 2) for h in _tok_md_headers) + "|"

_TOKEN_DISPLAY_FIELDS: frozenset[str] = frozenset(
    {
        "step_name",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "invocation_count",
        "peak_context",
        "turn_count",
    }
)

_TOKEN_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "elapsed_seconds",
        "loc_insertions",
        "loc_deletions",
    }
)

_TOKEN_FIELD_TO_COLUMN: dict[str, str] = {
    "step_name": "STEP",
    "model": "MODEL",
    "input_tokens": "UNCACHED",
    "output_tokens": "OUTPUT",
    "cache_read_input_tokens": "CACHE_RD",
    "peak_context": "PEAK_CTX",
    "turn_count": "TURNS",
    "cache_creation_input_tokens": "CACHE_WR",
    "invocation_count": "COUNT",
}

_TIMING_MD_LABELS: dict[str, str] = {
    "STEP": "Step",
    "DURATION": "Duration",
    "INVOCATIONS": "Invocations",
}

_timing_md_headers = [_TIMING_MD_LABELS[c.label] for c in _TIMING_COLUMNS]
_TIMING_MD_HEADER = "| " + " | ".join(_timing_md_headers) + " |"
_TIMING_MD_SEP = "|" + "|".join("-" * (len(h) + 2) for h in _timing_md_headers) + "|"


_MODEL_COLUMNS = (
    TerminalColumn("MODEL", max_width=30, align="<"),
    TerminalColumn("STEPS", max_width=7, align=">"),
    TerminalColumn("INPUT", max_width=10, align=">"),
    TerminalColumn("OUTPUT", max_width=10, align=">"),
    TerminalColumn("CACHE_RD", max_width=10, align=">"),
    TerminalColumn("CACHE_WR", max_width=10, align=">"),
    TerminalColumn("TIME", max_width=8, align=">"),
)

_MODEL_MD_LABELS: dict[str, str] = {
    "MODEL": "Model",
    "STEPS": "steps",
    "INPUT": "uncached",
    "OUTPUT": "output",
    "CACHE_RD": "cache_read",
    "CACHE_WR": "cache_write",
    "TIME": "time",
}

_model_md_headers = [_MODEL_MD_LABELS[c.label] for c in _MODEL_COLUMNS]
_MODEL_MD_HEADER = "| " + " | ".join(_model_md_headers) + " |"
_MODEL_MD_SEP = "|" + "|".join("-" * (len(h) + 2) for h in _model_md_headers) + "|"


def _ratio(tokens: int, loc: int) -> str:
    return f"{tokens / loc:.1f}" if loc > 0 else "—"


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
            _TOKEN_MD_HEADER,
            _TOKEN_MD_SEP,
        ]
        has_non_anthropic = False
        for step in steps:
            name = step.get("step_name", "?")
            model = step.get("model", "")
            if model and not model.startswith("claude-"):
                name = f"{name}*"
                has_non_anthropic = True
            count = step.get("invocation_count", 1)
            inp = h(step.get("input_tokens", 0))
            out = h(step.get("output_tokens", 0))
            cache_rd = h(step.get("cache_read_input_tokens", 0))
            peak_ctx = h(step.get("peak_context", 0))
            turns = step.get("turn_count", 0)
            cache_wr = h(step.get("cache_creation_input_tokens", 0))
            wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
            lines.append(
                f"| {name} | {model} | {count} | {inp} | {out} | {cache_rd} | {peak_ctx}"
                f" | {turns} | {cache_wr} | {fmt_dur(wc)} |"
            )

        total_in = h(total.get("input_tokens", 0))
        total_out = h(total.get("output_tokens", 0))
        total_cache_rd = h(total.get("cache_read_input_tokens", 0))
        total_peak = h(total.get("peak_context", 0))
        total_cache_wr = h(total.get("cache_creation_input_tokens", 0))
        total_time = total.get("total_elapsed_seconds", 0.0)
        lines.append(
            f"| **Total** | | | {total_in} | {total_out} | {total_cache_rd}"
            f" | {total_peak} | | {total_cache_wr} | {fmt_dur(total_time)} |"
        )
        if has_non_anthropic:
            lines.append("")
            lines.append(r"\* *Step used a non-Anthropic provider; caching behavior may differ.*")
        return "\n".join(lines)

    @staticmethod
    def format_timing_table(steps: list[dict], total: dict) -> str:
        """Produce a markdown table for step timing."""
        fmt_dur = TelemetryFormatter._fmt_duration

        lines = [
            "## Step Timing Summary",
            "",
            _TIMING_MD_HEADER,
            _TIMING_MD_SEP,
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

        rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = []
        has_non_anthropic = False
        for step in steps:
            step_name = step.get("step_name", "?")
            model = step.get("model", "")
            if model and not model.startswith("claude-"):
                step_name = f"{step_name}*"
                has_non_anthropic = True
            rows.append(
                (
                    step_name,
                    model,
                    str(step.get("invocation_count", 1)),
                    h(step.get("input_tokens", 0)),
                    h(step.get("output_tokens", 0)),
                    h(step.get("cache_read_input_tokens", 0)),
                    h(step.get("peak_context", 0)),
                    str(step.get("turn_count", 0)),
                    h(step.get("cache_creation_input_tokens", 0)),
                    fmt_dur(step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))),
                )
            )

        total_row = (
            "Total",
            "",
            "",
            h(total.get("input_tokens", 0)),
            h(total.get("output_tokens", 0)),
            h(total.get("cache_read_input_tokens", 0)),
            h(total.get("peak_context", 0)),
            "",
            h(total.get("cache_creation_input_tokens", 0)),
            fmt_dur(total.get("total_elapsed_seconds", 0.0)),
        )

        result = _render_terminal_table(_TOKEN_COLUMNS, rows + [total_row])
        if has_non_anthropic:
            result += "\n* Step used a non-Anthropic provider; caching behavior may differ."
        return result

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
        has_non_anthropic = False
        for step in steps:
            name = step.get("step_name", "?")
            model = step.get("model", "")
            if model and not model.startswith("claude-"):
                name = f"{name}*"
                has_non_anthropic = True
            count = step.get("invocation_count", 1)
            inp = h(step.get("input_tokens", 0))
            out = h(step.get("output_tokens", 0))
            cache_rd = h(step.get("cache_read_input_tokens", 0))
            peak_ctx = h(step.get("peak_context", 0))
            cache_wr = h(step.get("cache_creation_input_tokens", 0))
            turns = step.get("turn_count", 0)
            wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
            model_tag = f" model:{model}" if model else ""
            lines.append(
                f"{name} x{count}"
                f" [uc:{inp} out:{out} cr:{cache_rd} pk:{peak_ctx} cw:{cache_wr}"
                f" turns:{turns} t:{wc:.1f}s{model_tag}]"
            )
        if total:
            lines.append("")
            lines.append(f"total_uncached: {h(total.get('input_tokens', 0))}")
            lines.append(f"total_out: {h(total.get('output_tokens', 0))}")
            lines.append(f"total_cache_read: {h(total.get('cache_read_input_tokens', 0))}")
            lines.append(f"total_peak_context: {h(total.get('peak_context', 0))}")
            lines.append(f"total_cache_write: {h(total.get('cache_creation_input_tokens', 0))}")
        if mcp_responses:
            mcp_total = mcp_responses.get("total", {})
            if mcp_total:
                lines.append("")
                lines.append(f"mcp_invocations: {mcp_total.get('total_invocations', 0)}")
                est_tokens = mcp_total.get("total_estimated_response_tokens", 0)
                lines.append(f"mcp_response_tokens: ~{h(est_tokens)}")
        if has_non_anthropic:
            lines.append("")
            lines.append("* Step used a non-Anthropic provider; caching behavior may differ.")
        return "\n".join(lines)

    @staticmethod
    def format_efficiency_table(steps: list[dict], total: dict) -> str:
        """Produce a markdown Token Efficiency table. Returns '' when all LoC=0."""
        if not any(s.get("loc_insertions", 0) + s.get("loc_deletions", 0) > 0 for s in steps):
            return ""

        lines = [
            "## Token Efficiency",
            "",
            _EFFICIENCY_MD_HEADER,
            _EFFICIENCY_MD_SEP,
        ]
        for step in steps:
            loc = step.get("loc_insertions", 0) + step.get("loc_deletions", 0)
            cr = step.get("cache_read_input_tokens", 0)
            cw = step.get("cache_creation_input_tokens", 0)
            out = step.get("output_tokens", 0)
            lines.append(
                f"| {step.get('step_name', '?')} | {loc}"
                f" | {_ratio(cr, loc)}"
                f" | {_ratio(cw, loc)} | {_ratio(out, loc)} |"
            )

        total_loc = total.get("loc_insertions", 0) + total.get("loc_deletions", 0)
        total_cr = total.get("cache_read_input_tokens", 0)
        total_cw = total.get("cache_creation_input_tokens", 0)
        total_out = total.get("output_tokens", 0)
        lines.append(
            f"| **Total** | **{total_loc}**"
            f" | {_ratio(total_cr, total_loc)}"
            f" | {_ratio(total_cw, total_loc)} | {_ratio(total_out, total_loc)} |"
        )
        return "\n".join(lines)

    @staticmethod
    def format_model_table(model_totals: list[ModelTotalEntry]) -> str:
        """Produce markdown ## Model Usage Breakdown table. Returns '' when empty."""
        if not model_totals or all(m.get("model", "") == "unknown" for m in model_totals):
            return ""
        h = TelemetryFormatter._humanize
        fmt_dur = TelemetryFormatter._fmt_duration
        lines = [
            "## Model Usage Breakdown",
            "",
            _MODEL_MD_HEADER,
            _MODEL_MD_SEP,
        ]
        for m in model_totals:
            lines.append(
                f"| {m.get('model', '')} | {m.get('step_count', 0)}"
                f" | {h(m.get('input_tokens', 0))} | {h(m.get('output_tokens', 0))}"
                f" | {h(m.get('cache_read_input_tokens', 0))}"
                f" | {h(m.get('cache_creation_input_tokens', 0))}"
                f" | {fmt_dur(m.get('elapsed_seconds', 0.0))} |"
            )
        return "\n".join(lines)

    @staticmethod
    def format_model_table_terminal(model_totals: list[ModelTotalEntry]) -> str:
        """Produce padded-column terminal model breakdown table. Returns '' when empty."""
        if not model_totals or all(m.get("model", "") == "unknown" for m in model_totals):
            return ""
        h = TelemetryFormatter._humanize
        fmt_dur = TelemetryFormatter._fmt_duration
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        for m in model_totals:
            rows.append(
                (
                    m.get("model", ""),
                    str(m.get("step_count", 0)),
                    h(m.get("input_tokens", 0)),
                    h(m.get("output_tokens", 0)),
                    h(m.get("cache_read_input_tokens", 0)),
                    h(m.get("cache_creation_input_tokens", 0)),
                    fmt_dur(m.get("elapsed_seconds", 0.0)),
                )
            )
        return _render_terminal_table(_MODEL_COLUMNS, rows)

    @staticmethod
    def format_efficiency_table_terminal(steps: list[dict], total: dict) -> str:
        """Produce a padded-column plain text efficiency table. Returns '' when all LoC=0."""
        if not any(s.get("loc_insertions", 0) + s.get("loc_deletions", 0) > 0 for s in steps):
            return ""

        rows: list[tuple[str, str, str, str, str]] = []
        for step in steps:
            loc = step.get("loc_insertions", 0) + step.get("loc_deletions", 0)
            cr = step.get("cache_read_input_tokens", 0)
            cw = step.get("cache_creation_input_tokens", 0)
            out = step.get("output_tokens", 0)
            rows.append(
                (
                    step.get("step_name", "?"),
                    str(loc),
                    _ratio(cr, loc),
                    _ratio(cw, loc),
                    _ratio(out, loc),
                )
            )

        total_loc = total.get("loc_insertions", 0) + total.get("loc_deletions", 0)
        total_row = (
            "Total",
            str(total_loc),
            _ratio(total.get("cache_read_input_tokens", 0), total_loc),
            _ratio(total.get("cache_creation_input_tokens", 0), total_loc),
            _ratio(total.get("output_tokens", 0), total_loc),
        )
        return _render_terminal_table(_EFFICIENCY_COLUMNS, rows + [total_row])
