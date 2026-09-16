#!/usr/bin/env python3
"""PreToolUse hook: validate skill_command path argument format.

Denies run_skill calls where a path-argument skill is
invoked with extra descriptive text before the actual file path, e.g.:

    /implement-worktree-no-merge the verified plan .autoskillit/temp/plan.md
                                              ^^^^^^^^^^^^^^^^^^^ extra words

Detection logic:
- Parse the skill short-name from skill_command.
- If the skill is in PATH_ARG_SKILLS, scan the argument tokens.
- If the first token is NOT path-like but a later token IS path-like, the
  anti-pattern is detected → deny with an actionable message.
- If no path-like token exists at all → allow (could be pasted content).
- If the first token is already path-like → allow (correct format).

Output format follows the Claude Code hookSpecificOutput spec, matching
the pattern established by quota_guard.py in the same hooks/ package.

Exit strategy: uses exit 0 + JSON permissionDecision "deny" (same as
quota_guard.py). If the deny is found to be ignored in practice (see
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
SKILL_CMD_DENY_TRIGGER: str = "skill_command format error for"

PATH_ARG_SKILLS: frozenset[str] = frozenset(
    {
        "implement-worktree-no-merge",
        "implement-worktree",
        "retry-worktree",
        "resolve-failures",
    }
)

# Skills that take git branch names as positional arguments.
# When these skills receive a path-like first argument (starts with '/'),
# the invocation is malformed — branch names cannot start with '/'.
# This set must match exactly the skills whose SKILL.md files document
# branch-name argument parsing (verified by TestBranchArgSkillsContract).
BRANCH_ARG_SKILLS: frozenset[str] = frozenset(
    {
        "review-pr",
    }
)

# Token prefixes that unambiguously identify a filesystem path argument.
_PATH_PREFIXES: tuple[str, ...] = ("/", "./", ".autoskillit/")

# Position of the plan file among path-like tokens for each skill.
_PLAN_PATH_POSITION: dict[str, int] = {
    "implement-worktree-no-merge": 0,
    "implement-worktree": 0,
    "retry-worktree": 0,
    "resolve-failures": 1,
}

# Captures the skill short-name from a skill_command string such as:
#   /implement-worktree-no-merge ...
#   implement-worktree-no-merge ...
_SKILL_RE = re.compile(r"^/?(?:autoskillit:)?(\S+)")


def is_path_like_token(token: str) -> bool:
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


def _branch_arg_denial_reason(skill_name: str, args_str: str) -> str | None:
    if args_str and args_str.split()[0].startswith("/"):
        return (
            f"{SKILL_CMD_DENY_TRIGGER} '{skill_name}': "
            f"first argument looks like a filesystem path but '{skill_name}' "
            "expects a git branch name. Branch names cannot start with '/'."
        )
    return None


def _path_arg_denial_reason(skill_name: str, args_str: str) -> str | None:
    if not args_str:
        return None

    tokens = args_str.split()
    first = tokens[0]
    if is_path_like_token(first):
        plan_pos = _PLAN_PATH_POSITION.get(skill_name)
        if plan_pos is not None:
            path_tokens = [token for token in tokens if is_path_like_token(token)]
            if plan_pos < len(path_tokens):
                plan_token = path_tokens[plan_pos]
                basename = plan_token.rsplit("/", 1)[-1]
                if basename and "." not in basename:
                    return (
                        f"{SKILL_CMD_DENY_TRIGGER} '{skill_name}': "
                        f"plan path argument '{plan_token}' has no file extension. "
                        "This may indicate a mangled path (e.g. stripped .md extension). "
                        "Verify the path is correct and includes the file extension."
                    )
        return None

    path_token = next((token for token in tokens[1:] if is_path_like_token(token)), None)
    if path_token is None:
        return None

    has_newlines = "\n" in args_str
    path_tokens = [token for token in tokens if is_path_like_token(token)]
    non_path_tokens = [token for token in tokens if not is_path_like_token(token)]
    path_part = " ".join(path_tokens)
    if has_newlines and non_path_tokens:
        prose_block = " ".join(non_path_tokens)
        correct_cmd = f"/{skill_name} {path_part}\n\n{prose_block}"
    else:
        tail = (" " + " ".join(non_path_tokens)) if non_path_tokens else ""
        correct_cmd = f"/{skill_name} {path_part}{tail}"
    return (
        f"skill_command format error for '{skill_name}': "
        f"found extra descriptive text '{first}...' before the path argument "
        f"'{path_token}'. Path-argument skills require the path as the first "
        f"argument after the skill name. Relocate any additional context or "
        f"instructions to after all path arguments. "
        f'Fix: set skill_command to "{correct_cmd}".'
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
    if skill_name not in PATH_ARG_SKILLS and skill_name not in BRANCH_ARG_SKILLS:
        sys.exit(0)

    args_str = skill_command[m.end() :].strip()
    if skill_name in BRANCH_ARG_SKILLS:
        reason = _branch_arg_denial_reason(skill_name, args_str)
    else:
        reason = _path_arg_denial_reason(skill_name, args_str)
    if reason is not None:
        _deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
