#!/usr/bin/env python3
"""PreToolUse hook: validate skill_command path argument format.

Denies run_skill / run_skill_retry calls where a path-argument skill is
invoked with extra descriptive text before the actual file path, e.g.:

    /autoskillit:implement-worktree-no-merge the verified plan temp/plan.md
                                              ^^^^^^^^^^^^^^^^^^^ extra words

Detection logic:
- Parse the skill short-name from skill_command.
- If the skill is in PATH_ARG_SKILLS, scan the argument tokens.
- If the first token is NOT path-like but a later token IS path-like, the
  anti-pattern is detected → deny with an actionable message.
- If no path-like token exists at all → allow (could be pasted content).
- If the first token is already path-like → allow (correct format).

Output format follows the Claude Code hookSpecificOutput spec, matching
the pattern established by quota_check.py in the same hooks/ package.

Exit strategy: uses exit 0 + JSON permissionDecision "deny" (same as
quota_check.py). If the deny is found to be ignored in practice (see
GitHub Issue #4669 — closed as "not planned"), switch to exit 2 + stderr
message. The two-pronged design (hook + SKILL.md path-detection instructions)
provides defence in depth: even if the hook deny is occasionally ignored,
the headless Claude session's Step 0 catches the case internally.
"""
from __future__ import annotations

import json
import re
import sys

# Skills that take a file path as their first positional argument.
# When these skills receive extra descriptive text before the path, the
# headless session constructs an invalid path and fails with "not found".
# This set must match exactly the skills whose SKILL.md files carry
# path-detection instructions (verified by TestPathArgSkillsContract).
PATH_ARG_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree-no-merge",
        "implement-worktree",
        "retry-worktree",
        "resolve-failures",
    }
)

# Token prefixes that unambiguously identify a filesystem path argument.
_PATH_PREFIXES: tuple[str, ...] = ("/", "./", "temp/", ".autoskillit/")

# Captures the skill short-name from a skill_command string such as:
#   /autoskillit:implement-worktree-no-merge ...
#   implement-worktree-no-merge ...
_SKILL_RE = re.compile(r"^/?(?:autoskillit:)?(\S+)")


def _looks_like_path(token: str) -> bool:
    """Return True if token begins with a recognised filesystem path prefix."""
    return any(token.startswith(p) for p in _PATH_PREFIXES)


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.exit(0)  # malformed event — approve

    tool_input = data.get("tool_input", {})
    skill_command = tool_input.get("skill_command", "")
    if not skill_command:
        sys.exit(0)

    m = _SKILL_RE.match(skill_command.strip())
    if not m:
        sys.exit(0)

    skill_name = m.group(1)
    if skill_name not in PATH_ARG_SKILLS:
        sys.exit(0)

    args_str = skill_command[m.end() :].strip()
    if not args_str:
        sys.exit(0)

    tokens = args_str.split()
    first = tokens[0]

    # Correct format: first token is a path → allow.
    if _looks_like_path(first):
        sys.exit(0)

    # First token is not a path. Check whether a path token appears later.
    path_token = next((t for t in tokens[1:] if _looks_like_path(t)), None)
    if path_token is None:
        # No path-like token found at all — could be pasted plan content.
        # Allow; the skill's Step 0 will handle it.
        sys.exit(0)

    # Anti-pattern: path exists but is not the first token.
    correct_cmd = f"/autoskillit:{skill_name} {path_token}"
    _deny(
        f"skill_command format error for '{skill_name}': "
        f"found extra descriptive text '{first}...' before the path argument "
        f"'{path_token}'. Path-argument skills require the path as the first "
        f"argument after the skill name. "
        f"Fix: set skill_command to \"{correct_cmd}\" "
        f"(append any remaining positional args after the path if needed)."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
