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
        tokens = [verb, *rest]

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
            run_tokens = tokens[2:]
            for idx, run_tok in enumerate(run_tokens):
                if run_tok.startswith("-"):
                    continue
                run_basename = os.path.basename(run_tok)
                if run_basename in _PYTEST_NAMES:
                    return True
                # uv run python -m pytest / uv run python3 -m py.test
                if re.match(r"python3?$", run_basename):
                    remaining = run_tokens[idx + 1 :]
                    if (
                        len(remaining) >= 2
                        and remaining[0] == "-m"
                        and remaining[1] in _PYTEST_NAMES
                    ):
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

    cmd = parse_hook_command(data).command or ""
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
