"""Contract test: review-pr SKILL.md must contain severity calibration anchors."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SKILL_MD = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)


def test_review_pr_contains_severity_calibration_examples():
    """review-pr/SKILL.md must contain concrete severity calibration examples."""
    content = _SKILL_MD.read_text()
    assert re.search(r"[Ee]xample.*critical", content, re.DOTALL), (
        "review-pr/SKILL.md must contain an example for 'critical' severity"
    )
    assert re.search(r"[Ee]xample.*warning", content, re.DOTALL), (
        "review-pr/SKILL.md must contain an example for 'warning' severity"
    )
    assert re.search(r"[Ee]xample.*info", content, re.DOTALL), (
        "review-pr/SKILL.md must contain an example for 'info' severity"
    )


def test_review_pr_contains_grouping_rule():
    """review-pr/SKILL.md must contain a severity grouping instruction."""
    content = _SKILL_MD.read_text()
    assert re.search(
        r"(?:same.*structural.*pattern|highest.*severity|Grouping rule)",
        content,
        re.DOTALL | re.IGNORECASE,
    ), "review-pr/SKILL.md must contain a grouping rule for repeated patterns"
