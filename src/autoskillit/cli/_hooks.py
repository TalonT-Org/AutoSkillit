"""Hook registration helpers for the install command."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import atomic_write, is_git_worktree, pkg_root
from autoskillit.hook_registry import (
    _build_hook_entry,
    _claude_settings_path,  # noqa: F401 — re-exported; cli/__init__ + _stale_check + _init_helpers import from here
    _load_settings_data,
)
from autoskillit.hook_registry import (
    _is_own_hook as _is_autoskillit_hook_command,
)
from autoskillit.hooks import HOOK_REGISTRY


def _write_settings_data(settings_path: Path, data: dict) -> None:
    """Write settings data back atomically, creating parent dirs if needed."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(settings_path, json.dumps(data, indent=2))


def _evict_stale_autoskillit_hooks(settings_path: Path) -> None:
    """Remove all autoskillit-related hook entries from settings.json (all event types).

    This is a destructive-then-rebuild approach: evict everything autoskillit-
    related, then let sync_hooks_to_settings() write canonical entries fresh.
    Covers all legacy formats (python3 -m, old absolute paths, ${CLAUDE_PLUGIN_ROOT}).
    """
    data = _load_settings_data(settings_path)
    hooks = data.get("hooks", {})
    for event_type in list(hooks.keys()):
        event_list: list[dict] = hooks.get(event_type, [])
        cleaned = []
        for entry in event_list:
            entry_hooks = entry.get("hooks", [])
            non_autoskillit = [
                h for h in entry_hooks if not _is_autoskillit_hook_command(h.get("command", ""))
            ]
            if non_autoskillit:
                entry["hooks"] = non_autoskillit
                cleaned.append(entry)
        hooks[event_type] = cleaned
    _write_settings_data(settings_path, data)


def sync_hooks_to_settings(settings_path: Path) -> None:
    """Write all HOOK_REGISTRY hooks to settings.json.

    Must be called after _evict_stale_autoskillit_hooks() — assumes no
    autoskillit entries are present when this function runs.
    Each HookDef becomes one entry under its event_type with all its scripts as ordered commands.
    """
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH

    root = pkg_root()
    if is_git_worktree(root):
        raise RuntimeError(
            f"Refusing to sync hooks: pkg_root() resolves to a git linked worktree "
            f"({root}). Hook paths written from a transient worktree would become "
            f"dangling after worktree deletion. Use 'task install-worktree' instead "
            f"of 'autoskillit init' when working in a worktree."
        )
    hooks_dir = root / "hooks"
    data = _load_settings_data(settings_path)
    # Consolidate HookDef entries sharing the same (event_type, matcher) into a
    # single settings.json entry so Claude Code sees no duplicate matchers.
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in HOOK_REGISTRY:
        key = (hook_def.event_type, hook_def.matcher)
        hooks_list = [
            {
                "type": "command",
                "command": f"python3 {hooks_dir / script}",
                **(
                    {"timeout": hook_def.timeout_seconds}
                    if hook_def.timeout_seconds is not None
                    else {}
                ),
            }
            for script in hook_def.scripts
        ]
        if key not in groups:
            groups[key] = _build_hook_entry(hook_def, hooks_list)
        else:
            groups[key]["hooks"].extend(hooks_list)
    for (event_type, _), entry in groups.items():
        event_list: list[dict] = data.setdefault("hooks", {}).setdefault(event_type, [])
        event_list.append(entry)
    data["_autoskillit_registry_hash"] = HOOK_REGISTRY_HASH
    _write_settings_data(settings_path, data)


def sweep_all_scopes_for_orphans(project_root: Path | None = None) -> list[str]:
    """Evict stale autoskillit hooks from every Claude Code settings scope.

    Calls _evict_stale_autoskillit_hooks on user, project, and local scopes
    (project and local only when project_root is given and .claude/ dir exists).

    Returns a list of scope labels where evictions occurred.
    """
    from autoskillit.hook_registry import iter_all_scope_paths

    evicted: list[str] = []
    for scope_label, settings_path in iter_all_scope_paths(project_root):
        before_data = _load_settings_data(settings_path)
        before_str = json.dumps(before_data)
        _evict_stale_autoskillit_hooks(settings_path)
        after_data = _load_settings_data(settings_path)
        after_str = json.dumps(after_data)
        if before_str != after_str:
            evicted.append(scope_label)
    return evicted
