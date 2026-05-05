"""Validate experiment-type rationale document completeness."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]

RATIONALE_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "research" / "experiment-type-rationale.md"
)

EXPECTED_TYPE_SECTIONS = [
    "evidence_synthesis",
    "observational_correlational",
    "instrument_validation",
    "simulation_modeling",
    "factorial_design",
    "single_subject",
    "qualitative_interpretive",
]


@pytest.fixture()
def rationale_text() -> str:
    """Read the rationale document."""
    assert RATIONALE_PATH.exists(), f"Rationale document not found at {RATIONALE_PATH}"
    return RATIONALE_PATH.read_text()


def test_rationale_document_exists() -> None:
    """docs/research/experiment-type-rationale.md exists."""
    assert RATIONALE_PATH.exists()


@pytest.mark.parametrize("type_name", EXPECTED_TYPE_SECTIONS)
def test_rationale_covers_experiment_type(rationale_text: str, type_name: str) -> None:
    """Each new experiment type has a section in the rationale document."""
    assert f"`{type_name}`" in rationale_text, (
        f"Experiment type {type_name!r} not found as a backtick-delimited section heading"
    )


def test_rationale_has_reference_list(rationale_text: str) -> None:
    """Rationale document contains a Reference List section."""
    assert "## Reference List" in rationale_text


def test_rationale_has_no_synthetic_markers(rationale_text: str) -> None:
    """The rationale document itself contains no synthetic citation markers."""
    assert "【" not in rationale_text, "Rationale document contains synthetic 【 marker"
