"""Shared Ticket Grouper contract assertions across all bundled validate-* skills.

Each bundled validate-* skill carries an identical ``**From Ticket Grouper:**``
Step 7 block. The per-skill contract test files
(``test_validate_audit_contracts.py``, ``test_validate_test_audit_contracts.py``,
``test_validate_review_decisions_contracts.py``) each instantiate
``TestValidate*TicketGrouper`` with the same assertions hardcoded against their
own skill name. This file parametrises the three shared assertions across all
three skills so a regression in any one of them is caught by a single source
of truth instead of three near-identical copies.
"""

from __future__ import annotations

import pytest

from tests.skills._skill_text_helpers import (
    assert_ticket_grouper_has_effort_based_splitting,
    assert_ticket_grouper_has_minimum_group_floor,
    resolve_skill_text,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


_SHARED_VALIDATE_SKILLS = (
    "validate-audit",
    "validate-test-audit",
    "validate-review-decisions",
)


@pytest.mark.parametrize("skill_name", _SHARED_VALIDATE_SKILLS)
def test_ticket_grouper_has_minimum_group_floor(skill_name: str) -> None:
    assert_ticket_grouper_has_minimum_group_floor(resolve_skill_text(skill_name))


@pytest.mark.parametrize("skill_name", _SHARED_VALIDATE_SKILLS)
def test_ticket_grouper_has_effort_based_splitting(skill_name: str) -> None:
    assert_ticket_grouper_has_effort_based_splitting(resolve_skill_text(skill_name))


@pytest.mark.parametrize("skill_name", _SHARED_VALIDATE_SKILLS)
def test_ticket_grouper_has_rationale_self_consistency_check(skill_name: str) -> None:
    text = resolve_skill_text(skill_name)
    assert "Rationale self-consistency check" in text, (
        f"{skill_name} SKILL.md Step 7 must contain the 'Rationale self-consistency check' bullet"
    )
