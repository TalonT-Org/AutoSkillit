from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, read_versioned_json, write_versioned_json

logger = get_logger(__name__)

LIFECYCLE_REGISTRY_VERSION = 1

_REGISTRY_FILENAME = "lifecycle_registry.json"
_LEGACY_FILENAME = "absorption_registry.json"


class LifecycleCategory(StrEnum):
    VOIDED_PHASES = "voided_phases"
    VOIDED_ASSIGNMENTS = "voided_assignments"
    ABSORBED = "absorbed"
    VOIDED_WPS = "voided_wps"
    ARCHIVED_STUBS = "archived_stubs"


LIFECYCLE_CATEGORY_DEFAULTS: dict[LifecycleCategory, list | dict] = {
    LifecycleCategory.VOIDED_PHASES: [],
    LifecycleCategory.VOIDED_ASSIGNMENTS: [],
    LifecycleCategory.ABSORBED: {},
    LifecycleCategory.VOIDED_WPS: {},
    LifecycleCategory.ARCHIVED_STUBS: {},
}

for _cat in LifecycleCategory:
    if _cat not in LIFECYCLE_CATEGORY_DEFAULTS:
        raise AssertionError(
            f"LifecycleCategory.{_cat.name} missing from LIFECYCLE_CATEGORY_DEFAULTS"
        )
del _cat


def record_lifecycle_event(
    planner_dir: Path, key: LifecycleCategory, data: list[str] | dict[str, Any]
) -> None:
    wp_dir = planner_dir / "work_packages"
    wp_dir.mkdir(parents=True, exist_ok=True)
    registry_path = wp_dir / _REGISTRY_FILENAME

    existing: dict[str, Any] = {
        cat.value: type(LIFECYCLE_CATEGORY_DEFAULTS[cat])() for cat in LifecycleCategory
    }
    if registry_path.exists():
        loaded = read_versioned_json(registry_path, LIFECYCLE_REGISTRY_VERSION)
        if loaded:
            existing = {
                cat.value: loaded.get(cat.value, type(LIFECYCLE_CATEGORY_DEFAULTS[cat])())
                for cat in LifecycleCategory
            }
        else:
            logger.warning(
                "lifecycle_registry_unreadable",
                path=str(registry_path),
                hint="file exists but could not be parsed; merging with blank default",
            )

    key_str = str(key)
    if isinstance(data, list):
        current = existing.get(key_str, [])
        merged = list(dict.fromkeys(current + data))
        existing[key_str] = merged
    elif isinstance(data, dict):
        current = existing.get(key_str, {})
        current.update(data)
        existing[key_str] = current

    write_versioned_json(registry_path, existing, schema_version=LIFECYCLE_REGISTRY_VERSION)


def load_lifecycle_registry(planner_dir: Path) -> dict[str, Any]:
    wp_dir = planner_dir / "work_packages"
    registry_path = wp_dir / _REGISTRY_FILENAME

    defaults: dict[str, Any] = {
        cat.value: type(LIFECYCLE_CATEGORY_DEFAULTS[cat])() for cat in LifecycleCategory
    }

    if registry_path.exists():
        loaded = read_versioned_json(registry_path, LIFECYCLE_REGISTRY_VERSION)
        if loaded:
            result = dict(defaults)
            result.update(loaded)
            return result

    legacy_path = wp_dir / _LEGACY_FILENAME
    if legacy_path.exists():
        logger.warning("lifecycle_registry_fallback", path=str(legacy_path))
        loaded = read_versioned_json(legacy_path, 1)
        if loaded:
            result = dict(defaults)
            result["absorbed"] = loaded.get("absorbed", {})
            return result

    return dict(defaults)
