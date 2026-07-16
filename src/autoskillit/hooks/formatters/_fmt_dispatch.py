"""Dispatch-tool formatters for the pretty_output split.

Hosts the per-tool formatter for ``dispatch_food_truck``. Stdlib-only at runtime.

This module is split from ``_fmt_execution.py`` to keep the file size budget
(REQ-FILE-001) below the 329-line ceiling. Artifact-backed response projections
retain a bounded nested view while the central formatter exposes the full path.
"""

from __future__ import annotations

import json

from _fmt_primitives import (  # type: ignore[import-not-found]
    _CHECK_MARK,
    _CROSS_MARK,
    _fmt_tokens,
)


def _fmt_dispatch_food_truck(data: dict, _pipeline: bool) -> str:
    """Format dispatch_food_truck result as Markdown-KV.

    Renders both success (DispatchCompleted) and error (DispatchRejected /
    fleet_error) response shapes — discriminates on ``data.get("kind")``.
    Nested structured fields are rendered from the inline projection. The
    central spill notice exposes the full response artifact when present.
    """
    success = data.get("success", False)
    mark = _CHECK_MARK if success else _CROSS_MARK
    status = "OK" if success else "FAIL"
    kind = data.get("kind")

    lines = [f"## dispatch_food_truck {mark} {status}", ""]

    if kind == "rejected" or (not success and kind is None):
        # Error path — DispatchRejected / fleet_error shape
        error = data.get("error", "unknown")
        lines.append(f"error: {error}")
        user_msg = data.get("user_visible_message", "")
        if user_msg:
            lines.append(f"user_visible_message: {user_msg}")
        details = data.get("details")
        if details:
            lines.append("")
            lines.append("details:")
            lines.append(json.dumps(details, indent=2))
        dispatch_id = data.get("dispatch_id", "")
        if dispatch_id:
            lines.append(f"dispatch_id: {dispatch_id}")
        missing_steps = data.get("missing_provider_steps")
        if missing_steps:
            lines.append(f"missing_provider_steps: {missing_steps}")
        escape_hatch = data.get("escape_hatch", "")
        if escape_hatch:
            lines.append(f"escape_hatch: {escape_hatch}")
        return "\n".join(lines)

    # Success path — DispatchCompleted shape
    lines.append(f"success: {success}")
    dispatch_status = data.get("dispatch_status", "")
    if dispatch_status:
        lines.append(f"dispatch_status: {dispatch_status}")
    dispatch_id = data.get("dispatch_id", "")
    if dispatch_id:
        lines.append(f"dispatch_id: {dispatch_id}")
    dispatched_session_id = data.get("dispatched_session_id", "")
    if dispatched_session_id:
        lines.append(f"dispatched_session_id: {dispatched_session_id}")
    reason = data.get("reason", "")
    if reason:
        lines.append(f"reason: {reason}")

    token_usage = data.get("token_usage")
    if isinstance(token_usage, dict) and token_usage:
        lines.append("")
        lines.append("token_usage:")
        inp = _fmt_tokens(token_usage.get("input_tokens", token_usage.get("input")))
        out = _fmt_tokens(token_usage.get("output_tokens", token_usage.get("output")))
        lines.append(f"  input: {inp}")
        lines.append(f"  output: {out}")
        cr = token_usage.get("cache_read_tokens", 0)
        if cr:
            lines.append(f"  cache_read: {_fmt_tokens(cr)}")
        cw = token_usage.get("cache_write_tokens", 0)
        if cw:
            lines.append(f"  cache_write: {_fmt_tokens(cw)}")

    l3_payload = data.get("l3_payload")
    if l3_payload is not None:
        lines.append("")
        lines.append("l3_payload:")
        lines.append(json.dumps(l3_payload, indent=2))

    l3_parse_source = data.get("l3_parse_source", "")
    if l3_parse_source:
        lines.append(f"l3_parse_source: {l3_parse_source}")

    lifespan_started = data.get("lifespan_started")
    if lifespan_started is not None:
        lines.append(f"lifespan_started: {lifespan_started}")

    l3_raw_body = data.get("l3_raw_body")
    if l3_raw_body:
        lines.append("")
        lines.append("### l3_raw_body")
        lines.append(l3_raw_body)

    l3_parse_error = data.get("l3_parse_error")
    if l3_parse_error:
        lines.append("")
        lines.append("### l3_parse_error")
        lines.append(l3_parse_error)

    resume_checkpoint = data.get("resume_checkpoint")
    if resume_checkpoint:
        lines.append("")
        lines.append("### resume_checkpoint")
        lines.append(json.dumps(resume_checkpoint, indent=2))

    health_report = data.get("health_report")
    if health_report is not None:
        lines.append("")
        lines.append("### health_report")
        lines.append(json.dumps(health_report, indent=2))

    stderr = (data.get("stderr") or "").strip()
    if stderr:
        lines.append("")
        lines.append("### stderr")
        lines.append(stderr)

    elapsed = data.get("elapsed_seconds")
    if elapsed is not None:
        lines.append(f"elapsed_seconds: {elapsed}")

    return "\n".join(lines)


_FMT_DISPATCH_FOOD_TRUCK_RENDERED: frozenset[str] = frozenset(
    {
        "success",
        "dispatch_status",
        "dispatch_id",
        "dispatched_session_id",
        "reason",
        "token_usage",
        "l3_payload",
        "l3_parse_source",
        "lifespan_started",
        "l3_raw_body",
        "l3_parse_error",
        "resume_checkpoint",
        "health_report",
        "stderr",
        "elapsed_seconds",
        "error",
        "user_visible_message",
        "details",
        "missing_provider_steps",
        "escape_hatch",
    }
)
_FMT_DISPATCH_FOOD_TRUCK_SUPPRESSED: frozenset[str] = frozenset({"kind"})

_FMT_DISPATCH_COMPLETED_REQUIRED: frozenset[str] = frozenset(
    {
        "dispatch_status",
        "dispatch_id",
        "dispatched_session_id",
        "reason",
    }
)
_FMT_DISPATCH_REJECTED_REQUIRED: frozenset[str] = frozenset(
    {
        "error",
        "user_visible_message",
    }
)
