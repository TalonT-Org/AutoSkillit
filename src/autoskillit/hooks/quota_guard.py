#!/usr/bin/env python3
"""PreToolUse hook: quota check before run_skill.

Reads the local quota cache file (written by autoskillit's quota module) and
denies run_skill when the cached binding marks ``should_block=True``. The
threshold classification is computed once on the cache write side, so this
hook contains no threshold logic of its own. Fails open when the cache is
missing, expired, or unreadable — the next run_skill call will discover
quota exhaustion on its own.

This script is stdlib-only so it can run under any Python interpreter without
requiring the autoskillit package to be importable.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Sibling-import bootstrap: hooks run as ``python3 /path/to/quota_check.py``
# subprocesses outside the autoskillit venv (test_hooks_are_stdlib_only).
# Placing the script's directory first on sys.path lets the bare-name import
# below resolve to the shared stdlib-only settings module in both subprocess
# and package-mode invocations.
_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _hook_settings import resolve_quota_settings  # type: ignore[import-not-found]  # noqa: E402

_AUTOSKILLIT_LOG_DIR_ENV = "AUTOSKILLIT_LOG_DIR"


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
    Event schema: {ts, event, effective_threshold?, window_name?, utilization?,
    sleep_seconds?, resets_at?, cache_path?}
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


def main(*, cache_path_override: str | None = None) -> None:
    try:
        raw = sys.stdin.read()
        _ = json.loads(raw)  # validate event is JSON; contents not needed
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # malformed event — approve
    except Exception as e:
        print(
            f"quota_guard: unexpected error reading stdin: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(0)  # log but approve — don't block run_skill on hook bugs

    settings = resolve_quota_settings(cache_path_override=cache_path_override)
    if settings.disabled:
        sys.exit(0)  # quota guard disabled for this session
    cache_path_str = settings.cache_path
    cache_max_age = settings.cache_max_age
    log_dir = _resolve_quota_log_dir()
    ts = datetime.now(UTC).isoformat()

    cache = _read_quota_cache(cache_path_str, cache_max_age)
    if cache is None:
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "cache_miss",
                "cache_path": cache_path_str,
            },
            log_dir,
        )
        sys.exit(0)  # no fresh cache — fail open

    try:
        binding = cache.get("binding")
        if not binding or not isinstance(binding, dict):
            raise KeyError("binding")
        utilization = float(binding["utilization"])
        should_block = bool(binding.get("should_block", False))
        effective_threshold = float(binding.get("effective_threshold", 0.0))
        window_name = str(binding.get("window_name", "unknown"))
    except (KeyError, ValueError, TypeError):
        _write_quota_log_event(
            {
                "ts": ts,
                "event": "parse_error",
                "cache_path": cache_path_str,
            },
            log_dir,
        )
        sys.exit(0)  # malformed cache — fail open

    if should_block:
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

        _write_quota_log_event(
            {
                "ts": ts,
                "event": "blocked",
                "effective_threshold": effective_threshold,
                "window_name": window_name,
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
                            f"QUOTA WAIT REQUIRED (temporary — NOT a permanent error). "
                            f"Utilization: {utilization:.0f}% on window '{window_name}' "
                            f"(threshold: {effective_threshold:.0f}%). "
                            f"MANDATORY ACTION: Call run_cmd with: "
                            f'python3 -c "import time; time.sleep({n})" timeout={n + 30} — '
                            f"then retry the SAME run_skill call with identical arguments. "
                            f"Before executing, state aloud: "
                            f"'Quota exceeded at {utilization:.0f}%. "
                            f"Sleeping {n}s, then retrying.'"
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
                "effective_threshold": effective_threshold,
                "window_name": window_name,
                "utilization": utilization,
            },
            log_dir,
        )
    sys.exit(0)  # exit 0 so Claude Code parses the JSON decision


if __name__ == "__main__":
    main()
