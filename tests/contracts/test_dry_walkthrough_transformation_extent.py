"""Contract test: dry-walkthrough SKILL.md must check transformation extent/scope."""

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
    / "dry-walkthrough"
    / "SKILL.md"
)


def test_dry_walkthrough_contains_transformation_extent_check():
    """dry-walkthrough/SKILL.md Step 2 must include a block/statement transformation extent check."""
    content = _SKILL_MD.read_text()
    assert re.search(
        r"block.statement.*transformation|structural.*boundary",
        content,
        re.DOTALL | re.IGNORECASE,
    ), (
        "dry-walkthrough/SKILL.md must contain a check for block/statement transformation extent "
        "or structural boundary claims in plans"
    )
