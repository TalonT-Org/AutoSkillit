"""Cross-skill sync test for the Step 7 'From Ticket Grouper:' block.

The three bundled validate-* skills (validate-test-audit, validate-audit,
validate-review-decisions) share an identical Step 7 prose block. If they
drift, the runtime Rationale self-consistency check stops applying
identically across skills — exactly the bug class that produced #4610.

validate-test-audit is the canonical source for the block (it is the
first one written). The other two are asserted byte-equal to it.
"""

from __future__ import annotations

import pytest

from tests.skills.conftest import extract_step7_ticket_grouper_block, resolve_skill_text

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

_CANONICAL = "validate-test-audit"


@pytest.mark.parametrize("skill_name", ["validate-audit", "validate-review-decisions"])
def test_ticket_grouper_step7_block_matches_canonical(skill_name: str) -> None:
    canonical = extract_step7_ticket_grouper_block(resolve_skill_text(_CANONICAL))
    assert canonical, "canonical Step 7 From Ticket Grouper block was empty"
    assert extract_step7_ticket_grouper_block(resolve_skill_text(skill_name)) == canonical
