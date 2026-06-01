#!/usr/bin/env python3
"""PreToolUse hook: blocks direct pytest invocations in headless skill sessions.

Agents must use `task test-check` (or `task test-all`) instead of invoking
pytest directly. Direct invocations cause test-retry loops that waste tokens.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

TEST_RUNNER_DENY_TRIGGER: str = "Direct pytest invocation is prohibited"

# Must stay in sync with the exempt_skills frozenset on the test_runner_guard
# HookDef in hook_registry.py — stdlib-only boundary prevents a shared import.
_EXEMPT_SKILLS: frozenset[str] = frozenset({"implement-experiment"})

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

# Splits a shell command string on common shell operators (&&, ||, ;, |) so each
# segment can be analyzed independently with shlex.split. This is necessary because
# shlex does not treat ; as a metacharacter, so "echo foo; pytest" tokenizes as
# ["echo", "foo;", "pytest"] — the semicolon stays attached to the preceding token.
_SHELL_SEG_RE: re.Pattern[str] = re.compile(r"&&|\|\||;|\|")


def _is_read_only_prefix(token: str) -> bool:
    """Return True if token names a known read-only command (possibly with a path prefix)."""
    basename = os.path.basename(token)
    return any(basename == prefix for prefix in _READ_ONLY_PREFIXES)


def _is_direct_pytest(cmd: str) -> bool:
    """Return True if cmd contains a direct pytest invocation in command position."""
    for segment in _SHELL_SEG_RE.split(cmd):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue  # unclosed quotes → fail-open
        if not tokens:
            continue

        token = tokens[0]

        # Read-only single-word prefixes: never a pytest invocation.
        if _is_read_only_prefix(token):
            continue

        # Read-only multi-word prefixes (e.g. "uv pip").
        multiword_matched = False
        for parts in _READ_ONLY_MULTIWORD_PREFIXES:
            if len(parts) < 2:
                continue
            head, tail = parts[0], parts[1]
            if os.path.basename(token) == head and len(tokens) > 1 and tokens[1] == tail:
                multiword_matched = True
                break
        if multiword_matched:
            continue

        # Bare pytest or path/to/pytest or py.test.
        basename = os.path.basename(token)
        if basename in _PYTEST_NAMES:
            return True

        # python -m pytest / python3 -m py.test.
        if re.match(r"python3?$", basename):
            if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in _PYTEST_NAMES:
                return True

        # uv run pytest / uv run path/to/pytest (flags between run and command are skipped).
        if os.path.basename(token) == "uv" and len(tokens) >= 2 and tokens[1] == "run":
            for run_tok in tokens[2:]:
                if run_tok.startswith("-"):
                    continue
                if os.path.basename(run_tok) in _PYTEST_NAMES:
                    return True
                break

    return False


def main() -> None:
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name in _EXEMPT_SKILLS:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        sys.exit(0)

    cmd = tool_input.get("command", "") or tool_input.get("cmd", "")
    if not cmd:
        sys.exit(0)

    if _is_direct_pytest(cmd):
        reason = (
            f"{TEST_RUNNER_DENY_TRIGGER}. "
            "Use `task test-check` to run tests.\n"
            "`task test-check` handles environment setup, path filtering, "
            "and provides unambiguous PASS/FAIL output.\n"
            "If you need to run a specific test subset, use: "
            "AUTOSKILLIT_TEST_FILTER=none task test-check"
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
