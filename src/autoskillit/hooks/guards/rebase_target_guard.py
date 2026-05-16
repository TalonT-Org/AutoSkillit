#!/usr/bin/env python3
"""PreToolUse hook — blocks wrong-target git rebases in headless skill sessions.

When a headless skill session runs a literal `git rebase <remote>/<branch>`,
this guard reads the sidecar file to determine the authoritative base branch
and denies the call if the target does not match.

Fails-open for:
- Interactive sessions (AUTOSKILLIT_HEADLESS != "1")
- Orchestrator / fleet tiers (SESSION_TYPE != "skill")
- Commands with no literal rebase target (e.g., variable expansion "$REMOTE/...")
- Missing or unreadable sidecar (guard cannot validate)
- git common-dir resolution failures
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REBASE_TARGET_DENY_TRIGGER: str = "rebase targets wrong branch"

# Matches: git rebase origin/<branch> or git rebase upstream/<branch>
# The branch group must not start with $ (skip variable expansions)
_REBASE_PATTERN = re.compile(r"\bgit\s+rebase\s+(?:origin|upstream)/([^\s$\"\'\\]+)")

_SIDECAR_REL = ".autoskillit/temp/worktrees"


def _resolve_sidecar_branch(cwd: str) -> str | None:
    """Return the sidecar base branch for the worktree at *cwd*, or None."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        git_common_dir = result.stdout.strip()
        main_root = Path(git_common_dir).parent
        wt_name = Path(cwd).name
        sidecar = main_root / _SIDECAR_REL / wt_name / "base-branch"
        if not sidecar.is_file():
            return None
        return sidecar.read_text().strip() or None
    except Exception:
        return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)

    # Interactive sessions always pass
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    # Headless: only constrain skill sessions
    session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "").lower()
    if session_type not in ("skill", "leaf"):
        sys.exit(0)

    tool_input: dict = data.get("tool_input", {})
    command: str = tool_input.get("command", "") or tool_input.get("cmd", "")
    if not command:
        sys.exit(0)

    match = _REBASE_PATTERN.search(command)
    if not match:
        sys.exit(0)  # no literal rebase target found

    rebase_target_branch = match.group(1)

    cwd: str = tool_input.get("cwd", "") or os.getcwd()
    expected_branch = _resolve_sidecar_branch(cwd)
    if expected_branch is None:
        sys.exit(0)  # no sidecar — cannot validate, fail-open

    if rebase_target_branch == expected_branch:
        sys.exit(0)

    denial_reason = (
        f"git rebase {rebase_target_branch!r} targets wrong branch. "
        f"The sidecar records the authoritative base branch as {expected_branch!r}. "
        f"Use: git rebase <remote>/{expected_branch}"
    )
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": denial_reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
