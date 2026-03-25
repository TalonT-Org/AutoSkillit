#!/usr/bin/env python3
"""PostToolUse hook: append token usage summary to newly-opened PRs.

Fires after every run_skill response. If the result text contains a GitHub PR URL,
reads on-disk session logs (sessions.jsonl + per-session token_usage.json), aggregates
token usage by canonical step name, and appends a ## Token Usage Summary table to the
PR body via gh pr edit.

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
    return str(int(n))


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


def _load_sessions(log_root: pathlib.Path, cwd: str) -> dict[str, dict[str, Any]]:
    """Load and aggregate token data from sessions matching cwd.

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

        if idx.get("cwd") != cwd:
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
        entry["elapsed_seconds"] += float(data.get("timing_seconds", 0.0))
        entry["invocation_count"] += 1

    return aggregated


def _format_table(aggregated: dict[str, dict[str, Any]]) -> str:
    """Format aggregated token data as a markdown ## Token Usage Summary table."""
    lines = [
        "## Token Usage Summary",
        "",
        "| Step | input | output | cached | count | time |",
        "|------|-------|--------|--------|-------|------|",
    ]

    total_input = 0
    total_output = 0
    total_cached = 0
    total_time = 0.0

    for entry in aggregated.values():
        name = entry["step_name"]
        inp = entry["input_tokens"]
        out = entry["output_tokens"]
        cached = entry["cache_read_input_tokens"] + entry["cache_creation_input_tokens"]
        count = entry["invocation_count"]
        elapsed = entry["elapsed_seconds"]

        lines.append(
            f"| {name} | {_humanize(inp)} | {_humanize(out)} | {_humanize(cached)}"
            f" | {count} | {_fmt_duration(elapsed)} |"
        )

        total_input += inp
        total_output += out
        total_cached += cached
        total_time += elapsed

    lines.append(
        f"| **Total** | {_humanize(total_input)} | {_humanize(total_output)}"
        f" | {_humanize(total_cached)} | | {_fmt_duration(total_time)} |"
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

        cwd = os.getcwd()
        log_root = _log_root()

        aggregated = _load_sessions(log_root, cwd)
        if not aggregated:
            sys.exit(0)

        # Idempotency guard: check if PR body already has the summary
        view_proc = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "body", "--jq", ".body"],
            capture_output=True,
            text=True,
        )
        if view_proc.returncode != 0:
            sys.exit(0)
        if "## Token Usage Summary" in view_proc.stdout:
            sys.exit(0)

        current_body = view_proc.stdout.rstrip()
        if not current_body.strip():
            sys.stderr.write(
                "token_summary_appender: empty PR body from gh pr view — aborting edit\n"
            )
            sys.exit(0)

        token_table = _format_table(aggregated)
        new_body = current_body + "\n\n" + token_table

        try:
            subprocess.run(["gh", "pr", "edit", pr_url, "--body", new_body], check=True)
        except subprocess.CalledProcessError as cpe:
            sys.stderr.write(
                f"token_summary_appender: gh pr edit failed (rc={cpe.returncode}): {cpe.stderr}\n"
            )
            sys.exit(1)

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"token_summary_appender: unexpected error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
