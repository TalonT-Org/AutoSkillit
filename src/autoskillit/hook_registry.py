"""Canonical hook registry — single source of truth for all hook definitions.

Both hooks.json (plugin manifest) and _hooks.py (settings.json registration)
derive from this registry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

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
        scripts=["skill_cmd_guard.py", "quota_guard.py", "skill_command_guard.py"],
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
        scripts=["pretty_output_hook.py"],
    ),
    HookDef(
        event_type="PostToolUse",
        matcher=r"mcp__.*autoskillit.*__run_skill.*",
        scripts=["token_summary_hook.py", "quota_post_hook.py"],
    ),
    HookDef(
        event_type="SessionStart",
        scripts=["session_start_hook.py"],
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


# ---------------------------------------------------------------------------
# Hook diagnostic utilities — shared between cli/ and server/ (both L3).
# Placed here (package root, L0-accessible) to avoid L3-to-L3 peer imports.
# ---------------------------------------------------------------------------


def _claude_settings_path(scope: str) -> Path:
    """Return the Claude Code settings.json path for the given scope."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"


def _load_settings_data(settings_path: Path) -> dict:
    """Read and parse settings.json; return empty dict on any error."""
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def canonical_script_basenames() -> frozenset[str]:
    """Return the set of all known autoskillit hook script basenames."""
    return frozenset(s for h in HOOK_REGISTRY for s in h.scripts)


def _is_own_hook(command: str) -> bool:
    """Check if a hook command belongs to autoskillit (any format)."""
    if "autoskillit" in command:
        return True
    known_scripts = canonical_script_basenames()
    return any(command.endswith(script) or f"/{script}" in command for script in known_scripts)


def _extract_script_basenames(hooks_dict: dict) -> set[str]:
    """Extract autoskillit hook script basenames from a hooks dict.

    Filters to autoskillit-owned commands only, then normalizes
    to bare script filenames for installation-path-agnostic comparison.
    """
    return {
        Path(cmd.split()[-1]).name
        for event_entries in hooks_dict.values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for hook in entry.get("hooks", [])
        if (cmd := hook.get("command", "")) and _is_own_hook(cmd)
    }


class HookDriftResult(NamedTuple):
    """Bidirectional hook drift counts."""

    missing: int  # canonical − deployed (hooks not yet deployed)
    orphaned: int  # deployed − canonical (ghost hooks, fatal ENOENT risk)
    orphaned_cmds: frozenset[str] = frozenset()


def _count_hook_registry_drift(settings_path: Path) -> HookDriftResult:
    """Return bidirectional hook drift counts between canonical and deployed settings.json."""
    deployed_data = _load_settings_data(settings_path)
    canonical_basenames = canonical_script_basenames()
    deployed_basenames = _extract_script_basenames(deployed_data.get("hooks", {}))
    orphaned = deployed_basenames - canonical_basenames
    return HookDriftResult(
        missing=len(canonical_basenames - deployed_basenames),
        orphaned=len(orphaned),
        orphaned_cmds=frozenset(orphaned),
    )


def find_broken_hook_scripts(settings_path: Path) -> list[str]:
    """Return list of hook commands whose script files do not exist on disk."""
    data = _load_settings_data(settings_path)
    broken: list[str] = []
    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        for entry in data.get("hooks", {}).get(event_type, []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                parts = cmd.split()
                if len(parts) >= 2:
                    if not Path(parts[-1]).is_file():
                        broken.append(cmd)
    return broken
