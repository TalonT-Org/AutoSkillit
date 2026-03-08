"""Hook registration helpers for the install command."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import _atomic_write, pkg_root
from autoskillit.hooks import HOOK_REGISTRY


def _load_settings_data(settings_path: Path) -> dict:
    """Read and parse settings.json; return empty dict on any error."""
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _write_settings_data(settings_path: Path, data: dict) -> None:
    """Write settings data back atomically, creating parent dirs if needed."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(settings_path, json.dumps(data, indent=2))


def _is_autoskillit_hook_command(command: str) -> bool:
    """Check if a hook command belongs to autoskillit (any format)."""
    if "autoskillit" in command:
        return True
    known_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    return any(command.endswith(script) or f"/{script}" in command for script in known_scripts)


def _evict_stale_autoskillit_hooks(settings_path: Path) -> None:
    """Remove all autoskillit-related PreToolUse entries from settings.json.

    This is a destructive-then-rebuild approach: evict everything autoskillit-
    related, then let sync_hooks_to_settings() write canonical entries fresh.
    Covers all legacy formats (python3 -m, old absolute paths, ${CLAUDE_PLUGIN_ROOT}).
    """
    data = _load_settings_data(settings_path)
    hooks = data.get("hooks", {})
    pretooluse: list[dict] = hooks.get("PreToolUse", [])
    if not pretooluse:
        return

    cleaned = []
    for entry in pretooluse:
        entry_hooks = entry.get("hooks", [])
        non_autoskillit = [
            h for h in entry_hooks if not _is_autoskillit_hook_command(h.get("command", ""))
        ]
        if non_autoskillit:
            entry["hooks"] = non_autoskillit
            cleaned.append(entry)
    hooks["PreToolUse"] = cleaned
    _write_settings_data(settings_path, data)


def sync_hooks_to_settings(settings_path: Path) -> None:
    """Write all HOOK_REGISTRY hooks to settings.json.

    Must be called after _evict_stale_autoskillit_hooks() — assumes no
    autoskillit entries are present in PreToolUse when this function runs.
    Each HookDef becomes one entry with all its scripts as ordered commands.
    """
    hooks_dir = pkg_root() / "hooks"
    data = _load_settings_data(settings_path)
    pretooluse: list[dict] = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for hook_def in HOOK_REGISTRY:
        hooks_list = [
            {"type": "command", "command": f"python3 {hooks_dir / script}"}
            for script in hook_def.scripts
        ]
        pretooluse.append({"matcher": hook_def.matcher, "hooks": hooks_list})
    _write_settings_data(settings_path, data)


def _claude_settings_path(scope: str) -> Path:
    """Return the Claude Code settings.json path for the given scope."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"
