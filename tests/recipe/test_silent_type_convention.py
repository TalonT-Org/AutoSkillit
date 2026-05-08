"""Verify silent-type-convention.md exists and contains required sections."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONVENTION_PATH = _REPO_ROOT / "docs" / "research" / "silent-type-convention.md"


def test_silent_type_convention_exists():
    assert CONVENTION_PATH.exists()


def test_convention_has_detection_criteria():
    content = CONVENTION_PATH.read_text()
    assert "dimension_weights" in content
    assert "mandatory_figures" in content


def test_convention_has_advisory_schema():
    content = CONVENTION_PATH.read_text()
    assert "advisory_context" in content
    assert "subject_kind" in content
    assert "requires_decision" in content


def test_convention_has_write_targets():
    content = CONVENTION_PATH.read_text()
    assert "design-review-dashboard.md" in content
    assert "visualization-plan-trace.md" in content
