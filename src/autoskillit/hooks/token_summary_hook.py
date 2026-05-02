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
import pathlib
import platform
import re
import subprocess
import sys
from typing import Any

# stdlib-only subprocess hook: import _fmt_primitives by bare name via sys.path
# (test_hooks_are_stdlib_only). Venv tests use the autoskillit.hooks package path.
_HOOKS_DIR = str(pathlib.Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _fmt_primitives import (  # type: ignore[import-not-found]  # noqa: E402
    _HOOK_CONFIG_PATH_COMPONENTS,
)

_SUFFIX_RE = re.compile(r"-\d+$")
_PR_PARTS_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")


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
    return _SUFFIX_RE.sub("", name) if name else name


def _log_root() -> pathlib.Path:
    """Return the autoskillit session log root (stdlib-only platform check)."""
    if platform.system() == "Darwin":
        return pathlib.Path.home() / "Library/Application Support/autoskillit/logs"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".local/share"
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


def _humanize(n: int | float | None) -> str:
    """Format a number as compact string (1.0k, 1.2M, etc.)."""
    if n is None or n == 0:
        return "0"
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


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


def _read_kitchen_id(base: pathlib.Path | None = None) -> str:
    """Read kitchen_id from hook_config.json. Returns '' if absent or unset.

    Falls back to 'pipeline_id' key for configs written before the rename.
    """
    root = base if base is not None else pathlib.Path.cwd()
    path = root.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
    log_root: pathlib.Path, kitchen_id: str, *, order_id: str = ""
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

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            idx = json.loads(line)
        except json.JSONDecodeError:
            continue

        if order_id:
            # Per-issue filtering: match on order_id; sessions without order_id are skipped
            if idx.get("order_id", "") != order_id:
                continue
        else:
            # Fallback: filter by kitchen_id (backward compat)
            entry_kitchen_id = idx.get("kitchen_id") or idx.get("pipeline_id", "")
            if not kitchen_id or entry_kitchen_id != kitchen_id:
                continue

        dir_name = idx.get("dir_name", "")
        if not dir_name:
            continue

        tu_path = log_root / "sessions" / dir_name / "token_usage.json"
        if not tu_path.exists():
            continue

        try:
            data = json.loads(tu_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        raw_step = data.get("step_name", "")
        if not raw_step:
            continue

        key = _canonical(raw_step)
        if key not in aggregated:
            aggregated[key] = {
                "step_name": key,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "elapsed_seconds": 0.0,
                "invocation_count": 0,
                "loc_insertions": 0,
                "loc_deletions": 0,
            }
        entry = aggregated[key]
        entry["input_tokens"] += data.get("input_tokens", 0)
        entry["output_tokens"] += data.get("output_tokens", 0)
        entry["cache_creation_input_tokens"] += data.get("cache_creation_input_tokens", 0)
        entry["cache_read_input_tokens"] += data.get("cache_read_input_tokens", 0)
        _raw_timing = data.get("timing_seconds")
        entry["elapsed_seconds"] += float(_raw_timing) if _raw_timing is not None else 0.0
        entry["invocation_count"] += 1
        entry["loc_insertions"] = entry.get("loc_insertions", 0) + data.get("loc_insertions", 0)
        entry["loc_deletions"] = entry.get("loc_deletions", 0) + data.get("loc_deletions", 0)

    return aggregated


def _format_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format aggregated token data as a markdown ## Token Usage Summary table."""
    lines = [
        "## Token Usage Summary",
        "",
        "| Step | uncached | output | cache_read | cache_write | count | time |",
        "|------|----------|--------|------------|-------------|-------|------|",
    ]

    total_input = 0
    total_output = 0
    total_cache_rd = 0
    total_cache_wr = 0
    total_time = 0.0

    for entry in aggregated.values():
        name = entry["step_name"]
        inp = entry["input_tokens"]
        out = entry["output_tokens"]
        cache_rd = entry["cache_read_input_tokens"]
        cache_wr = entry["cache_creation_input_tokens"]
        count = entry["invocation_count"]
        elapsed = entry["elapsed_seconds"]

        lines.append(
            f"| {name} | {_humanize(inp)} | {_humanize(out)} | {_humanize(cache_rd)}"
            f" | {_humanize(cache_wr)} | {count} | {_fmt_duration(elapsed)} |"
        )

        total_input += inp
        total_output += out
        total_cache_rd += cache_rd
        total_cache_wr += cache_wr
        total_time += elapsed

    lines.append(
        f"| **Total** | {_humanize(total_input)} | {_humanize(total_output)}"
        f" | {_humanize(total_cache_rd)} | {_humanize(total_cache_wr)}"
        f" | | {_fmt_duration(total_time)} |"
    )

    return "\n".join(lines)


def _format_efficiency_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format aggregated token data as a markdown ## Token Efficiency table.

    Returns '' when no session has LoC data (all zero).
    """
    has_loc = any(
        e.get("loc_insertions", 0) + e.get("loc_deletions", 0) > 0 for e in aggregated.values()
    )
    if not has_loc:
        return ""

    def _ratio(tokens: int, loc: int) -> str:
        return f"{tokens / loc:.1f}" if loc > 0 else "—"

    lines = [
        "## Token Efficiency",
        "",
        "| Step | LoC Changed | cache_read/LoC | cache_write/LoC | output/LoC |",
        "|------|-------------|----------------|-----------------|------------|",
    ]
    total_loc = total_cr = total_cw = total_out = 0
    for entry in aggregated.values():
        loc = entry.get("loc_insertions", 0) + entry.get("loc_deletions", 0)
        cr = entry.get("cache_read_input_tokens", 0)
        cw = entry.get("cache_creation_input_tokens", 0)
        out = entry.get("output_tokens", 0)
        lines.append(
            f"| {entry['step_name']} | {loc}"
            f" | {_ratio(cr, loc)} | {_ratio(cw, loc)} | {_ratio(out, loc)} |"
        )
        total_loc += loc
        total_cr += cr
        total_cw += cw
        total_out += out

    lines.append(
        f"| **Total** | **{total_loc}**"
        f" | {_ratio(total_cr, total_loc)} | {_ratio(total_cw, total_loc)}"
        f" | {_ratio(total_out, total_loc)} |"
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

        token_table = _format_table(aggregated)
        efficiency_table = _format_efficiency_table(aggregated)
        new_body = current_body + "\n\n" + token_table
        if efficiency_table:
            new_body += "\n\n" + efficiency_table

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
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"token_summary_hook: unexpected error: {exc}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
