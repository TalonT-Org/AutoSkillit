"""Canonical hook registry — single source of truth for all hook definitions.

Both hooks.json (plugin manifest) and _hooks.py (settings.json registration)
derive from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from autoskillit.core import pkg_root


@dataclass(frozen=True)
class HookDef:
    """A single hook group: event type, matcher pattern, and ordered script list."""

    matcher: str = ""
    event_type: Literal["PreToolUse", "PostToolUse", "SessionStart"] = "PreToolUse"
    scripts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.event_type != "SessionStart" and not self.matcher:
            raise ValueError(
                f"HookDef with event_type={self.event_type!r} requires a non-empty matcher"
            )


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
        matcher=r"mcp__.*autoskillit.*__open_kitchen.*",
        scripts=["open_kitchen_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__merge_worktree",
        scripts=["branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__push_to_remote",
        scripts=["branch_protection_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__run_cmd",
        scripts=["unsafe_install_guard.py"],
    ),
    HookDef(
        matcher=r"Write|Edit",
        scripts=["generated_file_write_guard.py"],
    ),
    HookDef(
        matcher=r"mcp__.*autoskillit.*__(run_skill|run_cmd|run_python).*",
        scripts=["headless_orchestration_guard.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher="mcp__.*autoskillit.*",
        scripts=["pretty_output.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__run_skill.*",
        scripts=["token_summary_appender.py", "quota_post_check.py"],
    ),
    HookDef(
        event_type="SessionStart",
        scripts=["session_start_reminder.py"],
    ),
]


def _build_hook_entry(hook_def: HookDef, hook_commands: list[dict]) -> dict:
    """Build the per-entry dict for a hook definition.

    SessionStart entries omit the 'matcher' key; all others include it.
    This is the single authoritative formatter for both hooks.json and
    settings.json generation.
    """
    if hook_def.event_type == "SessionStart":
        return {"hooks": hook_commands}
    return {"matcher": hook_def.matcher, "hooks": hook_commands}


def generate_hooks_json() -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using absolute paths."""
    hooks_dir = pkg_root() / "hooks"
    by_event: dict[str, list] = {}
    for hook_def in HOOK_REGISTRY:
        hook_commands = [
            {"type": "command", "command": f"python3 {hooks_dir / script}"}
            for script in hook_def.scripts
        ]
        by_event.setdefault(hook_def.event_type, []).append(
            _build_hook_entry(hook_def, hook_commands)
        )
    return {"hooks": by_event}
