"""Status/summary tool formatters for the pretty_output split.

Hosts the per-tool formatters for ``get_token_summary``, ``get_timing_summary``,
``kitchen_status``, and ``clone_repo``. Stdlib-only at runtime.
"""

from __future__ import annotations

from _fmt_primitives import (  # type: ignore[import-not-found]
    _CHECK_MARK,
    _CROSS_MARK,
    _WARN_MARK,
    _fmt_tokens,
)


def _fmt_get_token_summary(data: dict, _pipeline: bool) -> str:
    """Format get_token_summary compact Markdown-KV output.

    This formatter receives only the JSON dict payload (format="json").
    When format="table" is used, the tool returns a pre-formatted markdown
    string. _resolve_payload() detects this as a _PlainTextPayload and routes
    it through _PLAIN_TEXT_FORMATTERS (pass-through), so this function is
    never called for the table format.
    """
    lines = ["## token_summary", ""]
    steps = data.get("steps", [])
    for step in steps:
        name = step.get("step_name", "?")
        count = step.get("invocation_count", 1)
        inp = _fmt_tokens(step.get("input_tokens", 0))
        out = _fmt_tokens(step.get("output_tokens", 0))
        cache_rd = _fmt_tokens(step.get("cache_read_input_tokens", 0))
        cache_wr = _fmt_tokens(step.get("cache_creation_input_tokens", 0))
        wc = step.get("wall_clock_seconds", step.get("elapsed_seconds", 0.0))
        lines.append(
            f"{name} x{count} [uc:{inp} out:{out} cr:{cache_rd} cw:{cache_wr} t:{wc:.1f}s]"
        )
    total = data.get("total", {})
    if total:
        lines.append("")
        lines.append(f"total_uncached: {_fmt_tokens(total.get('input_tokens', 0))}")
        lines.append(f"total_out: {_fmt_tokens(total.get('output_tokens', 0))}")
        lines.append(f"total_cache_read: {_fmt_tokens(total.get('cache_read_input_tokens', 0))}")
        lines.append(
            f"total_cache_write: {_fmt_tokens(total.get('cache_creation_input_tokens', 0))}"
        )
    mcp = data.get("mcp_responses", {})
    mcp_total = mcp.get("total", {})
    if mcp_total:
        lines.append("")
        lines.append(f"mcp_invocations: {mcp_total.get('total_invocations', 0)}")
        est_tokens = mcp_total.get("total_estimated_response_tokens", 0)
        lines.append(f"mcp_response_tokens: ~{_fmt_tokens(est_tokens)}")
    return "\n".join(lines)


def _fmt_get_timing_summary(data: dict, _pipeline: bool) -> str:
    """Format get_timing_summary compact Markdown-KV output.

    This formatter receives only the JSON dict payload (format="json").
    When format="table" is used, the tool returns a pre-formatted markdown
    string. _resolve_payload() detects this as a _PlainTextPayload and routes
    it through _PLAIN_TEXT_FORMATTERS (pass-through), so this function is
    never called for the table format.

    Each step becomes: name xN [dur:Xs]
    """
    lines = ["## timing_summary", ""]
    steps = data.get("steps", [])
    for step in steps:
        name = step.get("step_name", "?")
        count = step.get("invocation_count", 1)
        secs = step.get("total_seconds", 0.0)
        if secs < 60:
            dur = f"{secs:.0f}s"
        elif secs < 3600:
            m, s = divmod(int(secs), 60)
            dur = f"{m}m {s}s"
        else:
            h, remainder = divmod(int(secs), 3600)
            m = remainder // 60
            dur = f"{h}h {m}m"
        lines.append(f"{name} x{count} [dur:{dur}]")
    total = data.get("total", {})
    if total:
        total_secs = total.get("total_seconds", 0.0)
        if total_secs < 60:
            total_dur = f"{total_secs:.0f}s"
        elif total_secs < 3600:
            m, s = divmod(int(total_secs), 60)
            total_dur = f"{m}m {s}s"
        else:
            h, remainder = divmod(int(total_secs), 3600)
            m = remainder // 60
            total_dur = f"{h}h {m}m"
        lines.append("")
        lines.append(f"total: {total_dur}")
    return "\n".join(lines)


def _fmt_kitchen_status(data: dict, _pipeline: bool) -> str:
    """Format kitchen_status as Markdown-KV."""
    success = not data.get("error")
    mark = _CHECK_MARK if success else _CROSS_MARK
    enabled = data.get("tools_enabled", False)
    gate_str = "OPEN" if enabled else "CLOSED"
    lines = [f"## kitchen_status {mark} {gate_str}", ""]
    for key in (
        "package_version",
        "plugin_json_version",
        "versions_match",
        "tools_enabled",
        "token_usage_verbosity",
        "quota_guard_enabled",
        "github_token_configured",
        "github_default_repo",
    ):
        if key in data:
            lines.append(f"{key}: {data[key]}")
    warning = data.get("warning")
    if warning:
        lines.extend(["", f"warning: {warning}"])
    return "\n".join(lines)


def _fmt_clone_repo(data: dict, _pipeline: bool) -> str:
    """Format clone_repo result as Markdown-KV."""
    is_warning = "uncommitted_changes" in data or "unpublished_branch" in data
    has_error = "error" in data

    if is_warning:
        mark = _WARN_MARK
        status = "WARNING"
    elif has_error:
        mark = _CROSS_MARK
        status = "FAIL"
    else:
        mark = _CHECK_MARK
        status = "OK"

    lines = [f"## clone_repo {mark} {status}", ""]
    for key, val in data.items():
        if isinstance(val, (dict, list)):
            continue
        lines.append(f"{key}: {val}")
    return "\n".join(lines)
