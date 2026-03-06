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
_DEFAULT_CACHE_MAX_AGE = 60  # seconds


def _read_quota_cache() -> dict | None:
    """Read quota cache file. Returns parsed data or None if missing/stale/corrupt."""
    cache_path_str = os.environ.get("AUTOSKILLIT_QUOTA_CACHE", _DEFAULT_CACHE_PATH)
    cache_path = Path(cache_path_str).expanduser()
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
        fetched = datetime.fromisoformat(data["fetched_at"])
        age = (datetime.now(UTC) - fetched).total_seconds()
        if age > _DEFAULT_CACHE_MAX_AGE:
            return None  # stale
        return data
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


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

    cache = _read_quota_cache()
    if cache is None:
        sys.exit(0)  # no fresh cache — fail open

    try:
        utilization = float(cache["five_hour"]["utilization"])
    except (KeyError, ValueError, TypeError):
        sys.exit(0)  # malformed cache — fail open

    if utilization >= _DEFAULT_THRESHOLD:
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
    sys.exit(0)  # exit 0 so Claude Code parses the JSON decision


if __name__ == "__main__":
    main()
