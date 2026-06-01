"""Guard tests for review-design SKILL.md — data_acquisition dimension."""

from pathlib import Path

import pytest

from autoskillit.recipe.experiment_type_registry import load_all_experiment_types

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

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
    for spec in types:
        weight = spec.dimension_weights.get("data_acquisition")
        if weight in ("M", "H"):
            return
    raise AssertionError("data_acquisition must have M or H weight in at least one bundled type")


def test_data_acquisition_rejects_template_syntax() -> None:
    """data_acquisition dimension must specify a STOP for unresolved template tokens."""
    text = SKILL_PATH.read_text()
    da_start = text.find("#### `data_acquisition`")
    assert da_start != -1, "data_acquisition dimension not found"
    next_dim = text.find("####", da_start + 1)
    da_section = text[da_start:next_dim].lower() if next_dim != -1 else text[da_start:].lower()
    placeholder_signals = ["{", "placeholder", "template", "unresolved"]
    assert any(s in da_section for s in placeholder_signals), (
        "data_acquisition must specify template/placeholder syntax validation"
    )
    assert "stop" in da_section, (
        "data_acquisition must produce STOP for unresolvable template tokens"
    )


def test_data_acquisition_enumerates_sub_checks() -> None:
    """data_acquisition must enumerate all named sub-checks in SKILL.md."""
    text = SKILL_PATH.read_text()
    da_start = text.find("#### `data_acquisition`")
    assert da_start != -1
    next_dim = text.find("####", da_start + 1)
    da_section = text[da_start:next_dim].lower() if next_dim != -1 else text[da_start:].lower()
    required_checks = [
        "hypothesis coverage",
        "external source readiness",
        "gitignored path handling",
        "dependency ordering",
        "directive compliance",
    ]
    for check in required_checks:
        assert check in da_section, f"data_acquisition must enumerate sub-check: {check}"


def test_data_acquisition_has_stop_findings() -> None:
    """data_acquisition findings format must include STOP-severity criteria.

    data_acquisition is an L4 dimension — its critical findings produce REVISE via
    the global verdict logic (not STOP via stop_triggers, which is L1+red_team only).
    But the dimension's own findings format must still specify STOP-severity criteria
    so the LLM executing review-design can flag catastrophic data gaps at the
    dimension level.
    """
    text = SKILL_PATH.read_text()
    # Find the section heading (#### heading with backtick-wrapped dimension name)
    da_start = text.find("#### `data_acquisition`")
    assert da_start != -1, "data_acquisition dimension not found"
    next_dim = text.find("####", da_start + 1)
    da_section = text[da_start:next_dim].lower() if next_dim != -1 else text[da_start:].lower()
    assert "stop" in da_section, (
        "data_acquisition findings format must specify STOP-severity criteria"
    )


def test_agent_implementability_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "agent_implementability" in text


def test_agent_implementability_weight_row() -> None:
    """agent_implementability must have H/H/M/M/L weights for the 5 bundled types."""
    types = load_all_experiment_types()
    by_name = {s.name: s for s in types}
    expected = {
        "benchmark": "H",
        "configuration_study": "H",
        "causal_inference": "M",
        "robustness_audit": "M",
        "exploratory": "L",
    }
    for type_name, exp_weight in expected.items():
        spec = by_name.get(type_name)
        assert spec is not None, f"Bundled type {type_name!r} not found"
        actual = spec.dimension_weights.get("agent_implementability")
        assert actual == exp_weight, (
            f"{type_name}.agent_implementability = {actual!r}, expected {exp_weight!r}"
        )


def test_scope_alignment_weight_row() -> None:
    """scope_alignment must have H/M/M/M/L weights for the 5 original bundled types."""
    types = load_all_experiment_types()
    by_name = {s.name: s for s in types}
    expected = {
        "causal_inference": "H",
        "benchmark": "M",
        "configuration_study": "M",
        "robustness_audit": "M",
        "exploratory": "L",
    }
    for type_name, exp_weight in expected.items():
        spec = by_name.get(type_name)
        assert spec is not None, f"Bundled type {type_name!r} not found"
        actual = spec.dimension_weights.get("scope_alignment")
        assert actual == exp_weight, (
            f"{type_name}.scope_alignment = {actual!r}, expected {exp_weight!r}"
        )
