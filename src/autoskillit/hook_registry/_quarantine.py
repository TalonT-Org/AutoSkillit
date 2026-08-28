"""Hook-payload validation and durable, content-addressed quarantine markers."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from autoskillit.core import (
    atomic_write,
    installed_plugin_artifact_manifest_path,
    installed_plugin_cache_dir,
)

from ._drift import find_broken_hook_scripts


def hook_quarantine_marker_path(
    manifest_path: Path,
    raw_hooks: bytes,
) -> Path:
    """Return the external marker path for one exact hooks.json payload."""
    digest = sha256(raw_hooks).hexdigest()
    return manifest_path.parent / f"{manifest_path.name}.hook-quarantine-{digest}"


def is_hook_payload_quarantined(manifest_path: Path, raw_hooks: bytes) -> bool:
    """Return whether this exact hooks payload has a durable rejection marker."""
    return hook_quarantine_marker_path(manifest_path, raw_hooks).is_file()


def quarantine_hook_payload(manifest_path: Path, raw_hooks: bytes) -> Path:
    """Durably mark this exact hooks payload as terminally broken."""
    marker_path = hook_quarantine_marker_path(manifest_path, raw_hooks)
    if not marker_path.is_file():
        atomic_write(marker_path, "", strict_durability=True)
    return marker_path


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
