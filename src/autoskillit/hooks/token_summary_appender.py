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


def _extract_pr_url(tool_name: str, tool_response_raw: str) -> str | None:
    """Extract a GitHub PR URL from a PostToolUse tool_response string.

    Replicates pretty_output._resolve_payload double-unwrap logic.
    Returns the URL string or None if not found.
    """
    try:
        outer = json.loads(tool_response_raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(outer, dict):
        return None

    result_text: str | None = None
    if (
        tool_name.startswith("mcp__")
        and list(outer.keys()) == ["result"]
        and isinstance(outer["result"], str)
    ):
        try:
            inner = json.loads(outer["result"])
            if isinstance(inner, dict):
                result_text = inner.get("result", "")
        except (json.JSONDecodeError, ValueError):
            result_text = outer["result"]
    else:
        result_text = outer.get("result", "")

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
    path = root / ".autoskillit" / ".hook_config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        return str(data.get("kitchen_id") or data.get("pipeline_id", ""))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return ""


def _extract_order_id(tool_name: str, tool_response_raw: str) -> str:
    """Extract order_id from a PostToolUse run_skill result JSON.

    Replicates the same double-unwrap logic as _extract_pr_url.
    Returns '' if not found.
    """
    try:
        outer = json.loads(tool_response_raw)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(outer, dict):
        return ""

    inner_dict: dict | None = None
    if (
        tool_name.startswith("mcp__")
        and list(outer.keys()) == ["result"]
        and isinstance(outer["result"], str)
    ):
        try:
            parsed = json.loads(outer["result"])
            if isinstance(parsed, dict):
                inner_dict = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    else:
        inner_dict = outer

    if inner_dict is None:
        return ""
    return str(inner_dict.get("order_id", ""))


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
            }
        entry = aggregated[key]
        entry["input_tokens"] += data.get("input_tokens", 0)
        entry["output_tokens"] += data.get("output_tokens", 0)
        entry["cache_creation_input_tokens"] += data.get("cache_creation_input_tokens", 0)
        entry["cache_read_input_tokens"] += data.get("cache_read_input_tokens", 0)
        _raw_timing = data.get("timing_seconds")
        entry["elapsed_seconds"] += float(_raw_timing) if _raw_timing is not None else 0.0
        entry["invocation_count"] += 1

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
                f"token_summary_appender: gh api read failed (rc={view_proc.returncode}): "
                f"{view_proc.stderr.strip() if view_proc.stderr else 'no stderr'}\n"
            )
            sys.exit(0)
        if "## Token Usage Summary" in view_proc.stdout:
            sys.exit(0)

        current_body = view_proc.stdout.rstrip()
        if not current_body.strip():
            sys.stderr.write(
                "token_summary_appender: empty PR body from gh api — aborting update\n"
            )
            sys.exit(0)

        token_table = _format_table(aggregated)
        new_body = current_body + "\n\n" + token_table

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
                f"token_summary_appender: gh api update failed"
                f" (rc={cpe.returncode}): {cpe.stderr}\n"
            )
            sys.exit(1)

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"token_summary_appender: unexpected error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
