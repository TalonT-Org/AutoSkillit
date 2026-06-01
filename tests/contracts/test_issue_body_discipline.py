"""Cross-skill contract: issue body discipline.

Rules enforced:
- No SKILL.md may contain instructions to append validation_summary content
  to GitHub issue bodies. Validation summaries are pipeline audit artifacts
  and must remain separate from issue content.
- This invariant was established by PR #2178 (issue #2097) and violated by
  PR #2277's file-audit-issues Step 6.
"""

from __future__ import annotations

import re

import pytest

from tests.contracts.conftest import _all_skill_mds

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_no_skill_appends_validation_summary_to_issue_body() -> None:
    """No SKILL.md may instruct appending validation_summary to issue bodies."""
    pattern = re.compile(
        r"append.*validation.summary.*(?:body|issue)|"
        r"validation.summary.*append.*(?:body|issue)|"
        r"gh\s+issue\s+edit.*validation.summary",
        re.IGNORECASE,
    )
    failures: list[str] = []
    for skill_name, content in _all_skill_mds():
        if pattern.search(content):
            failures.append(
                f"  {skill_name}: contains instructions to append "
                f"validation_summary to issue bodies"
            )
    assert not failures, (
        "Skills must not append validation summaries to issue bodies "
        "(pipeline audit artifacts, not ticket content):\n" + "\n".join(failures)
    )


def test_no_step_named_append_validation_summaries() -> None:
    """No SKILL.md may have a step titled 'Append Validation Summaries'."""
    pattern = re.compile(
        r"^##\s+Step\s+\d+.*Append\s+Validation\s+Summar",
        re.MULTILINE | re.IGNORECASE,
    )
    failures: list[str] = []
    for skill_name, content in _all_skill_mds():
        if pattern.search(content):
            failures.append(f"  {skill_name}: has a step named 'Append Validation Summaries'")
    assert not failures, (
        "Skills must not have steps that append validation summaries:\n" + "\n".join(failures)
    )
