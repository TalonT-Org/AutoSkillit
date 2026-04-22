#!/usr/bin/env python3
"""PreToolUse hook: block gh pr create via run_cmd when kitchen is open.

Enforces the mandatory prepare_pr → compose_pr pipeline. Direct gh pr create
calls bypass mandatory arch-lens, diff annotation, and automated code review.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

# Shell-separator tokens that introduce a new subcommand.
_SHELL_OPS = frozenset({"&&", "||", ";", "!", "|", "("})

_DENY_REASON = (
    "PR creation via run_cmd is prohibited during recipe execution. "
    "Use the prepare_pr → compose_pr pipeline instead. "
    "Direct gh pr create bypasses mandatory arch-lens, annotation, and review steps."
)


def _is_gh_pr_create(cmd: str) -> bool:
    """Return True only when `gh pr create` appears as an actual subcommand.

    Tokenises with shlex to avoid false positives from quoted arguments
    (e.g. ``echo 'do not gh pr create'`` must not match). A `gh` token is
    considered a subcommand start when it is at position 0 or immediately
    follows a shell separator token (&&, ||, ;, |, !, ().
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Unclosed quotes — shlex cannot parse; fail-open (no block).
        return False
    for i, token in enumerate(tokens):
        if token == "gh" and i + 2 < len(tokens):
            if tokens[i + 1] == "pr" and tokens[i + 2] == "create":
                if i == 0 or tokens[i - 1] in _SHELL_OPS:
                    return True
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        cmd = data.get("tool_input", {}).get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not _is_gh_pr_create(cmd):
        sys.exit(0)
    # Hook config file is written by open_kitchen and removed by close_kitchen.
    # Its presence reliably signals an open kitchen without needing session ID.
    try:
        cfg_path = Path.cwd() / ".autoskillit" / "temp" / ".hook_config.json"
        if not cfg_path.exists():
            sys.exit(0)  # kitchen not open; fail-open
    except OSError:
        sys.exit(0)
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
    sys.exit(0)


if __name__ == "__main__":
    main()
