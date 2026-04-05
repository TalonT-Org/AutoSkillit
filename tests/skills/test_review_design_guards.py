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
