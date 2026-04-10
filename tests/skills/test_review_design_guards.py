"""Guard tests for review-design SKILL.md — data_acquisition dimension."""

from pathlib import Path

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
    """data_acquisition must be M-weight minimum to influence verdict."""
    text = SKILL_PATH.read_text()
    lines = text.split("\n")
    for line in lines:
        if "data_acquisition" in line and "|" in line:
            assert "M" in line or "H" in line, (
                "data_acquisition must have M or H weight in at least one experiment type"
            )
            return
    raise AssertionError("data_acquisition not found in weight table")


def test_agent_implementability_dimension_exists() -> None:
    text = SKILL_PATH.read_text()
    assert "agent_implementability" in text


def test_agent_implementability_weight_row() -> None:
    """agent_implementability must have H|H|M|M|L weights in the matrix."""
    text = SKILL_PATH.read_text()
    for line in text.split("\n"):
        if "agent_implementability" in line and "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) == 6:  # dimension name + 5 weights
                assert cells[1] == "H", f"benchmark weight should be H, got {cells[1]}"
                assert cells[2] == "H", f"config_study weight should be H, got {cells[2]}"
                assert cells[3] == "M", f"causal_inf weight should be M, got {cells[3]}"
                assert cells[4] == "M", f"robust_audit weight should be M, got {cells[4]}"
                assert cells[5] == "L", f"exploratory weight should be L, got {cells[5]}"
                return
    raise AssertionError("agent_implementability not found in weight table")
