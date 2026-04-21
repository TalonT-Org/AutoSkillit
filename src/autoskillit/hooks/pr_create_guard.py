#!/usr/bin/env python3
"""PreToolUse hook: block gh pr create via run_cmd when kitchen is open.

Enforces the mandatory prepare_pr → compose_pr pipeline. Direct gh pr create
calls bypass mandatory arch-lens, diff annotation, and automated code review.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_GH_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")

_DENY_REASON = (
    "PR creation via run_cmd is prohibited during recipe execution. "
    "Use the prepare_pr → compose_pr pipeline instead. "
    "Direct gh pr create bypasses mandatory arch-lens, annotation, and review steps."
)


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        cmd = data.get("tool_input", {}).get("cmd", "")
        if not _GH_PR_CREATE_RE.search(cmd):
            sys.exit(0)
        # Hook config file is written by open_kitchen and removed by close_kitchen.
        # Its presence reliably signals an open kitchen without needing session ID.
        cfg_path = Path.cwd() / ".autoskillit" / "temp" / ".hook_config.json"
        if not cfg_path.exists():
            sys.exit(0)  # kitchen not open; fail-open
        # Kitchen is open and command matches: deny
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _DENY_REASON,
                }
            }
        )
        sys.stdout.write(payload + "\n")
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
