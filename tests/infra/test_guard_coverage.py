"""Structural test: destructive tools have PreToolUse hook coverage."""

import re

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, RISKY_GIT_OPERATIONS

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

# Tools that perform destructive git operations and MUST have PreToolUse hooks.
DESTRUCTIVE_TOOLS = [
    "merge_worktree",
    "push_to_remote",
    "remove_clone",
]


def test_destructive_tools_have_pretooluse_hooks() -> None:
    """Every tool in DESTRUCTIVE_TOOLS must be matched by at least one
    PreToolUse hook in HOOK_REGISTRY."""
    covered: set[str] = set()
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type != "PreToolUse":
            continue
        pattern = re.compile(hook_def.matcher)
        for tool in DESTRUCTIVE_TOOLS:
            fqn = f"mcp__autoskillit__local__autoskillit__{tool}"
            if pattern.match(fqn):
                covered.add(tool)

    uncovered = set(DESTRUCTIVE_TOOLS) - covered
    assert not uncovered, (
        f"Destructive tools without PreToolUse hooks: {sorted(uncovered)}. "
        f"Add hook entries to HOOK_REGISTRY."
    )


def test_destructive_bash_git_ops_have_pretooluse_hooks() -> None:
    """Every tuple in RISKY_GIT_OPERATIONS must be covered by a Bash|run_cmd PreToolUse hook."""
    bash_matchers: list[re.Pattern[str]] = []
    for hook_def in HOOK_REGISTRY:
        if hook_def.event_type != "PreToolUse":
            continue
        if re.fullmatch(hook_def.matcher, "Bash"):
            bash_matchers.append(re.compile(hook_def.matcher))

    assert bash_matchers, "Expected at least one Bash PreToolUse hook in HOOK_REGISTRY"
    assert RISKY_GIT_OPERATIONS, "RISKY_GIT_OPERATIONS must not be empty"

    # Verify at least one Bash-matching hook exists (structural check — actual
    # detection coverage is enforced by test_risky_git_ops_coverage.py).
    bash_covered = any(m.fullmatch("Bash") for m in bash_matchers)
    assert bash_covered, (
        "No Bash|run_cmd PreToolUse hook found in HOOK_REGISTRY. "
        "Add a HookDef with matcher=r'Bash|mcp__.*autoskillit.*__run_cmd' covering git ops."
    )
