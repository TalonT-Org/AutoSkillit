#!/usr/bin/env python3
"""PostToolUse hook: record quota observations after run_skill execution.

The server owns quota admission when it finalizes an execution launch. This
hook only records the cached quota observation for diagnostics; it never
replaces a tool result or tells the caller to delay a later launch.

This script is stdlib-only so it can run under any Python interpreter without
requiring the autoskillit package to be importable.
"""

import json
import sys
import time
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
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

_PACKAGE_DIR = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from _hook_settings import (  # noqa: E402
    QuotaHookSettings,
    is_quota_guard_disabled_for_session,
    read_quota_cache,
    resolve_quota_log_dir,
    resolve_quota_settings,
    write_quota_log_event,
)  # type: ignore[import-not-found]
from quota_constraints import (  # noqa: E402
    QuotaConstraint,
    decide_quota_block,
)  # type: ignore[import-not-found]


def quota_post_decision(
    settings: QuotaHookSettings, *, now_epoch: int
) -> tuple[QuotaConstraint | None, dict]:
    """Return the cumulative quota blocker and poll display metadata."""
    return decide_quota_block(
        settings.cache_path,
        account_scope=settings.quota_account_scope,
        read_cache=read_quota_cache,
        cache_max_age=settings.cache_max_age,
        now_epoch=now_epoch,
    )


def main(*, cache_path_override: str | None = None) -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:
        sys.exit(0)

    if not isinstance(event, dict):
        sys.exit(0)

    tool_name = event.get("tool_name", "")
    event_session_id = ""
    if isinstance(event.get("session_id"), str):
        event_session_id = event["session_id"]
    settings = resolve_quota_settings(cache_path_override=cache_path_override)
    if settings.disabled:
        sys.exit(0)  # quota guard disabled for this session
    if event_session_id and is_quota_guard_disabled_for_session(event_session_id):
        sys.exit(0)  # session-scoped disable marker present
    cache_path_str = settings.cache_path
    log_dir = resolve_quota_log_dir(caller="quota_post_hook")
    ts = datetime.now(UTC).isoformat()

    now_epoch = int(time.time())
    winner, metadata = quota_post_decision(settings, now_epoch=now_epoch)
    write_quota_log_event(
        {
            "ts": ts,
            "event": "post_quota_observation",
            "cache_path": cache_path_str,
            "cache_state": metadata["cache_state"],
            "effective_threshold": float(metadata["effective_threshold"]),
            "window_name": (
                winner.limit_type if winner is not None else str(metadata["window_name"])
            ),
            "utilization": float(metadata["utilization"]),
            "constraint_observed": winner is not None,
            "unknown_reset_observed": bool(metadata["unknown_reset_block"]),
            "resets_at": (
                datetime.fromtimestamp(winner.blocked_until_epoch, tz=UTC).isoformat()
                if winner is not None
                else None
            ),
            "tool_name": tool_name,
        },
        log_dir,
        caller="quota_post_hook",
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
