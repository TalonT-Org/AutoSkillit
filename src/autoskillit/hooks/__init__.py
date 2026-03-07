"""PreToolUse hook scripts for AutoSkillit.

This module defines the canonical hook registry — the single source of truth
for all hook definitions. Both hooks.json (plugin manifest) and _hooks.py
(settings.json registration) derive from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.core import pkg_root


@dataclass(frozen=True)
class HookDef:
    """A single PreToolUse hook group: matcher pattern + ordered script list."""

    matcher: str
    scripts: list[str] = field(default_factory=list)


HOOK_REGISTRY: list[HookDef] = [
    HookDef(
        matcher="mcp__.*autoskillit.*__run_skill.*",
        scripts=["skill_cmd_check.py", "quota_check.py", "skill_command_guard.py"],
    ),
    HookDef(
        matcher="mcp__.*autoskillit.*__remove_clone",
        scripts=["remove_clone_guard.py"],
    ),
    HookDef(
        matcher="^(Read|Write|Edit|Bash|Glob|Grep|Agent|WebFetch|WebSearch|NotebookEdit)$",
        scripts=["native_tool_guard.py"],
    ),
]


def generate_hooks_json() -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using absolute paths."""
    hooks_dir = pkg_root() / "hooks"
    entries = []
    for hook_def in HOOK_REGISTRY:
        hooks_list = [
            {
                "type": "command",
                "command": f"python3 {hooks_dir / script}",
            }
            for script in hook_def.scripts
        ]
        entries.append({"matcher": hook_def.matcher, "hooks": hooks_list})
    return {"hooks": {"PreToolUse": entries}}
