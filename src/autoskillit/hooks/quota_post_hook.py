#!/usr/bin/env python3
"""PostToolUse hook: quota warning after run_skill execution.

Fires after run_skill completes and checks whether the cached binding marks
``should_block=True``. When set, replaces the tool output with a compact
result summary + quota warning + sleep instruction via updatedMCPToolOutput.

This script is stdlib-only so it can run under any Python interpreter without
requiring the autoskillit package to be importable.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Sibling-import bootstrap: hooks run as ``python3 /path/to/quota_post_check.py``
# subprocesses outside the autoskillit venv (test_hooks_are_stdlib_only).
# Placing the script's directory first on sys.path lets the bare-name import
# below resolve to the shared stdlib-only settings module in both subprocess
# and package-mode invocations.
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import resolve_quota_settings  # type: ignore[import-not-found]  # noqa: E402

_AUTOSKILLIT_LOG_DIR_ENV = "AUTOSKILLIT_LOG_DIR"

# Emitted in post-tool output; referenced by orchestrator prompt and sous-chef SKILL.md.
QUOTA_POST_WARNING_TRIGGER: str = "--- QUOTA WARNING ---"


def _read_quota_cache(cache_path_str: str, max_age: int) -> dict | None:
    """Read quota cache file. Returns parsed data or None if missing/stale/corrupt."""
    cache_path = Path(cache_path_str).expanduser()
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
        fetched = datetime.fromisoformat(data["fetched_at"])
        age = (datetime.now(UTC) - fetched).total_seconds()
        if age > max_age:
            return None  # stale
        return data
    except (json.JSONDecodeError, KeyError, ValueError, OSError, TypeError):
        return None


def _resolve_quota_log_dir() -> Path | None:
    """Resolve the autoskillit log root directory. Returns None on any error."""
    try:
        override = os.environ.get(_AUTOSKILLIT_LOG_DIR_ENV)
        if override:
            return Path(override)
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "autoskillit" / "logs"
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "autoskillit" / "logs"
        return Path.home() / ".local" / "share" / "autoskillit" / "logs"
    except Exception:
        return None


def _write_quota_log_event(event: dict, log_dir: Path | None) -> None:
    """Append a quota guard event to quota_events.jsonl at the log root."""
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event) + "\n"
        with open(log_dir / "quota_events.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _extract_run_skill_result(tool_response: str | dict) -> str:
    """Extract a compact summary from the run_skill double-wrapped JSON response."""
    try:
        outer = json.loads(tool_response) if isinstance(tool_response, str) else tool_response
        if isinstance(outer, dict) and "result" in outer:
            inner_str = outer["result"]
            if isinstance(inner_str, str):
                try:
                    inner = json.loads(inner_str)
                    if isinstance(inner, dict):
                        success = inner.get("success", "unknown")
                        result_text = inner.get("result", "")
                        if isinstance(result_text, str) and len(result_text) > 500:
                            result_text = result_text[:500] + "..."
                        return f"success: {success}\nresult: {result_text}"
                except (json.JSONDecodeError, ValueError):
                    pass
                return inner_str[:500] if len(inner_str) > 500 else inner_str
        return str(outer)[:500]
    except (json.JSONDecodeError, ValueError, TypeError):
        return str(tool_response)[:500]


def main(*, cache_path_override: str | None = None) -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:
        sys.exit(0)

    if not isinstance(event, dict):
        sys.exit(0)

    tool_name = event.get("tool_name", "")
    tool_response = event.get("tool_response") or ""

    settings = resolve_quota_settings(cache_path_override=cache_path_override)
    if settings.disabled:
        sys.exit(0)  # quota guard disabled for this session
    cache_path_str = settings.cache_path
    cache_max_age = settings.cache_max_age
    log_dir = _resolve_quota_log_dir()
    ts = datetime.now(UTC).isoformat()

    cache = _read_quota_cache(cache_path_str, cache_max_age)
    if cache is None:
        sys.exit(0)

    try:
        binding = cache.get("binding")
        if not binding or not isinstance(binding, dict):
            raise KeyError("binding")
        utilization = float(binding["utilization"])
        should_block = bool(binding.get("should_block", False))
        effective_threshold = float(binding.get("effective_threshold", 0.0))
        window_name = str(binding.get("window_name", "unknown"))
    except (KeyError, ValueError, TypeError):
        sys.exit(0)

    if not should_block:
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "post_check_pass",
                "effective_threshold": effective_threshold,
                "window_name": window_name,
                "utilization": utilization,
                "tool_name": tool_name,
            },
            log_dir,
        )
        sys.exit(0)

    resets_at_str = binding.get("resets_at")
    if resets_at_str:
        try:
            resets_at = datetime.fromisoformat(resets_at_str)
            now = datetime.now(UTC)
            n = max(
                0,
                int((resets_at - now).total_seconds()) + settings.buffer_seconds,
            )
        except (ValueError, TypeError):
            n = settings.buffer_seconds
    else:
        n = settings.buffer_seconds

    result_summary = _extract_run_skill_result(tool_response)

    warning_text = (
        f"{result_summary}\n\n"
        f"{QUOTA_POST_WARNING_TRIGGER}\n"
        f"Post-execution utilization: {utilization:.0f}% on window '{window_name}' "
        f"(threshold: {effective_threshold:.0f}%)\n"
        f"MANDATORY ACTION before next run_skill: Call run_cmd with: "
        f'python3 -c "import time; time.sleep({n})" timeout={n + 30}\n'
        f"Before executing, state aloud: "
        f"'Quota at {utilization:.0f}%. Sleeping {n}s before next step.'"
    )

    _write_quota_log_event(
        {
            "ts": ts,
            "event": "post_check_warning",
            "effective_threshold": effective_threshold,
            "window_name": window_name,
            "utilization": utilization,
            "sleep_seconds": n,
            "resets_at": resets_at_str,
            "tool_name": tool_name,
        },
        log_dir,
    )

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedMCPToolOutput": warning_text,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
