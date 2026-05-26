from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, read_versioned_json, write_versioned_json

logger = get_logger(__name__)

LIFECYCLE_REGISTRY_VERSION = 1

_REGISTRY_FILENAME = "lifecycle_registry.json"
_LEGACY_FILENAME = "absorption_registry.json"


def record_lifecycle_event(planner_dir: Path, key: str, data: list[str] | dict[str, Any]) -> None:
    wp_dir = planner_dir / "work_packages"
    wp_dir.mkdir(parents=True, exist_ok=True)
    registry_path = wp_dir / _REGISTRY_FILENAME

    existing: dict[str, Any] = {
        "voided_phases": [],
        "voided_assignments": [],
        "absorbed": {},
        "voided_wps": {},
    }
    if registry_path.exists():
        loaded = read_versioned_json(registry_path, LIFECYCLE_REGISTRY_VERSION)
        if loaded:
            existing = {
                "voided_phases": loaded.get("voided_phases", []),
                "voided_assignments": loaded.get("voided_assignments", []),
                "absorbed": loaded.get("absorbed", {}),
                "voided_wps": loaded.get("voided_wps", {}),
            }
        else:
            logger.warning(
                "lifecycle_registry_unreadable",
                path=str(registry_path),
                hint="file exists but could not be parsed; merging with blank default",
            )

    if isinstance(data, list):
        current = existing.get(key, [])
        merged = list(dict.fromkeys(current + data))
        existing[key] = merged
    elif isinstance(data, dict):
        current = existing.get(key, {})
        current.update(data)
        existing[key] = current

    write_versioned_json(registry_path, existing, schema_version=LIFECYCLE_REGISTRY_VERSION)


def load_lifecycle_registry(planner_dir: Path) -> dict[str, Any]:
    wp_dir = planner_dir / "work_packages"
    registry_path = wp_dir / _REGISTRY_FILENAME

    if registry_path.exists():
        loaded = read_versioned_json(registry_path, LIFECYCLE_REGISTRY_VERSION)
        if loaded:
            return {
                "voided_phases": loaded.get("voided_phases", []),
                "voided_assignments": loaded.get("voided_assignments", []),
                "absorbed": loaded.get("absorbed", {}),
                "voided_wps": loaded.get("voided_wps", {}),
            }

    legacy_path = wp_dir / _LEGACY_FILENAME
    if legacy_path.exists():
        logger.warning("lifecycle_registry_fallback", path=str(legacy_path))
        loaded = read_versioned_json(legacy_path, 1)
        if loaded:
            return {
                "voided_phases": [],
                "voided_assignments": [],
                "absorbed": loaded.get("absorbed", {}),
                "voided_wps": {},
            }

    return {"voided_phases": [], "voided_assignments": [], "absorbed": {}, "voided_wps": {}}
