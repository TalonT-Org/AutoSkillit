"""Structural test: destructive tools have PreToolUse hook coverage."""

import re

from autoskillit.hook_registry import HOOK_REGISTRY

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
