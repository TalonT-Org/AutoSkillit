"""Cross-skill sync test for the Step 7 'From Ticket Grouper:' block.

The three bundled validate-* skills (validate-test-audit, validate-audit,
validate-review-decisions) share an identical Step 7 prose block. If they
drift, the runtime Rationale self-consistency check stops applying
identically across skills — exactly the bug class that produced #4610.

The canonical source for the block is declared once in
``tests/skills/_skill_text_helpers.py`` as ``CANONICAL_TICKET_GROUPER_SKILL``;
the other two skills are asserted byte-equal to it here.
"""

from __future__ import annotations

import pytest

from tests.skills._skill_text_helpers import (
    CANONICAL_TICKET_GROUPER_SKILL,
    extract_step7_grouper_block,
    resolve_skill_text,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


@pytest.mark.parametrize("skill_name", ["validate-audit", "validate-review-decisions"])
def test_ticket_grouper_step7_block_matches_canonical(skill_name: str) -> None:
    canonical = extract_step7_grouper_block(resolve_skill_text(CANONICAL_TICKET_GROUPER_SKILL))
    assert canonical, "canonical Step 7 From Ticket Grouper block was empty"
    assert extract_step7_grouper_block(resolve_skill_text(skill_name)) == canonical
