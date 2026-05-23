"""Cross-skill contract: ticket body size ceiling.

Rules enforced:
- Skills that file issues from ticket body files must document a body
  size guard (warning or abort when body exceeds threshold).
"""

from __future__ import annotations

import re

from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir


def _all_skill_mds() -> list[tuple[str, str]]:
    result = []
    for skills_dir in (bundled_skills_dir(), bundled_skills_extended_dir()):
        result.extend(
            (d.name, (d / "SKILL.md").read_text())
            for d in sorted(skills_dir.iterdir())
            if d.is_dir() and (d / "SKILL.md").is_file()
        )
    return result


def test_ticket_filing_skills_have_body_size_guard() -> None:
    """Skills that file issues from ticket_body files must have a body size guard."""
    pattern_filing = re.compile(
        r"ticket_body.*createIssue|createIssue.*ticket_body|batch.*create.*issue",
        re.IGNORECASE,
    )
    pattern_guard = re.compile(
        r"(?:body.*size|size.*(?:limit|guard|ceiling|threshold|check)"
        r"|exceed.*(?:KB|char)|(?:10|65).?(?:KB|536))",
        re.IGNORECASE,
    )
    failures: list[str] = []
    for skill_name, content in _all_skill_mds():
        if pattern_filing.search(content) and not pattern_guard.search(content):
            failures.append(
                f"  {skill_name}: files issues from ticket bodies but has no body size guard"
            )
    assert not failures, "Issue-filing skills must document a body size guard:\n" + "\n".join(
        failures
    )
