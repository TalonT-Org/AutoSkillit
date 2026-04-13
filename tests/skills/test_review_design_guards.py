"""Guard tests for review-design SKILL.md — data_acquisition dimension."""

from pathlib import Path

import yaml

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-design"
    / "SKILL.md"
)

EXP_TYPES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipes"
    / "experiment-types"
)

_BUNDLED_TYPE_ORDER = [
    "benchmark",
    "causal_inference",
    "configuration_study",
    "exploratory",
    "robustness_audit",
]


def _load_bundled_types() -> dict[str, dict]:
    result = {}
    for path in sorted(EXP_TYPES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        result[data["name"]] = data
    return result


def test_data_acquisition_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "data_acquisition" in text


def test_data_acquisition_not_l_weight() -> None:
    """data_acquisition must be M-weight minimum to influence verdict in at least one type."""
    types = _load_bundled_types()
    for name, data in types.items():
        weight = data.get("dimension_weights", {}).get("data_acquisition")
        if weight in ("M", "H"):
            return
    raise AssertionError("data_acquisition must have M or H weight in at least one bundled type")


def test_agent_implementability_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "agent_implementability" in text


def test_agent_implementability_weight_row() -> None:
    """agent_implementability must have H/H/M/M/L weights for the 5 bundled types."""
    types = _load_bundled_types()
    expected = {
        "benchmark": "H",
        "configuration_study": "H",
        "causal_inference": "M",
        "robustness_audit": "M",
        "exploratory": "L",
    }
    for type_name, exp_weight in expected.items():
        data = types.get(type_name)
        assert data is not None, f"Bundled type {type_name!r} not found"
        actual = data.get("dimension_weights", {}).get("agent_implementability")
        assert actual == exp_weight, (
            f"{type_name}.agent_implementability = {actual!r}, expected {exp_weight!r}"
        )
