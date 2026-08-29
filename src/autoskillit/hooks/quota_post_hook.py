#!/usr/bin/env python3
"""PostToolUse hook: quota warning after run_skill execution.

Fires after run_skill completes and checks whether the cached binding marks
``should_block=True``. When set, replaces the tool output with a quota warning
and sleep instruction via updatedMCPToolOutput.

This script is stdlib-only so it can run under any Python interpreter without
requiring the autoskillit package to be importable.
"""

import json
import os
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
_PACKAGE_DIR = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_DIR not in sys.path:
    sys.path.insert(0, _PACKAGE_DIR)

from _hook_settings import (  # noqa: E402
    is_quota_guard_disabled_for_session,
    read_quota_cache,
    resolve_quota_log_dir,
    resolve_quota_settings,
    write_quota_log_event,
)  # type: ignore[import-not-found]
from quota_constraints import (  # noqa: E402
    QuotaConstraint,
    QuotaEvidenceSource,
    decode_observed_constraints,
    effective_quota_block,
    observed_constraint_path,
)  # type: ignore[import-not-found]

# Emitted in post-tool output; referenced by orchestrator prompt and sous-chef SKILL.md.
QUOTA_POST_WARNING_TRIGGER: str = "--- QUOTA WARNING ---"
QUOTA_POST_BUDGET_EXCEEDED_TRIGGER: str = "QUOTA BUDGET EXCEEDED"


def quota_post_decision(settings, *, now_epoch: int) -> tuple[QuotaConstraint | None, dict]:
    """Return the cumulative quota blocker and poll display metadata."""
    constraints = decode_observed_constraints(observed_constraint_path(settings.cache_path))
    metadata = {
        "utilization": 0.0,
        "effective_threshold": 0.0,
        "window_name": "unknown",
        "unknown_reset_block": False,
    }
    cache = read_quota_cache(settings.cache_path, settings.cache_max_age)
    if cache is not None:
        binding = cache.get("binding")
        if isinstance(binding, dict):
            try:
                metadata = {
                    "utilization": float(binding["utilization"]),
                    "effective_threshold": float(binding.get("effective_threshold", 0.0)),
                    "window_name": str(binding.get("window_name", "unknown")),
                    "unknown_reset_block": False,
                }
                resets_at = binding.get("resets_at")
                if bool(binding.get("should_block", False)):
                    if resets_at:
                        constraints.append(
                            QuotaConstraint(
                                source=QuotaEvidenceSource.PROVIDER_POLL,
                                scope=settings.quota_account_scope,
                                blocked_until_epoch=int(
                                    datetime.fromisoformat(str(resets_at)).timestamp()
                                ),
                                observed_at_epoch=now_epoch,
                                limit_type=metadata["window_name"],
                            )
                        )
                    else:
                        metadata["unknown_reset_block"] = True
            except (KeyError, TypeError, ValueError):
                pass
    winner = effective_quota_block(
        constraints,
        account_scope=settings.quota_account_scope,
        now_epoch=now_epoch,
    )
    return winner, metadata


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

    backend = os.environ.get("AUTOSKILLIT_AGENT_BACKEND", "").strip()
    if backend == "codex":
        write_quota_log_event(
            {
                "ts": ts,
                "event": "post_backend_bypass",
                "backend": backend,
                "tool_name": tool_name,
                "cache_path": cache_path_str,
            },
            log_dir,
            caller="quota_post_hook",
        )
        sys.exit(0)

    profile = os.environ.get("AUTOSKILLIT_PROVIDER_PROFILE", "").strip()
    if profile and profile.casefold() != "anthropic":
        write_quota_log_event(
            {
                "ts": ts,
                "event": "post_provider_bypass",
                "profile": profile,
                "tool_name": tool_name,
                "cache_path": cache_path_str,
            },
            log_dir,
            caller="quota_post_hook",
        )
        sys.exit(0)

    now_epoch = int(time.time())
    winner, metadata = quota_post_decision(settings, now_epoch=now_epoch)
    utilization = float(metadata["utilization"])
    effective_threshold = float(metadata["effective_threshold"])
    window_name = str(metadata["window_name"])
    should_block = winner is not None or bool(metadata["unknown_reset_block"])

    if not should_block:
        write_quota_log_event(
            {
                "ts": ts,
                "event": "post_check_pass",
                "effective_threshold": effective_threshold,
                "window_name": window_name,
                "utilization": utilization,
                "tool_name": tool_name,
            },
            log_dir,
            caller="quota_post_hook",
        )
        sys.exit(0)

    if winner is not None:
        resets_at_str = datetime.fromtimestamp(winner.blocked_until_epoch, tz=UTC).isoformat()
        n = max(
            0,
            winner.blocked_until_epoch - now_epoch + settings.buffer_seconds,
        )
        window_name = winner.limit_type or window_name
    else:
        resets_at_str = None
        n = settings.buffer_seconds

    session_deadline_str = os.environ.get("AUTOSKILLIT_SESSION_DEADLINE")
    budget_exceeded = False
    remaining_budget = float("inf")
    if session_deadline_str:
        try:
            session_deadline = float(session_deadline_str)
            remaining_budget = max(0, session_deadline - time.time())
            if n > remaining_budget:
                budget_exceeded = True
        except (ValueError, TypeError):
            pass

    if budget_exceeded:
        resets_at_display = resets_at_str or "unknown"
        warning_text = (
            f"{QUOTA_POST_BUDGET_EXCEEDED_TRIGGER}\n"
            f"Post-execution utilization: {utilization:.0f}% on window '{window_name}' "
            f"(threshold: {effective_threshold:.0f}%)\n"
            f"Quota sleep ({n}s) exceeds session budget ({remaining_budget:.0f}s remaining).\n"
            f"MANDATORY ACTION: Emit your result block with "
            f'"success": false, '
            f'"reason": "fleet_quota_exhausted", '
            f'"wait_seconds": {n}, '
            f'"resets_at": "{resets_at_display}", '
            f'"summary": "Quota exceeded; session budget insufficient for sleep. '
            f'"Resume after window resets." '
            f"Then STOP — do not call any more tools."
        )
    else:
        warning_text = (
            f"{QUOTA_POST_WARNING_TRIGGER}\n"
            f"Post-execution utilization: {utilization:.0f}% on window '{window_name}' "
            f"(threshold: {effective_threshold:.0f}%)\n"
            f"MANDATORY ACTION before next run_skill: Call run_cmd with: "
            f'python3 -c "import time; time.sleep({n})" timeout={n + 30}\n'
            f"Before executing, state aloud: "
            f"'Quota at {utilization:.0f}%. Sleeping {n}s before next step.'"
        )

    write_quota_log_event(
        {
            "ts": ts,
            "event": "post_check_budget_exceeded" if budget_exceeded else "post_check_warning",
            "effective_threshold": effective_threshold,
            "window_name": window_name,
            "utilization": utilization,
            "sleep_seconds": n,
            "resets_at": resets_at_str,
            "tool_name": tool_name,
            "budget_exceeded": budget_exceeded,
            "remaining_budget": remaining_budget if remaining_budget != float("inf") else None,
        },
        log_dir,
        caller="quota_post_hook",
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
