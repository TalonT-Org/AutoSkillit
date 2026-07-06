"""Contract tests for make-plan multi-part green-gate rule and xfail bridge documentation.

Validates that make-plan/SKILL.md contains the CRITICAL multi-part rules
required for per-part test-gate independence:
- Every part must independently pass the test gate
- Test invalidation changes must be co-located with triggering code (or xfail-bridged)
- ``xfail(strict=True)`` is documented as the canonical bridge mechanism
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_MD = _REPO_ROOT / "src/autoskillit/skills_extended/make-plan/SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    """Load make-plan SKILL.md content.

    Defined locally (not via conftest) because the conftest ``skill_text``
    fixture targets investigate/SKILL.md.
    """
    assert _SKILL_MD.exists(), f"SKILL.md not found at {_SKILL_MD}"
    return _SKILL_MD.read_text()


def test_make_plan_green_gate_rule_exists(skill_text: str) -> None:
    """make-plan must require each part to independently pass the test gate."""
    assert re.search(
        r"(every|each)\s+part\s+must\s+(independently\s+)?(pass|leave)\s+(.*test|.*green|.*gate)",
        skill_text,
        re.IGNORECASE,
    ), (
        "make-plan SKILL.md must contain a multi-part rule requiring each part "
        "to independently pass the test gate"
    )


def test_make_plan_test_invalidation_colocation_rule(skill_text: str) -> None:
    """make-plan must require test changes co-located with triggering code changes."""
    assert re.search(
        r"(test|guard).*invalidat.*same\s+part|same\s+part.*(test|guard).*invalidat",
        skill_text,
        re.IGNORECASE | re.DOTALL,
    ) or re.search(
        r"xfail.*bridge|bridge.*xfail",
        skill_text,
        re.IGNORECASE,
    ), (
        "make-plan SKILL.md must require test invalidation changes to be "
        "co-located with the code that triggers them, or use xfail bridging"
    )


def test_make_plan_documents_xfail_bridge(skill_text: str) -> None:
    """make-plan must document xfail(strict=True) as the canonical bridge mechanism."""
    assert re.search(
        r"xfail.*strict.*True|strict.*True.*xfail",
        skill_text,
    ), (
        "make-plan SKILL.md must document xfail(strict=True) as the canonical "
        "multi-part bridge mechanism for cross-part test dependencies"
    )


def test_make_plan_documents_deletion_guard_canary_rule(skill_text: str) -> None:
    """make-plan must prohibit deferring deletion-guard canary removal to a later part."""
    assert re.search(
        r"deletion.guard\s+canar|deletion-guard\s+canary",
        skill_text,
        re.IGNORECASE,
    ), (
        "make-plan SKILL.md must document that deletion-guard canaries must be "
        "removed or xfail-bridged in the same part that re-registers the name"
    )
