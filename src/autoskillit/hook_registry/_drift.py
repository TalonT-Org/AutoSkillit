"""Drift detection + broken-script detection across deployed hook configs."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from ._hooks_defs import HookDriftResult
from ._registry_data import HOOK_REGISTRY, PLUGIN_ROOT_TOKEN, RETIRED_SCRIPT_BASENAMES


def _load_settings_data(settings_path: Path) -> dict:
    """Read and parse settings.json; return empty dict on any error."""
    if settings_path.exists():
        try:
            return json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt or unreadable settings.json is indistinguishable from
            # a missing/empty one at the drift-counting layer; surface the
            # reason in a comment rather than silently passing.
            import sys as _sys

            print(
                f"_load_settings_data: {settings_path} unreadable ({exc!r}); treating as empty",
                file=_sys.stderr,
            )
            return {}
    return {}


def canonical_script_basenames() -> frozenset[str]:
    """Return the set of all known autoskillit hook script basenames."""
    return frozenset(s for h in HOOK_REGISTRY for s in h.scripts)


def _is_own_hook(command: str) -> bool:
    """Check if a hook command belongs to autoskillit (any format)."""
    if "autoskillit" in command:
        return True
    if "_dispatch.py" in command:
        return True
    known = canonical_script_basenames() | RETIRED_SCRIPT_BASENAMES
    bare = {Path(s).name for s in known}
    return any(command.endswith(script) or f"/{script}" in command for script in known | bare)


def _extract_script_basenames(hooks_dict: dict) -> set[str]:
    """Extract autoskillit hook script relative paths from a hooks dict.

    Filters to autoskillit-owned commands only, then normalizes
    to hooks-dir-relative paths for installation-path-agnostic comparison.
    """
    result: set[str] = set()
    for event_entries in hooks_dict.values():
        if not isinstance(event_entries, list):
            continue
        for entry in event_entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not cmd or not _is_own_hook(cmd):
                    continue
                parts = cmd.split()
                if "_dispatch.py" in cmd and len(parts) >= 3:
                    logical_name = parts[-1]
                    result.add(logical_name + ".py")
                else:
                    script_path = Path(parts[-1])
                    bare = script_path.name
                    canonical = canonical_script_basenames()
                    matched = next((c for c in canonical if Path(c).name == bare), bare)
                    result.add(matched)
    return result


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


def find_broken_hook_scripts(
    hook_config_path: Path,
    *,
    expansion_root: Path | None = None,
) -> list[str]:
    """Return list of hook commands whose script files do not exist on disk.

    Commands are parsed with ``shlex`` (not bare ``.split()``) so the quoted
    relocatable form (``python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py" name``)
    classifies correctly. A command containing ``PLUGIN_ROOT_TOKEN`` is
    resolved against ``expansion_root`` (the root of the artifact — e.g. the
    plugin-cache incarnation dir — that contains it) before the existence
    check. A token-bearing command with no ``expansion_root`` supplied is
    reported broken (fail-closed, never silently skipped). Commands with a
    plain absolute path (settings.json, dev-mode) are checked as before,
    independent of ``expansion_root``.
    """
    data = _load_settings_data(hook_config_path)
    broken: list[str] = []
    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        for entry in data.get("hooks", {}).get(event_type, []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if not _is_own_hook(cmd):
                    continue
                try:
                    parts = shlex.split(cmd)
                except ValueError as exc:
                    broken.append(f"{cmd}  # shlex parse error: {exc}")
                    continue
                has_dispatcher = any(part.endswith("_dispatch.py") for part in parts)
                if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
                    script_path_str = parts[-2]
                elif has_dispatcher:
                    broken.append(cmd)
                    continue
                elif len(parts) >= 2:
                    script_path_str = parts[-1]
                else:
                    broken.append(cmd)
                    continue
                if PLUGIN_ROOT_TOKEN in script_path_str:
                    if expansion_root is None:
                        broken.append(cmd)
                        continue
                    script_path_str = script_path_str.replace(
                        PLUGIN_ROOT_TOKEN, str(expansion_root)
                    )
                    expansion_root_resolved = expansion_root.resolve()
                    script_path = Path(script_path_str).resolve()
                    if not script_path.is_relative_to(expansion_root_resolved):
                        broken.append(cmd)
                        continue
                else:
                    script_path = Path(script_path_str)
                if not script_path.is_file():
                    broken.append(cmd)
    return broken
