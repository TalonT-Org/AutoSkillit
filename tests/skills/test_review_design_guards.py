"""Guard tests for review-design SKILL.md — data_acquisition dimension."""

from pathlib import Path

from autoskillit.recipe.experiment_type_registry import load_all_experiment_types

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-design"
    / "SKILL.md"
)


def test_data_acquisition_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "data_acquisition" in text


def test_data_acquisition_not_l_weight() -> None:
    """data_acquisition must be M-weight minimum to influence verdict in at least one type."""
    types = load_all_experiment_types()
    for name, spec in types.items():
        weight = spec.dimension_weights.get("data_acquisition")
        if weight in ("M", "H"):
            return
    raise AssertionError("data_acquisition must have M or H weight in at least one bundled type")


def test_agent_implementability_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "agent_implementability" in text


def test_agent_implementability_weight_row() -> None:
    """agent_implementability must have H/H/M/M/L weights for the 5 bundled types."""
    types = load_all_experiment_types()
    expected = {
        "benchmark": "H",
        "configuration_study": "H",
        "causal_inference": "M",
        "robustness_audit": "M",
        "exploratory": "L",
    }
    for type_name, exp_weight in expected.items():
        spec = types.get(type_name)
        assert spec is not None, f"Bundled type {type_name!r} not found"
        actual = spec.dimension_weights.get("agent_implementability")
        assert actual == exp_weight, (
            f"{type_name}.agent_implementability = {actual!r}, expected {exp_weight!r}"
        )
