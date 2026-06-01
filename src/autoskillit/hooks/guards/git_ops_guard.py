#!/usr/bin/env python3
"""PreToolUse hook: block destructive git operations in headless skill sessions.

Blocks commit --amend, push --force, reset --hard, clean -f, checkout .
and related operations that rewrite history or destroy uncommitted changes.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _SHELL_OPS,
    has_interpreter_wrapped_command,
    has_nested_shell,
)
from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402

GIT_OPS_DENY_TRIGGER: str = "Destructive git operation blocked in headless session"

_DENY_REASON_TEMPLATE = (
    "Destructive git operation '{op}' is blocked in headless skill sessions. "
    "Create a new commit instead of amending, and avoid force-push, reset --hard, "
    "clean -f, or checkout . in automated workflows."
)

# Must stay in sync with RISKY_GIT_OPERATIONS in hook_registry.py —
# stdlib-only boundary prevents a shared import. test_risky_git_ops_coverage.py
# enforces that this set covers every tuple in RISKY_GIT_OPERATIONS.
_BLOCKED_GIT_OPS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("commit", "--amend"),
        ("push", "--force"),
        ("push", "-f"),
        ("push", "--force-with-lease"),
        ("reset", "--hard"),
        ("clean", "-f"),
        ("clean", "-fd"),
        ("checkout", "."),
        ("checkout", "--", "."),
    }
)

_GIT_GLOBAL_FLAGS: frozenset[str] = frozenset(
    {"-C", "--work-tree", "--git-dir", "--no-pager", "--bare", "-c"}
)
_GIT_GLOBAL_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"-C", "--work-tree", "--git-dir", "-c"})

# No skill legitimately needs destructive git ops in a headless session.
_EXEMPT_SKILLS: frozenset[str] = frozenset()

# Must stay in sync with exempt_session_types on the git_ops_guard HookDef
# in hook_registry.py — stdlib-only boundary prevents a shared import.
_EXEMPT_SESSION_TYPES: frozenset[str] = frozenset({"orchestrator"})


def _extract_git_subcommand_and_remaining(
    tokens: list[str], start: int
) -> tuple[str, list[str]] | None:
    """Starting at tokens[start] (the 'git' token), return (subcommand, remaining).

    Skips global git flags and their value tokens to find the subcommand.
    Returns None if no subcommand is found.
    """
    i = start + 1
    while i < len(tokens):
        token = tokens[i]
        if token in _GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        if token in _GIT_GLOBAL_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        subcommand = token
        remaining = tokens[i + 1 :]
        return (subcommand, remaining)
    return None


def _contains_blocked_git_op(cmd: str) -> tuple[str, ...] | None:
    """Return the matching blocked git op tuple, or None if no match.

    Tokenises with shlex. A 'git' token (or /path/to/git) is considered a
    command start when it is at position 0 or immediately follows a shell
    separator token. env-prefixed invocations (VAR=1 git ...) are skipped
    (fail-open), matching artifact_download_guard behavior.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None

    for i, token in enumerate(tokens):
        if token != "git" and not token.endswith("/git"):
            continue
        # Only treat as a command start at position 0 or after a shell operator.
        if i != 0 and tokens[i - 1] not in _SHELL_OPS:
            continue
        result = _extract_git_subcommand_and_remaining(tokens, i)
        if result is None:
            continue
        subcommand, remaining = result
        for op_tuple in _BLOCKED_GIT_OPS:
            if subcommand != op_tuple[0]:
                continue
            flags = op_tuple[1:]
            if all(f in remaining for f in flags):
                return op_tuple

    # Check for interpreter-wrapped invocations (python3 -c "subprocess.run(['git', ...])")
    if has_interpreter_wrapped_command(cmd, target_commands=["git"]):
        cmd_lower = cmd.lower()
        for op_tuple in _BLOCKED_GIT_OPS:
            if op_tuple[0] in cmd_lower and all(f in cmd_lower for f in op_tuple[1:]):
                return op_tuple

    # Check for nested shell invocations (bash -c "git commit --amend")
    if has_nested_shell(cmd):
        cmd_lower = cmd.lower()
        for op_tuple in _BLOCKED_GIT_OPS:
            if (
                "git" in cmd_lower
                and op_tuple[0] in cmd_lower
                and all(f in cmd_lower for f in op_tuple[1:])
            ):
                return op_tuple

    return None


def main() -> None:
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
        tool_input = data.get("tool_input", {})
        cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    except (json.JSONDecodeError, AttributeError, OSError):
        sys.exit(0)

    if not cmd:
        sys.exit(0)

    blocked = _contains_blocked_git_op(cmd)
    if blocked is None:
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name in _EXEMPT_SKILLS:
        sys.exit(0)

    session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    if session_type in _EXEMPT_SESSION_TYPES:
        sys.exit(0)

    # Hook config file is written by open_kitchen and removed by close_kitchen.
    # Its presence reliably signals an open kitchen without needing session ID.
    try:
        cfg_path = Path.cwd() / ".autoskillit" / "temp" / ".hook_config.json"
        if not cfg_path.exists():
            sys.exit(0)
    except OSError:
        sys.exit(0)

    # Recipe-level authorization: check git_ops_policy for per-subcommand allow.
    try:
        hook_data = read_merged_hook_config()
        git_ops_policy = hook_data.get("git_ops_policy", {})
        if git_ops_policy.get(f"allow_{blocked[0]}"):
            sys.exit(0)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        sys.stderr.write(f"git_ops_guard: config read error: {exc}\n")

    op_str = " ".join(("git",) + blocked)
    deny_reason = _DENY_REASON_TEMPLATE.format(op=op_str)
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
