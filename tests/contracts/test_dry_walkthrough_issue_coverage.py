"""Contract test: dry-walkthrough SKILL.md must contain plan-vs-issue coverage check step."""

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


def _read_skill_md() -> str:
    return _SKILL_MD.read_text()


def test_dry_walkthrough_has_issue_coverage_step():
    """dry-walkthrough SKILL.md must contain Step 4.6 for plan-vs-issue coverage."""
    content = _read_skill_md()
    assert re.search(
        r"###\s+Step\s+4\.6[\s:].*Plan-vs-Issue\s+Coverage\s+Check",
        content,
        re.DOTALL,
    ), (
        "dry-walkthrough/SKILL.md must contain a '### Step 4.6' section titled "
        "'Plan-vs-Issue Coverage Check'"
    )


def test_dry_walkthrough_issue_coverage_positioned_after_step45():
    """Step 4.6 must appear between Step 4.5 and Step 5."""
    content = _read_skill_md()
    step45_pos = content.find("### Step 4.5")
    step46_pos = content.find("### Step 4.6")
    step5_pos = content.find("### Step 5:")
    assert step45_pos != -1, "Step 4.5 section must exist"
    assert step46_pos != -1, "Step 4.6 section must exist"
    assert step5_pos != -1, "Step 5 section must exist"
    assert step45_pos < step46_pos < step5_pos, (
        "Step 4.6 must be positioned between Step 4.5 and Step 5 in dry-walkthrough/SKILL.md"
    )


def test_dry_walkthrough_issue_coverage_references_issue_context():
    """Step 4.6 must reference issue_url or issue_number."""
    content = _read_skill_md()
    step46_section = content[content.find("### Step 4.6") : content.find("### Step 5:")]
    assert "issue_url" in step46_section or "issue_number" in step46_section, (
        "dry-walkthrough SKILL.md Step 4.6 must reference issue_url or issue_number"
    )


def test_dry_walkthrough_issue_coverage_checks_enumerated_items():
    """Step 4.6 must mention enumerated or remediation item language."""
    content = _read_skill_md()
    step46_section = content[content.find("### Step 4.6") : content.find("### Step 5:")]
    assert re.search(
        r"enumerated|remediation item|requirement item",
        step46_section,
        re.IGNORECASE,
    ), (
        "dry-walkthrough SKILL.md Step 4.6 must mention 'enumerated', 'remediation item', "
        "or 'requirement item' to describe the scope check"
    )


def test_dry_walkthrough_issue_coverage_blocks_on_missing():
    """Step 4.6 must specify blocking/failing when items are unmapped."""
    content = _read_skill_md()
    step46_section = content[content.find("### Step 4.6") : content.find("### Step 5:")]
    assert re.search(
        r"UNMAPPED|FAIL|block|do not stamp|not stamp",
        step46_section,
        re.IGNORECASE,
    ), (
        "dry-walkthrough SKILL.md Step 4.6 must specify blocking/failing behavior when "
        "enumerated items are not mapped to plan steps"
    )


def test_dry_walkthrough_issue_coverage_fetches_issue_body():
    """Step 4.6 must reference gh issue view or fetch_github_issue for issue body retrieval."""
    content = _read_skill_md()
    step46_section = content[content.find("### Step 4.6") : content.find("### Step 5:")]
    assert "gh issue view" in step46_section or "fetch_github_issue" in step46_section, (
        "dry-walkthrough SKILL.md Step 4.6 must reference 'gh issue view' or "
        "'fetch_github_issue' for issue body retrieval"
    )


def test_dry_walkthrough_issue_coverage_graceful_skip():
    """Step 4.6 must specify graceful skip when issue_url is not provided."""
    content = _read_skill_md()
    step46_section = content[content.find("### Step 4.6") : content.find("### Step 5:")]
    assert re.search(
        r"skip\s+this\s+step|not\s+provided|no\s+issue\s+context",
        step46_section,
        re.IGNORECASE,
    ), (
        "dry-walkthrough SKILL.md Step 4.6 must specify graceful skip when issue_url "
        "or issue_number is not provided (non-issue-sourced pipelines)"
    )


def test_dry_walkthrough_arguments_documents_issue_url():
    """dry-walkthrough SKILL.md Arguments section must document issue_url."""
    content = _read_skill_md()
    args_section = content[content.find("## Arguments") : content.find("## Critical Constraints")]
    assert "issue_url" in args_section, (
        "dry-walkthrough SKILL.md Arguments section must document the issue_url argument"
    )
