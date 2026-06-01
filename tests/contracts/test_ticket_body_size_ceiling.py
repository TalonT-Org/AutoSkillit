"""Cross-skill contract: ticket body size ceiling.

Rules enforced:
- Skills that file issues from ticket body files must document a body
  size guard (warning or abort when body exceeds threshold).
"""

from __future__ import annotations

import re

import pytest

from tests.contracts.conftest import _all_skill_mds

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _has_own_filing_instructions(content: str) -> bool:
    """Check whether a skill performs issue filing itself (not just referencing another skill)."""
    pattern_filing = re.compile(
        r"ticket_body.*createIssue|createIssue.*ticket_body|batch.*create.*issue",
        re.IGNORECASE,
    )
    skill_ref = re.compile(r"/autoskillit:\S+.*batch.*create.*issue", re.IGNORECASE)
    for match in pattern_filing.finditer(content):
        line_start = content.rfind("\n", 0, match.start()) + 1
        line_end = content.find("\n", match.end())
        line = content[line_start : line_end if line_end != -1 else len(content)]
        if not skill_ref.search(line):
            return True
    return False


def test_ticket_filing_skills_have_body_size_guard() -> None:
    """Skills that file issues from ticket_body files must have a body size guard."""
    pattern_guard = re.compile(
        r"(?:body.*size|size.*(?:limit|guard|ceiling|threshold|check)"
        r"|exceed.*(?:KB|char)|(?:10|65).?(?:KB|536))",
        re.IGNORECASE,
    )
    failures: list[str] = []
    for skill_name, content in _all_skill_mds():
        if _has_own_filing_instructions(content) and not pattern_guard.search(content):
            failures.append(
                f"  {skill_name}: files issues from ticket bodies but has no body size guard"
            )
    assert not failures, "Issue-filing skills must document a body size guard:\n" + "\n".join(
        failures
    )
