"""Plugin-cache hook validation.

Reads each ``hooks.json`` under ``cache_dir/<version>/hooks/hooks.json``
(the real installed layout) and checks that every autoskillit hook
script path exists on disk. Token-bearing commands are expanded against
``hooks_json_path.parent.parent`` — the ``<version>`` incarnation
directory that Claude Code binds ``${CLAUDE_PLUGIN_ROOT}`` to.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import installed_plugin_artifact_manifest_path, installed_plugin_cache_dir

from ._drift import find_broken_hook_scripts
from ._quarantine import is_hook_payload_quarantined


def validate_plugin_cache_hooks(cache_dir: Path | None = None) -> list[str]:
    """Return list of broken hook commands from the plugin cache hooks.json."""
    _cache = cache_dir or installed_plugin_cache_dir(Path.home(), "autoskillit")
    broken: list[str] = []
    if not _cache.is_dir():
        return broken
    for hooks_json_path in _cache.glob("*/hooks/hooks.json"):
        incarnation_dir = hooks_json_path.parent.parent
        try:
            raw_hooks = hooks_json_path.read_bytes()
        except OSError:
            broken.append(f"{hooks_json_path} is unreadable")
            continue
        manifest_path = installed_plugin_artifact_manifest_path(incarnation_dir)
        if is_hook_payload_quarantined(manifest_path, raw_hooks):
            continue
        try:
            payload = json.loads(raw_hooks)
        except (UnicodeDecodeError, json.JSONDecodeError):
            broken.append(f"{hooks_json_path} is not valid JSON")
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("hooks"), dict):
            broken.append(f"{hooks_json_path} has an invalid hooks structure")
            continue
        try:
            findings = find_broken_hook_scripts(
                hooks_json_path,
                expansion_root=incarnation_dir,
            )
        except (AttributeError, TypeError):
            broken.append(f"{hooks_json_path} has an invalid hooks structure")
            continue
        broken.extend(findings)
    return broken
