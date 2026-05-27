#!/usr/bin/env python3
"""PreToolUse hook: block gh run/release download without --dir via run_cmd or Bash tool.

Prevents CI artifact downloads from dumping files into the project root.
All sessions are covered — no skill or session-type exemptions exist because
no legitimate autoskillit skill uses gh run/release download without --dir.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import shlex
import sys

# Shell-separator tokens that introduce a new subcommand.
_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "!", "|", "("})

_DOWNLOAD_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset(
    {("run", "download"), ("release", "download")}
)

_DENY_REASON = (
    "gh {sub1} download without --dir is prohibited. "
    "Artifact downloads without --dir dump files into the project root. "
    "Use --dir <path> or -D <path> to specify an explicit output directory."
)


def _deny_subcommand(cmd: str) -> str | None:
    """Return the subcommand name if an unguarded download is detected, else None."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    for i, token in enumerate(tokens):
        if token != "gh" or i + 2 >= len(tokens):
            continue
        if i != 0 and tokens[i - 1] not in _SHELL_OPS:
            continue
        pair = (tokens[i + 1], tokens[i + 2])
        if pair in _DOWNLOAD_SUBCOMMANDS:
            rest: list[str] = []
            for t in tokens[i + 3 :]:
                if t in _SHELL_OPS:
                    break
                rest.append(t)
            if "--dir" not in rest and "-D" not in rest:
                return tokens[i + 1]  # e.g. "run" or "release"
    return None


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not cmd:
        sys.exit(0)

    sub1 = _deny_subcommand(cmd)
    if sub1 is None:
        sys.exit(0)

    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _DENY_REASON.format(sub1=sub1),
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
