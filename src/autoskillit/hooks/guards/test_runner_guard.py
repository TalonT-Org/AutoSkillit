#!/usr/bin/env python3
"""PreToolUse hook: blocks direct pytest invocations in headless skill sessions.

Agents must use the configured `test_check.command` instead of invoking pytest
directly. Direct invocations cause test-retry loops that waste tokens.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    all_evaluated_segments,
    command_verb_and_args,
)
from _hook_constants import (  # type: ignore[import-not-found]  # noqa: E402
    DENY_TRIGGER_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD,
)
from _hook_payload import parse_hook_command  # type: ignore[import-not-found]  # noqa: E402
from _hook_settings import enforce_session_scope  # noqa: E402

TEST_RUNNER_DENY_TRIGGER: str = DENY_TRIGGER_BY_GUARD["test_runner_guard"]

_EXEMPT_SKILLS: frozenset[str] = EXEMPT_SKILLS_BY_GUARD["test_runner_guard"]

# Tokens that occupy a command position but are never pytest invocations.
# Matching tokens are either exactly the prefix or prefixed with an absolute
# or relative path (e.g. "/usr/bin/grep" or "./grep").
_READ_ONLY_PREFIXES: tuple[str, ...] = (
    "grep",
    "rg",
    "find",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "wc",
    "git",
    "pip",
    "echo",
    "printf",
    "ls",
    "file",
    "stat",
    "pre-commit",
    "task",
)

# Multi-word prefixes that need a 2-token check (e.g. "uv run" or "uv pip").
_READ_ONLY_MULTIWORD_PREFIXES: tuple[tuple[str, ...], ...] = (("uv", "pip"),)

_PYTEST_NAMES: frozenset[str] = frozenset({"pytest", "py.test"})


def _is_read_only_prefix(token: str) -> bool:
    """Return True if token names a known read-only command (possibly with a path prefix)."""
    basename = os.path.basename(token)
    return any(basename == prefix for prefix in _READ_ONLY_PREFIXES)


def _is_read_only_invocation(tokens: list[str]) -> bool:
    """Return True if tokens begin with a known read-only invocation."""
    token = tokens[0]
    if _is_read_only_prefix(token):
        return True
    return any(
        os.path.basename(token) == parts[0] and len(tokens) > 1 and tokens[1] == parts[1]
        for parts in _READ_ONLY_MULTIWORD_PREFIXES
        if len(parts) >= 2
    )


def _is_uv_run_pytest(args: list[str]) -> bool:
    """Return True if uv run arguments execute pytest."""
    if not args or args[0] != "run":
        return False
    for idx, run_tok in enumerate(args[1:]):
        if run_tok.startswith("-"):
            continue
        run_basename = os.path.basename(run_tok)
        if run_basename in _PYTEST_NAMES:
            return True
        if re.match(r"python3?$", run_basename):
            remaining = args[idx + 2 :]
            return len(remaining) >= 2 and remaining[0] == "-m" and remaining[1] in _PYTEST_NAMES
        # Non-flag, non-pytest, non-python3 token: not a pytest invocation
        # itself but keep scanning — pytest (or another match) may appear
        # later in the arg list (e.g. `uv run helper.py pytest`).
        continue
    return False


def _is_pytest_invocation(verb: str, args: list[str]) -> bool:
    """Return True if a parsed command invocation directly executes pytest."""
    tokens = [verb, *args]
    if _is_read_only_invocation(tokens):
        return False

    basename = os.path.basename(verb)
    if basename in _PYTEST_NAMES:
        return True
    if re.match(r"python3?$", basename):
        return len(args) >= 2 and args[0] == "-m" and args[1] in _PYTEST_NAMES
    if basename == "uv":
        return _is_uv_run_pytest(args)
    return False


def _is_direct_pytest(cmd: str) -> bool:
    """Return True if cmd contains a direct pytest invocation in command position.

    Reads *cmd* through `all_evaluated_segments` (rectify #4941 Part B): a
    direct invocation, one delivered via `bash -c`/`eval`, one fed through a
    heredoc/herestring/pipe to a shell, and a literal-argv
    `subprocess.run(["pytest", ...])` are all seen the same way. `None`
    (unparseable) is treated as no segments, matching today's unclosed-quote
    fail-open contract. `command_verb_and_args` skips a leading POSIX
    assignment or wrapper (sudo, env, ...) to find the verb, so `sudo pytest`
    and `VAR=1 pytest` now resolve to `pytest` too.
    """
    for segment in all_evaluated_segments(cmd) or ():
        if not segment:
            continue
        verb, rest = command_verb_and_args(segment)
        if not verb:
            continue
        if _is_pytest_invocation(verb, rest):
            return True

    return False


def main() -> None:
    if not enforce_session_scope("guards/test_runner_guard.py"):
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name in _EXEMPT_SKILLS:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open

    cmd = parse_hook_command(data).command or ""
    if not cmd:
        sys.exit(0)

    if _is_direct_pytest(cmd):
        reason = (
            f"{TEST_RUNNER_DENY_TRIGGER}. "
            "Use the configured `test_check.command` instead of invoking pytest.\n"
            "The configured gate handles environment setup, path filtering, "
            "and provides unambiguous PASS/FAIL output.\n"
            "If you need to run a specific test subset, set AUTOSKILLIT_TEST_FILTER=none "
            "on the configured command."
        )
        payload = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
        sys.stdout.write(payload + "\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
