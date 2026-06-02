#!/usr/bin/env python3
"""PreToolUse guard: block fresh dispatch_food_truck on already-claimed issues.

When an issue already has an in-progress label from a prior dispatch,
starting a fresh L2 session will inevitably fail at claim time. This
guard forces the orchestrator to use resume_session_id instead.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

FLEET_CLAIM_DENY_TRIGGER: str = "already has an in-progress label"

_ISSUE_URL_RE = re.compile(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)")


def _deny(reason: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )


def _issue_has_in_progress_label(issue_url: str) -> bool | None:
    """Check via gh CLI whether the issue has an in-progress label.

    Returns True/False on success, None on any error (fail-open).
    """
    m = _ISSUE_URL_RE.match(issue_url.strip())
    if not m:
        return None
    repo, number = m.group(1), m.group(2)
    try:
        result = subprocess.run(
            ["gh", "issue", "view", number, "--repo", repo, "--json", "labels"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    labels = data.get("labels") or []
    return any(lb.get("name") == "in-progress" for lb in labels)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open

    if not isinstance(data, dict):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)

    # If resume_session_id is provided, this is a resume attempt — allow.
    # Trust assumption: interactive callers pass authentic values.
    # Headless callers are validated by resume_ownership_guard (runs earlier).
    if tool_input.get("resume_session_id"):
        sys.exit(0)

    # Extract issue URLs from ingredients.
    ingredients = tool_input.get("ingredients") or {}
    if not isinstance(ingredients, dict):
        sys.exit(0)
    issue_urls_raw = ingredients.get("issue_urls", "")
    if not issue_urls_raw:
        sys.exit(0)

    urls = [u.strip() for u in issue_urls_raw.split(",") if u.strip()]
    if not urls:
        sys.exit(0)

    # Check each URL. If ANY has in-progress label, block.
    for url in urls:
        has_label = _issue_has_in_progress_label(url)
        if has_label is True:
            _deny(
                f"Issue {url} already has an in-progress label from a prior dispatch. "
                f"You must resume the prior session — pass resume_session_id (from "
                f"dispatched_session_id in the prior result) and prior_dispatch_id (from "
                f"dispatch_id in the prior result) to dispatch_food_truck. If the prior "
                f"session is unrecoverable, ask the human operator to remove the label."
            )
            sys.exit(0)

    # No claimed issues found — allow.
    sys.exit(0)


if __name__ == "__main__":
    main()
