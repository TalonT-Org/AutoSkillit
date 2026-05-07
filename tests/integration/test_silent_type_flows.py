"""Integration tests for silent-type handling across review-design and vis-lens.

Shared between Work Items 2.3 (#835) and 4.7 (#846).
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVENTION_PATH = REPO_ROOT / "docs" / "research" / "silent-type-convention.md"
REVIEW_DESIGN_SKILL = (
    REPO_ROOT / "src" / "autoskillit" / "skills_extended" / "review-design" / "SKILL.md"
)

ADVISORY_REQUIRED_KEYS = {"verdict", "advisory_context", "requires_decision"}
ADVISORY_CONTEXT_KEYS = {"subject_kind", "subject_name", "reasoning", "reference_framework"}


def test_silent_type_advisory_schema_matches_convention() -> None:
    convention = CONVENTION_PATH.read_text()
    for key in ADVISORY_REQUIRED_KEYS:
        assert key in convention, f"Convention doc missing '{key}'"
    for key in ADVISORY_CONTEXT_KEYS:
        assert key in convention, f"Convention doc missing '{key}'"


def test_review_design_advisory_write_target() -> None:
    convention = CONVENTION_PATH.read_text()
    assert "design-review-dashboard.md" in convention


def test_is_silent_type_aligns_with_convention_threshold() -> None:
    convention = CONVENTION_PATH.read_text()
    assert "6 of 8" in convention or "≥6 of 8" in convention


def test_review_design_skill_references_silent_type_handling() -> None:
    content = REVIEW_DESIGN_SKILL.read_text()
    assert "Silent Type Handling" in content
    assert "is_silent_type" in content
    assert "advisory_context" in content


def test_advisory_schema_in_review_design_skill() -> None:
    content = REVIEW_DESIGN_SKILL.read_text()
    assert "subject_kind: experiment_type" in content
    assert "requires_decision: false" in content
