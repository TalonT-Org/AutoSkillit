"""Experiment type registry — load bundled and user-defined experiment type specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import get_logger, load_yaml, pkg_root

logger = get_logger(__name__)

BUNDLED_EXPERIMENT_TYPES_DIR: Path = pkg_root() / "recipes" / "experiment-types"


@dataclass
class ExperimentTypeSpec:
    """Specification for a single experiment type."""

    name: str
    classification_triggers: list[str]
    dimension_weights: dict[str, str]
    applicable_lenses: dict[str, str | None]
    red_team_focus: dict[str, str]
    l1_severity: dict[str, str]
    schema_version: str = ""
    priority: int = 999
    is_fallback: bool = False
    dimension_weight_rationale: dict[str, str] = field(default_factory=dict)


def _parse_int_field(data: dict, field: str, default: int, source_path: Path) -> int:
    val = data.get(field, default)
    try:
        return int(val)
    except (ValueError, TypeError) as e:
        name = data.get("name", "?")
        raise TypeError(
            f"Experiment type '{name}' field '{field}' must be an integer: {source_path}"
        ) from e


def _parse_bool_field(data: dict, field: str, default: bool, source_path: Path) -> bool:
    val = data.get(field, default)
    if not isinstance(val, bool):
        name = data.get("name", "?")
        raise TypeError(
            f"Experiment type '{name}' field '{field}' must be a boolean: {source_path}"
        )
    return val


def _parse_experiment_type(data: dict, source_path: Path) -> ExperimentTypeSpec:
    if "name" not in data:
        raise ValueError(f"Experiment type YAML missing 'name' field: {source_path}")
    for f in (
        "dimension_weights",
        "applicable_lenses",
        "red_team_focus",
        "l1_severity",
        "dimension_weight_rationale",
    ):
        val = data.get(f)
        if val is not None and not isinstance(val, dict):
            raise TypeError(
                f"Experiment type '{data['name']}' field '{f}' must be a dict, "
                f"got {type(val).__name__}: {source_path}"
            )
    return ExperimentTypeSpec(
        name=data["name"],
        classification_triggers=list(data.get("classification_triggers", [])),
        dimension_weights=dict(data.get("dimension_weights", {})),
        applicable_lenses=dict(data.get("applicable_lenses", {})),
        red_team_focus=dict(data.get("red_team_focus", {})),
        l1_severity=dict(data.get("l1_severity", {})),
        schema_version=str(data.get("schema_version", "")),
        priority=_parse_int_field(data, "priority", 999, source_path),
        is_fallback=_parse_bool_field(data, "is_fallback", False, source_path),
        dimension_weight_rationale=dict(data.get("dimension_weight_rationale", {})),
    )


def _load_types_from_dir(directory: Path) -> dict[str, ExperimentTypeSpec]:
    if not directory.exists():
        return {}
    result: dict[str, ExperimentTypeSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = load_yaml(path)
            if isinstance(data, dict):
                spec = _parse_experiment_type(data, path)
                result[spec.name] = spec
        except Exception:
            logger.warning("Skipping malformed experiment type file: %s", path, exc_info=True)
    return result


def load_all_experiment_types(
    project_dir: Path | None = None,
) -> list[ExperimentTypeSpec]:
    """Load experiment types: bundled types merged with user-defined overrides.

    User-defined types with the same name as a bundled type replace the bundled
    type entirely — no field merging. User-defined types with a new name are added
    alongside bundled types.

    The returned list is sorted by ``(priority, name)`` with ``is_fallback=True``
    entries always appended last.

    Args:
        project_dir: Project root containing optional user-defined overrides at
            ``.autoskillit/experiment-types/``. When ``None``, only bundled types
            are returned.

    Returns:
        Sorted list of ``ExperimentTypeSpec``, fallback entries last.
    """
    types = _load_types_from_dir(BUNDLED_EXPERIMENT_TYPES_DIR)

    if project_dir is not None:
        user_dir = Path(project_dir) / ".autoskillit" / "experiment-types"
        user_types = _load_types_from_dir(user_dir)
        for spec in user_types.values():
            if spec.schema_version and spec.schema_version != "1.0":
                logger.warning(
                    "User experiment type has schema_version mismatch; loading continues",
                    type_name=spec.name,
                    schema_version=spec.schema_version,
                    expected_schema_version="1.0",
                )
        types.update(user_types)

    non_fallback = [s for s in types.values() if not s.is_fallback]
    fallback = [s for s in types.values() if s.is_fallback]
    non_fallback.sort(key=lambda s: (s.priority, s.name))
    fallback.sort(key=lambda s: (s.priority, s.name))
    return non_fallback + fallback


def get_experiment_type_by_name(
    name: str,
    project_dir: Path | None = None,
) -> ExperimentTypeSpec | None:
    """Look up a single experiment type by name.

    Returns the matching spec or None if not found.
    """
    for spec in load_all_experiment_types(project_dir):
        if spec.name == name:
            return spec
    return None
