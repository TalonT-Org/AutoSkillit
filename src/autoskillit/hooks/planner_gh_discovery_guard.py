#!/usr/bin/env python3
"""PreToolUse hook: block GitHub discovery commands in planner skill sessions.

Planner sessions must not discover GitHub issues/PRs via listing or search
commands. If a planner skill needs a specific issue, it must be provided as
skill input.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

DISCOVERY_DENY_TRIGGER: str = "Planner skills cannot discover GitHub issues"

_SHELL_OPS = frozenset({"&&", "||", ";", "!", "|", "("})

_DENY_REASON = (
    "Planner skills cannot discover GitHub issues or PRs. "
    "If you need to read a specific issue, it must be provided as a skill input."
)

_DISCOVERY_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("issue", "list"),
        ("pr", "list"),
        ("search", "issues"),
        ("search", "prs"),
    }
)

_TARGETED_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("issue", "view"),
        ("pr", "view"),
    }
)

_API_LISTING_RE = re.compile(r"/repos/[^/]+/[^/]+/(issues|pulls)(?:\?.*)?$")

_API_SPECIFIC_RE = re.compile(r"/repos/[^/]+/[^/]+/(issues|pulls)/\d+")


def _is_gh_discovery(cmd: str) -> bool:
    """Return True when *cmd* contains a GitHub discovery subcommand.

    Targeted reads (``gh issue view <N>``, ``gh api .../issues/<N>``) are
    explicitly allowed.  Tokenises with shlex to avoid false positives from
    quoted arguments.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    for i, token in enumerate(tokens):
        if token != "gh" or i + 2 >= len(tokens):
            continue
        if i != 0 and tokens[i - 1] not in _SHELL_OPS:
            continue
        pair = (tokens[i + 1], tokens[i + 2])
        if pair in _DISCOVERY_SUBCOMMANDS:
            return True
        if pair in _TARGETED_SUBCOMMANDS:
            return False
        if tokens[i + 1] == "api":
            endpoint = tokens[i + 2]
            if _API_SPECIFIC_RE.search(endpoint):
                return False
            if _API_LISTING_RE.search(endpoint):
                return True
    return False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if not skill_name.startswith("planner-"):
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        sys.exit(0)
    cmd = tool_input.get("command", "") or tool_input.get("cmd", "")

    if not _is_gh_discovery(cmd):
        sys.exit(0)

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
