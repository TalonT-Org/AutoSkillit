"""Hook registration helpers for the install command."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import _atomic_write, pkg_root


def _hook_command(script_name: str) -> str:
    """Build an absolute hook command for settings.json registration.

    Uses pkg_root() to resolve the installed package location at registration
    time, producing absolute paths that work regardless of which Python
    interpreter Claude Code uses to run the hook.
    """
    return f"python3 {pkg_root() / 'hooks' / script_name}"


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


def _register_pretooluse_hook(settings_path: Path, matcher: str, command: str) -> None:
    """Idempotently register a single PreToolUse hook entry in settings.json.

    Idempotency checks:
    - Returns early if any existing entry has the same ``matcher``.
    - Returns early if any existing entry has the same ``command`` (regardless of matcher).
    """
    data = _load_settings_data(settings_path)
    hooks = data.setdefault("hooks", {})
    pretooluse: list[dict] = hooks.setdefault("PreToolUse", [])

    for entry in pretooluse:
        if entry.get("matcher") == matcher:
            return  # matcher already registered
        if any(h.get("command") == command for h in entry.get("hooks", [])):
            return  # command already registered under a different matcher

    pretooluse.append(
        {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        }
    )
    _write_settings_data(settings_path, data)


def _register_quota_hook(settings_path: Path) -> None:
    """Idempotently add the quota PreToolUse hook to .claude/settings.json."""
    _register_pretooluse_hook(
        settings_path,
        matcher="mcp__.*autoskillit.*__run_skill.*",
        command=_hook_command("quota_check.py"),
    )


def _register_remove_clone_guard_hook(settings_path: Path) -> None:
    """Idempotently add the remove_clone_guard PreToolUse hook to .claude/settings.json."""
    _register_pretooluse_hook(
        settings_path,
        matcher="mcp__.*autoskillit.*__remove_clone",
        command=_hook_command("remove_clone_guard.py"),
    )


def _register_skill_command_guard_hook(settings_path: Path) -> None:
    """Idempotently add the skill_command_guard PreToolUse hook to .claude/settings.json.

    This hook shares the same ``run_skill`` matcher as the quota hook, so it
    appends its command to the existing matcher entry when one already exists.
    """
    data = _load_settings_data(settings_path)
    hooks = data.setdefault("hooks", {})
    pretooluse: list[dict] = hooks.setdefault("PreToolUse", [])

    MATCHER = "mcp__.*autoskillit.*__run_skill.*"
    COMMAND = _hook_command("skill_command_guard.py")

    # Idempotency: return if command already present anywhere
    for entry in pretooluse:
        if any(h.get("command") == COMMAND for h in entry.get("hooks", [])):
            return

    # Add to existing run_skill matcher entry if one exists, else create a new entry
    for entry in pretooluse:
        if entry.get("matcher") == MATCHER:
            entry["hooks"].append({"type": "command", "command": COMMAND})
            _write_settings_data(settings_path, data)
            return

    pretooluse.append(
        {
            "matcher": MATCHER,
            "hooks": [{"type": "command", "command": COMMAND}],
        }
    )
    _write_settings_data(settings_path, data)


def _claude_settings_path(scope: str) -> Path:
    """Return the Claude Code settings.json path for the given scope."""
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return Path.cwd() / ".claude" / "settings.json"
