#!/usr/bin/env python3
"""Deny unsafe raw GitHub mutations at Bash and run_cmd boundaries.

The structured ``post_pr_review`` tool is the sole pull-request review
publication authority. Other raw GitHub writes are allowed only when this
guard can prove that the command issues exactly one non-review mutation.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._command_classification import (
        GitHubMutationKind,
        GitHubMutationStatus,
        analyze_github_mutations,
    )
else:
    from _command_classification import (  # noqa: E402
        GitHubMutationKind,
        GitHubMutationStatus,
        analyze_github_mutations,
    )

GITHUB_MUTATION_DENY_TRIGGER: str = "Unsafe raw GitHub mutation is prohibited"

_RUN_CMD_SUFFIX = "__run_cmd"
_REVIEW_KINDS: frozenset[GitHubMutationKind] = frozenset(
    {
        GitHubMutationKind.PULL_REVIEW,
        GitHubMutationKind.PULL_REVIEW_COMMENT,
        GitHubMutationKind.PULL_REVIEW_REPLY,
        GitHubMutationKind.GRAPHQL_REVIEW,
    }
)
_DENY_REASON = (
    "Unsafe raw GitHub mutation is prohibited. Use post_pr_review for pull-request "
    "review publication, or the appropriate structured AutoSkillit mutation tool. "
    "Raw review writes, multiple writes, and unresolved mutation commands fail closed."
)


def _deny() -> NoReturn:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": _DENY_REASON,
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    raise SystemExit(0)


def _normalized_input(data: dict[str, Any]) -> tuple[str, str] | None:
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return None

    if tool_name == "Bash":
        if "cmd" in tool_input:
            _deny()
        command = tool_input.get("command")
        cwd = data.get("cwd", "")
    elif tool_name.endswith(_RUN_CMD_SUFFIX) and "autoskillit" in tool_name:
        if "command" in tool_input:
            _deny()
        command = tool_input.get("cmd")
        cwd = tool_input.get("cwd", "")
    else:
        return None

    if not isinstance(command, str):
        _deny()
    if cwd is None:
        cwd = ""
    if not isinstance(cwd, str):
        _deny()
    if cwd and not os.path.isabs(cwd):
        _deny()
    return (command, cwd)


def main() -> None:
    try:
        loaded = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        raise SystemExit(0)
    if not isinstance(loaded, dict):
        raise SystemExit(0)

    normalized = _normalized_input(loaded)
    if normalized is None:
        raise SystemExit(0)
    command, cwd = normalized
    analysis = analyze_github_mutations(command, cwd=cwd)

    if analysis.status in {
        GitHubMutationStatus.MULTIPLE,
        GitHubMutationStatus.UNRESOLVED,
    }:
        _deny()
    if any(record.kind in _REVIEW_KINDS for record in analysis.mutations):
        _deny()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
