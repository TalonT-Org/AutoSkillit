"""Codex config.toml hook generation and synchronization."""

from __future__ import annotations

from pathlib import Path

from autoskillit.execution import (
    _read_codex_config,
    _write_codex_config,
)
from autoskillit.hook_registry import (
    HOOKS_DIR,
    _build_hook_command,
    _build_hook_entry,
)
from autoskillit.hooks import HOOK_REGISTRY


def generate_codex_hooks_config() -> list[dict]:
    """Generate Codex config.toml hooks entries from HOOK_REGISTRY.

    Skips interactive_only hooks. Consolidates entries sharing the same
    (event_type, matcher) key.
    """
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in HOOK_REGISTRY:
        if hook_def.session_scope == "interactive_only":
            continue
        key = (hook_def.event_type, hook_def.matcher)
        hook_commands = [
            _build_hook_command(HOOKS_DIR, script, hook_def.timeout_seconds)
            for script in hook_def.scripts
        ]
        if key not in groups:
            entry = _build_hook_entry(hook_def, hook_commands)
            entry["event"] = hook_def.event_type
            groups[key] = entry
        else:
            groups[key]["hooks"].extend(hook_commands)
    return list(groups.values())


def _is_autoskillit_hook_entry(entry: dict) -> bool:
    """Check if a Codex hooks config entry belongs to autoskillit."""
    hooks_dir_str = str(HOOKS_DIR)
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if "/autoskillit/" in cmd or hooks_dir_str in cmd or "_dispatch.py" in cmd:
            return True
    return False


def sync_hooks_to_codex_config(config_path: Path | None = None) -> bool:
    """Sync autoskillit hooks to Codex config.toml.

    Returns True if the config was changed, False if already up to date.
    """
    if config_path is None:
        config_path = Path.home() / ".codex" / "config.toml"
    config = _read_codex_config(config_path)
    existing_hooks = config.get("hooks", [])
    foreign_hooks = [e for e in existing_hooks if not _is_autoskillit_hook_entry(e)]
    fresh = generate_codex_hooks_config()
    new_hooks = foreign_hooks + fresh
    if new_hooks == existing_hooks:
        return False
    config["hooks"] = new_hooks
    _write_codex_config(config_path, config)
    return True
