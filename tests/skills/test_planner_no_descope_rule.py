"""Contract tests: rectify and make-plan SKILL.md must contain no-descope rule."""

from __future__ import annotations

import re

import pytest

from tests._helpers import extract_always_block

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


def _read_rectify() -> str:
    from autoskillit.core.paths import pkg_root

    return (pkg_root() / "skills_extended" / "rectify" / "SKILL.md").read_text()


def _read_make_plan() -> str:
    from autoskillit.core.paths import pkg_root

    return (pkg_root() / "skills_extended" / "make-plan" / "SKILL.md").read_text()


_NO_DESCOPE_PATTERN = re.compile(
    r"plan\s+must\s+cover\s+every\s+(remediation|requirement)\s+item",
    re.IGNORECASE,
)


def test_rectify_contains_no_descope_rule():
    """rectify SKILL.md must contain the no-descope rule in its ALWAYS section."""
    always_block = extract_always_block(_read_rectify())
    assert _NO_DESCOPE_PATTERN.search(always_block), (
        "rectify/SKILL.md ALWAYS section must contain the no-descope rule: "
        "'The plan must cover every remediation/requirement item enumerated in the "
        "source issue; if an item cannot be delivered, stop and surface it — "
        "do not descope it in the plan'"
    )


def test_make_plan_contains_no_descope_rule():
    """make-plan SKILL.md must contain the no-descope rule in its ALWAYS section."""
    always_block = extract_always_block(_read_make_plan())
    assert _NO_DESCOPE_PATTERN.search(always_block), (
        "make-plan/SKILL.md ALWAYS section must contain the no-descope rule: "
        "'The plan must cover every remediation/requirement item enumerated in the "
        "source issue; if an item cannot be delivered, stop and surface it — "
        "do not descope it in the plan'"
    )
