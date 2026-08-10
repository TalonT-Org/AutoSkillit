"""Locked, validated access to the kitchen session overlay."""

from __future__ import annotations

import fcntl
import json
import types
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from autoskillit.config import CoreRunConfig, FleetConfig, RunSkillConfig
from autoskillit.core import OVERLAY_MAPPING_DOMAINS, atomic_write
from autoskillit.server._misc import _hook_config_overlay_path


class OverlayStateError(ValueError):
    """The persisted session overlay is malformed or invalid."""


def _matches_annotation(value: object, annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in (types.UnionType,):
        return any(_matches_annotation(value, member) for member in get_args(annotation))
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    if origin is dict:
        return isinstance(value, dict)
    if origin is list:
        return isinstance(value, list)
    if annotation is type(None):
        return value is None
    return True


def _validate_dataclass_domain(name: str, data: Mapping[str, object], cls: type[Any]) -> None:
    allowed = {item.name for item in fields(cls)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise OverlayStateError(f"Unknown {name} configuration keys: {unknown}")

    hints = get_type_hints(cls)
    for key, value in data.items():
        if not _matches_annotation(value, hints[key]):
            raise OverlayStateError(
                f"Invalid {name}.{key} type: expected {hints[key]!r}, got {type(value).__name__}"
            )

    try:
        candidate = replace(cls(), **data)
        if isinstance(candidate, FleetConfig):
            candidate.validate(feature_enabled=True)
    except (TypeError, ValueError) as exc:
        raise OverlayStateError(f"Invalid {name} configuration: {exc}") from exc


def validate_overlay_document(value: object) -> dict[str, Any]:
    """Return an independent validated overlay mapping."""
    if not isinstance(value, Mapping):
        raise OverlayStateError(
            f"Session overlay root must be a mapping, got {type(value).__name__}"
        )
    overlay = deepcopy(dict(value))
    for name in OVERLAY_MAPPING_DOMAINS:
        if name in overlay and not isinstance(overlay[name], Mapping):
            raise OverlayStateError(
                f"Session overlay domain {name!r} must be a mapping, "
                f"got {type(overlay[name]).__name__}"
            )
    if "order" in overlay:
        _validate_dataclass_domain("order", overlay["order"], RunSkillConfig)
    if "fleet" in overlay:
        _validate_dataclass_domain("fleet", overlay["fleet"], FleetConfig)
    if "core" in overlay:
        _validate_dataclass_domain("core", overlay["core"], CoreRunConfig)
    return overlay


def _read_overlay_unlocked(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return validate_overlay_document(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as exc:
        raise OverlayStateError(f"Unable to read session overlay: {exc}") from exc


@contextmanager
def locked_overlay(project_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Hold the stable overlay lock for a complete transaction."""
    overlay_path = _hook_config_overlay_path(project_dir)
    lock_path = overlay_path.with_suffix(".lock")
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield overlay_path, _read_overlay_unlocked(overlay_path)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_overlay_locked(path: Path, value: object) -> dict[str, Any]:
    """Validate and atomically replace an overlay while its lock is held."""
    overlay = validate_overlay_document(value)
    atomic_write(path, json.dumps(overlay))
    return overlay


def read_overlay(project_dir: Path) -> dict[str, Any]:
    """Read a validated overlay under its stable lock."""
    with locked_overlay(project_dir) as (_, overlay):
        return overlay


def update_overlay(
    project_dir: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Apply one locked, validated read-modify-write transaction."""
    with locked_overlay(project_dir) as (path, overlay):
        mutate(overlay)
        return write_overlay_locked(path, overlay)
