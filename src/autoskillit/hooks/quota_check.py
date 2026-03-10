#!/usr/bin/env python3
"""PreToolUse hook: quota check before run_skill.

Reads the local quota cache file (written by autoskillit's quota module) and
denies run_skill when utilization exceeds the threshold. Fails open when the
cache is missing, expired, or unreadable — the next run_skill call will discover
quota exhaustion on its own.

This script is stdlib-only so it can run under any Python interpreter without
requiring the autoskillit package to be importable.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_DEFAULT_CACHE_PATH = "~/.claude/autoskillit_quota_cache.json"
_DEFAULT_THRESHOLD = 90.0
_DEFAULT_CACHE_MAX_AGE = 300  # seconds

HOOK_CONFIG_FILENAME = ".autoskillit_hook_config.json"
HOOK_DIR_COMPONENTS = (".autoskillit", "temp")

_AUTOSKILLIT_LOG_DIR_ENV = "AUTOSKILLIT_LOG_DIR"


def _read_hook_config() -> dict:
    """Read server-written config from .autoskillit/temp/.autoskillit_hook_config.json.

    Returns the quota_guard section, or {} if the file is absent or unreadable.
    This file is written by open_kitchen and removed by close_kitchen.
    """
    try:
        config_path = Path.cwd().joinpath(*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME)
        return json.loads(config_path.read_text()).get("quota_guard", {})
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


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
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def _resolve_quota_log_dir() -> Path | None:
    """Resolve the autoskillit log root directory. Returns None on any error.

    Priority: AUTOSKILLIT_LOG_DIR env var > platform default.
    Mirrors the logic in execution/session_log.py:resolve_log_dir().
    """
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
    """Append a quota guard event to quota_events.jsonl at the log root.

    Silently no-ops on any error — hook observability must never block run_skill.
    Event schema: {ts, event, threshold, utilization?, sleep_seconds?, resets_at?, cache_path?}
    """
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event) + "\n"
        with open(log_dir / "quota_events.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass  # Never block the hook on logging failure


def main() -> None:
    try:
        raw = sys.stdin.read()
        _ = json.loads(raw)  # validate event is JSON; contents not needed
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # malformed event — approve
    except Exception as e:
        print(
            f"quota_check: unexpected error reading stdin: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(0)  # log but approve — don't block run_skill on hook bugs

    hook_config = _read_hook_config()
    threshold = hook_config.get("threshold", _DEFAULT_THRESHOLD)
    cache_max_age = hook_config.get("cache_max_age", _DEFAULT_CACHE_MAX_AGE)
    # env var takes priority over hook config for cache path
    cache_path_str = (
        os.environ.get("AUTOSKILLIT_QUOTA_CACHE")
        or hook_config.get("cache_path")
        or _DEFAULT_CACHE_PATH
    )
    log_dir = _resolve_quota_log_dir()
    ts = datetime.now(UTC).isoformat()

    cache = _read_quota_cache(cache_path_str, cache_max_age)
    if cache is None:
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "cache_miss",
                "threshold": threshold,
                "cache_path": cache_path_str,
            },
            log_dir,
        )
        sys.exit(0)  # no fresh cache — fail open

    try:
        utilization = float(cache["five_hour"]["utilization"])
    except (KeyError, ValueError, TypeError):
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "parse_error",
                "threshold": threshold,
                "cache_path": cache_path_str,
            },
            log_dir,
        )
        sys.exit(0)  # malformed cache — fail open

    if utilization >= threshold:
        resets_at_str = cache.get("five_hour", {}).get("resets_at")
        if resets_at_str:
            try:
                resets_at = datetime.fromisoformat(resets_at_str)
                buffer_seconds = 60
                now = datetime.now(UTC)
                n = max(0, int((resets_at - now).total_seconds()) + buffer_seconds)
            except (ValueError, TypeError):
                n = 60
        else:
            n = 60

        _write_quota_log_event(
            {
                "ts": ts,
                "event": "blocked",
                "threshold": threshold,
                "utilization": utilization,
                "sleep_seconds": n,
                "resets_at": resets_at_str,
            },
            log_dir,
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Quota threshold exceeded. Sleep {n} seconds then retry. "
                            f'Call run_cmd with: python3 -c "import time; time.sleep({n})" '
                            f"timeout={n + 30}"
                        ),
                    }
                }
            )
        )
    else:
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "approved",
                "threshold": threshold,
                "utilization": utilization,
            },
            log_dir,
        )
    sys.exit(0)  # exit 0 so Claude Code parses the JSON decision


if __name__ == "__main__":
    main()
