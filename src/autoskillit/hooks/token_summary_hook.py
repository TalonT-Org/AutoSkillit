#!/usr/bin/env python3
"""PostToolUse hook: append token usage summary to newly-opened PRs.

Fires after every run_skill response. If the result text contains a GitHub PR URL,
reads on-disk session logs (sessions.jsonl + per-session token_usage.json), aggregates
token usage by canonical step name, and appends a ## Token Usage Summary table to the
PR body via the GitHub REST API (gh api).

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# stdlib-only subprocess hook: import sibling modules by bare name via sys.path
# (test_hooks_are_stdlib_only). Venv tests use the autoskillit.hooks package path.
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)
_PACKAGE_DIR = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)


from _hook_settings import read_merged_hook_config  # noqa: E402
from _hook_utils import STEP_SUFFIX_RE  # noqa: E402
from _measure_aggregation import (  # noqa: E402
    CANONICAL_ACCOUNTING_FIELDS,
    MeasureRatio,
    MeasureRecord,
    SourcePair,
    TokenMeasure,
    aggregate_measures,
    measure_ratio,
)

_PR_PARTS_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")

# v1 on-disk key names (schema_version < 2). Referenced via module constants so
# data.get() call-site literals remain within TOKEN_USAGE_FILE_KEYS (AST contract).
_V1_CACHE_WRITE_KEY = "cache_creation_input_tokens"
_V1_CACHE_READ_KEY = "cache_read_input_tokens"
_TOKEN_FIELDS = (*CANONICAL_ACCOUNTING_FIELDS, "peak_context")


def _parse_pr_url_parts(pr_url: str) -> tuple[str, str, int] | None:
    """Extract (owner, repo, pr_number) from a GitHub PR URL.

    Returns None if the URL does not match the expected pattern.
    """
    m = _PR_PARTS_RE.search(pr_url)
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def _canonical(name: str) -> str:
    """Strip trailing -N numeric disambiguation suffix from a step name."""
    return STEP_SUFFIX_RE.sub("", name) if name else name


def _log_root() -> Path:
    """Return the autoskillit session log root (stdlib-only platform check)."""
    override = os.environ.get("AUTOSKILLIT_LOG_DIR")
    if override:
        return Path(override)
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/autoskillit/logs"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local/share"
    return base / "autoskillit/logs"


def _unwrap_mcp_response(tool_name: str, raw: str) -> dict | None:
    """Parse and double-unwrap a PostToolUse tool_response string.

    Returns the effective payload dict, or None if raw is not valid JSON or
    not a dict.

    For MCP tools (tool_name starts with 'mcp__'), if the outer dict has
    exactly one key 'result' whose value is a JSON string, attempts to parse
    that string as a nested dict and returns it. Falls back to returning the
    outer dict when inner parsing fails or yields a non-dict. Non-MCP tools
    return the outer dict directly.
    """
    try:
        outer = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(outer, dict):
        return None

    if (
        tool_name.startswith("mcp__")
        and list(outer.keys()) == ["result"]
        and isinstance(outer["result"], str)
    ):
        try:
            inner = json.loads(outer["result"])
            if isinstance(inner, dict):
                return inner
        except (json.JSONDecodeError, ValueError):
            pass

    return outer


def _extract_pr_url(tool_name: str, tool_response_raw: str) -> str | None:
    """Extract a GitHub PR URL from a PostToolUse tool_response string.

    Returns the URL string or None if not found.
    """
    payload = _unwrap_mcp_response(tool_name, tool_response_raw)
    if payload is None:
        return None
    result_text = payload.get("result", "")
    if not result_text or not isinstance(result_text, str):
        return None
    m = re.search(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", result_text)
    return m.group() if m else None


def _measure_record(
    entry: dict[str, Any], pair: SourcePair, *, legacy: bool = False
) -> MeasureRecord:
    return MeasureRecord(
        pair,
        {
            name: TokenMeasure.measure_from_raw(entry.get(name), legacy=legacy)
            for name in _TOKEN_FIELDS
        },
    )


def _humanize(n: Any) -> str:
    """Format a number as compact string (1.0k, 1.2M, etc.)."""
    if isinstance(n, dict):
        if n.get("state") not in {"measured", "measured_zero"}:
            return str(n.get("state", "unknown"))
        n = n.get("value")
    if n is None:
        return "unknown"
    if n == 0:
        return "0"
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _source_label(entry: dict[str, Any]) -> str:
    backend, provider = entry.get("backend"), entry.get("provider_used")
    return f"{backend}/{provider}" if backend and provider else ""


def _display_source_pair(entry: dict[str, Any]) -> tuple[str, SourcePair]:
    label = _source_label(entry)
    pair = (
        SourcePair(entry["backend"], entry["provider_used"])
        if label
        else SourcePair("unknown", "unknown")
    )
    return label, pair


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


def _read_kitchen_id(base: Path | None = None) -> str:
    """Read kitchen_id from merged hook config. Returns '' if absent or unset.

    Falls back to 'pipeline_id' key for configs written before the rename.
    """
    root = base if base is not None else Path.cwd()
    try:
        data = read_merged_hook_config(root)
        if not isinstance(data, dict):
            return ""
        return str(data.get("kitchen_id") or data.get("pipeline_id", ""))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return ""


def _extract_order_id(tool_name: str, tool_response_raw: str) -> str:
    """Extract order_id from a PostToolUse run_skill result JSON.

    Returns '' if not found.
    """
    payload = _unwrap_mcp_response(tool_name, tool_response_raw)
    if payload is None:
        return ""
    return str(payload.get("order_id", ""))


def _load_sessions(
    log_root: Path, kitchen_id: str, *, order_id: str = ""
) -> dict[str, dict[str, Any]]:
    """Load and aggregate token data from sessions matching kitchen_id or order_id.

    When order_id is non-empty, filters sessions by order_id for per-issue accuracy.
    When order_id is empty, falls back to kitchen_id filtering (existing behavior).
    Sessions missing the 'order_id' key are gracefully skipped when order_id filter is active.

    Returns a dict keyed by canonical step name, with aggregated counts.
    Preserves insertion order (Python 3.7+).
    """
    index_path = log_root / "sessions.jsonl"
    try:
        raw = index_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return {}

    aggregated: dict[str, dict[str, Any]] = {}
    index_key = "order_id" if order_id else "kitchen_id"
    index_identity = order_id or kitchen_id

    for line in raw.splitlines():
        try:
            idx = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_identity = idx.get(index_key, "")
        if index_key == "kitchen_id":
            entry_identity = entry_identity or idx.get("pipeline_id", "")
        if not index_identity or entry_identity != index_identity:
            continue

        dir_name = idx.get("dir_name", "")
        if not dir_name:
            continue

        tu_path = log_root / "sessions" / dir_name / "token_usage.json"
        try:
            data = json.loads(tu_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        raw_step = data.get("session_label", "")
        if not raw_step:
            continue

        backend = str(data.get("backend") or idx.get("backend") or "unknown")
        provider_used = str(data.get("provider_used") or backend)
        step_name = _canonical(raw_step)
        key = f"{step_name}\x1f{backend}\x1f{provider_used}"
        entry = aggregated.setdefault(
            key,
            {
                "step_name": step_name,
                "backend": backend,
                "provider_used": provider_used,
                "model": "",
                "elapsed_seconds": 0.0,
                "invocation_count": 0,
                "loc_insertions": 0,
                "loc_deletions": 0,
                "turn_count": 0,
            },
        )
        _model = data.get("model_identifier", "") or data.get("configured_model", "")
        entry["model"] = entry["model"] or _model or ""
        legacy = data.get("schema_version", 1) < 4
        raw_measures = {
            **data,
            "cache_write_tokens": data.get("cache_write_tokens", data.get(_V1_CACHE_WRITE_KEY)),
            "cache_read_tokens": data.get("cache_read_tokens", data.get(_V1_CACHE_READ_KEY)),
        }
        pair = SourcePair(backend, provider_used)
        records = [_measure_record(raw_measures, pair, legacy=legacy)]
        if entry["invocation_count"]:
            records.insert(0, _measure_record(entry, pair))
        result = aggregate_measures(records, _TOKEN_FIELDS)
        entry.update({name: result.fields[name].value.to_dict() for name in _TOKEN_FIELDS})
        _raw_timing = data.get("timing_seconds")
        entry["elapsed_seconds"] += float(_raw_timing) if _raw_timing is not None else 0.0
        entry["loc_insertions"] = entry.get("loc_insertions", 0) + (
            data.get("loc_insertions") or 0
        )
        entry["loc_deletions"] = entry.get("loc_deletions", 0) + (data.get("loc_deletions") or 0)
        entry["invocation_count"] += 1
        _raw_turns = data.get("turn_count", 0)
        if isinstance(_raw_turns, int):
            entry["turn_count"] = entry.get("turn_count", 0) + _raw_turns

    return aggregated


def _format_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format aggregated token data as a markdown ## Token Usage Summary table."""
    lines = [
        "## Token Usage Summary",
        "",
        "| Step | Model | count | uncached | output | cache_read | peak_ctx | turns | cache_write | time |",  # noqa: E501
        "|------|-------|-------|----------|--------|------------|----------|-------|-------------|------|",
    ]

    totals: dict[tuple[str, SourcePair], dict[str, Any]] = {}
    has_non_anthropic = False

    for entry in aggregated.values():
        name = entry["step_name"]
        source, pair = _display_source_pair(entry)
        record = _measure_record(entry, pair)
        if source:
            name = f"{name} ({source})"
        model = entry.get("model", "")
        if model and not model.startswith("claude-"):
            name = f"{name}*"
            has_non_anthropic = True
        count = entry.get("invocation_count", 1)
        inp = record.measures["input_tokens"].to_dict()
        out = record.measures["output_tokens"].to_dict()
        cache_rd = record.measures["cache_read_tokens"].to_dict()
        peak_ctx = record.measures["peak_context"].to_dict()
        turns = entry.get("turn_count", 0)
        cache_wr = record.measures["cache_write_tokens"].to_dict()
        elapsed = entry["elapsed_seconds"]

        lines.append(
            f"| {name} | {model} | {count} | {_humanize(inp)} | {_humanize(out)}"
            f" | {_humanize(cache_rd)} | {_humanize(peak_ctx)} | {turns} | {_humanize(cache_wr)}"
            f" | {_fmt_duration(elapsed)} |"
        )

        total = totals.setdefault((source, pair), {"records": [], "elapsed_seconds": 0.0})
        total["records"].append(record)
        total["elapsed_seconds"] += elapsed

    for (source, _pair), total in totals.items():
        result = aggregate_measures(total["records"], _TOKEN_FIELDS)
        total.update({name: result.fields[name].value.to_dict() for name in _TOKEN_FIELDS})
        label = f"Total ({source})" if source else "Total"
        lines.append(
            f"| **{label}** | | | {_humanize(total['input_tokens'])}"
            f" | {_humanize(total['output_tokens'])}"
            f" | {_humanize(total['cache_read_tokens'])}"
            f" | {_humanize(total['peak_context'])} | |"
            f" {_humanize(total['cache_write_tokens'])}"
            f" | {_fmt_duration(total['elapsed_seconds'])} |"
        )
    if has_non_anthropic:
        lines.append("")
        lines.append(r"\* *Step used a non-Anthropic provider; caching behavior may differ.*")

    return "\n".join(lines)


def _aggregate_efficiency_source_totals(
    aggregated: dict[str, dict[str, Any]],
) -> tuple[
    dict[tuple[str, SourcePair], dict[str, MeasureRatio]], dict[tuple[str, SourcePair], int]
]:
    """Aggregate efficiency measures and changed LoC by source."""
    grouped: dict[tuple[str, SourcePair], list[MeasureRecord]] = {}
    source_locs: dict[tuple[str, SourcePair], int] = {}
    for entry in aggregated.values():
        loc = entry.get("loc_insertions", 0) + entry.get("loc_deletions", 0)
        source, pair = _display_source_pair(entry)
        key = (source, pair)
        source_locs[key] = source_locs.get(key, 0) + loc
        record = _measure_record(entry, pair)
        record.measures["loc_changed"] = TokenMeasure.observed(loc)
        grouped.setdefault(key, []).append(record)
    totals = {
        source: {
            field: measure_ratio(records, token_field, "loc_changed")
            for field, token_field in (
                ("cr", "cache_read_tokens"),
                ("cw", "cache_write_tokens"),
                ("out", "output_tokens"),
            )
        }
        for source, records in grouped.items()
    }
    return totals, source_locs


def _ratio_text(ratio: MeasureRatio) -> str:
    if ratio.unknown_runs:
        return "unknown"
    return f"{ratio.value:.1f}" if ratio.value is not None else "—"


def _format_efficiency_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format aggregated token data as a markdown ## Token Efficiency table.

    Returns '' when no session has LoC data (all zero).
    """
    has_loc = any(
        e.get("loc_insertions", 0) + e.get("loc_deletions", 0) > 0 for e in aggregated.values()
    )
    if not has_loc:
        return ""

    def _ratio(record: MeasureRecord, field: str) -> str:
        measure = record.measures[field]
        if measure.value is None:
            return str(measure.state)
        return _ratio_text(measure_ratio([record], field, "loc_changed"))

    lines = [
        "## Token Efficiency",
        "",
        "| Step | LoC Changed | cache_read/LoC | cache_write/LoC | output/LoC |",
        "|------|-------------|----------------|-----------------|------------|",
    ]
    totals, source_locs = _aggregate_efficiency_source_totals(aggregated)
    for entry in aggregated.values():
        loc = entry.get("loc_insertions", 0) + entry.get("loc_deletions", 0)
        source, pair = _display_source_pair(entry)
        record = _measure_record(entry, pair)
        record.measures["loc_changed"] = TokenMeasure.observed(loc)
        label = f"{entry['step_name']} ({source})" if source else entry["step_name"]
        lines.append(
            f"| {label} | {loc} | {_ratio(record, 'cache_read_tokens')}"
            f" | {_ratio(record, 'cache_write_tokens')} | {_ratio(record, 'output_tokens')} |"
        )
    for (source, pair), total in totals.items():
        label = f"Total ({source})" if source else "Total"
        total_loc = source_locs[(source, pair)]
        lines.append(
            f"| **{label}** | **{total_loc}** | {_ratio_text(total['cr'])}"
            f" | {_ratio_text(total['cw'])} | {_ratio_text(total['out'])} |"
        )
    return "\n".join(lines)


def _format_model_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format per-model aggregate breakdown as ## Model Usage Breakdown table.

    Returns '' when all entries have no model data (legacy sessions).

    Duplicates TelemetryFormatter.format_model_table by design: hook scripts are
    stdlib-only and cannot import from autoskillit.*.
    """
    model_data: dict[tuple[str, SourcePair, str], dict[str, Any]] = {}
    for entry in aggregated.values():
        model = entry.get("model", "")
        if not model or model == "unknown":
            continue
        source, pair = _display_source_pair(entry)
        key = (source, pair, model)
        if key not in model_data:
            model_data[key] = {
                "model": f"{source}: {model}" if source else model,
                "_steps": set(),
                "records": [],
                "elapsed_seconds": 0.0,
            }
        md = model_data[key]
        md["_steps"].add(entry.get("step_name", ""))
        md["records"].append(_measure_record(entry, pair))
        md["elapsed_seconds"] += entry.get("elapsed_seconds", 0.0)

    if not model_data:
        return ""

    lines = [
        "## Model Usage Breakdown",
        "",
        "| Model | steps | uncached | output | cache_read | cache_write | time |",
        "|-------|-------|----------|--------|------------|-------------|------|",
    ]
    for md in model_data.values():
        result = aggregate_measures(md["records"], CANONICAL_ACCOUNTING_FIELDS)
        md.update(
            {name: result.fields[name].value.to_dict() for name in CANONICAL_ACCOUNTING_FIELDS}
        )
        step_count = len(md.pop("_steps"))
        lines.append(
            f"| {md['model']} | {step_count}"
            f" | {_humanize(md['input_tokens'])} | {_humanize(md['output_tokens'])}"
            f" | {_humanize(md['cache_read_tokens'])}"
            f" | {_humanize(md['cache_write_tokens'])}"
            f" | {_fmt_duration(md['elapsed_seconds'])} |"
        )
    return "\n".join(lines)


def main() -> None:
    """Entry point: read PostToolUse event from stdin, append token summary to PR."""
    try:
        data = json.loads(sys.stdin.read())
        tool_name: str = data.get("tool_name", "")
        tool_response_raw: str = data.get("tool_response", "")

        pr_url = _extract_pr_url(tool_name, tool_response_raw)
        if not pr_url:
            sys.exit(0)

        parts = _parse_pr_url_parts(pr_url)
        if not parts:
            sys.exit(0)
        owner, repo, pr_number = parts

        kitchen_id = _read_kitchen_id()
        order_id = _extract_order_id(tool_name, tool_response_raw)
        log_root = _log_root()

        aggregated = _load_sessions(log_root, kitchen_id, order_id=order_id)
        if not aggregated:
            sys.exit(0)

        # Idempotency guard: read current PR body via REST API
        view_proc = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}", "--jq", ".body"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if view_proc.returncode != 0:
            sys.stderr.write(
                f"token_summary_hook: gh api read failed (rc={view_proc.returncode}): "
                f"{view_proc.stderr.strip() if view_proc.stderr else 'no stderr'}\n"
            )
            sys.exit(0)
        if "## Token Usage Summary" in view_proc.stdout:
            sys.exit(0)

        current_body = view_proc.stdout.rstrip()
        if not current_body.strip():
            sys.stderr.write("token_summary_hook: empty PR body from gh api — aborting update\n")
            sys.exit(0)

        tables = [
            _format_table(aggregated),
            _format_efficiency_table(aggregated),
            _format_model_table(aggregated),
        ]
        new_body = current_body + "\n\n" + "\n\n".join(filter(None, tables))

        try:
            subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/{owner}/{repo}/pulls/{pr_number}",
                    "--method",
                    "PATCH",
                    "--raw-field",
                    f"body={new_body}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as cpe:
            sys.stderr.write(
                f"token_summary_hook: gh api update failed (rc={cpe.returncode}): {cpe.stderr}\n"
            )
            sys.exit(0)

    except SystemExit:
        raise
    except Exception as exc:
        sys.stderr.write(f"token_summary_hook: unexpected error: {exc}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
