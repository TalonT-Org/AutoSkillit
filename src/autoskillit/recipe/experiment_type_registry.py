"""Experiment type registry — load bundled and user-defined experiment type specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import load_yaml, pkg_root

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


def _parse_experiment_type(data: dict, source_path: Path) -> ExperimentTypeSpec:
    if "name" not in data:
        raise ValueError(f"Experiment type YAML missing 'name' field: {source_path}")
    return ExperimentTypeSpec(
        name=data["name"],
        classification_triggers=list(data.get("classification_triggers", [])),
        dimension_weights=dict(data.get("dimension_weights", {})),
        applicable_lenses=dict(data.get("applicable_lenses", {})),
        red_team_focus=dict(data.get("red_team_focus", {})),
        l1_severity=dict(data.get("l1_severity", {})),
    )


def _load_types_from_dir(directory: Path) -> dict[str, ExperimentTypeSpec]:
    if not directory.exists():
        return {}
    result: dict[str, ExperimentTypeSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = load_yaml(path)
        if isinstance(data, dict):
            spec = _parse_experiment_type(data, path)
            result[spec.name] = spec
    return result


def load_all_experiment_types(
    project_dir: Path | None = None,
) -> dict[str, ExperimentTypeSpec]:
    """Load experiment types: bundled types merged with user-defined overrides.

    User-defined types with the same name as a bundled type replace the bundled
    type entirely — no field merging. User-defined types with a new name are added
    alongside bundled types.

    Args:
        project_dir: Project root containing optional user-defined overrides at
            ``.autoskillit/experiment-types/``. When ``None``, only bundled types
            are returned.

    Returns:
        Mapping of experiment type name to ``ExperimentTypeSpec``.
    """
    types = _load_types_from_dir(BUNDLED_EXPERIMENT_TYPES_DIR)

    if project_dir is not None:
        user_dir = Path(project_dir) / ".autoskillit" / "experiment-types"
        user_types = _load_types_from_dir(user_dir)
        types.update(user_types)

    return types
