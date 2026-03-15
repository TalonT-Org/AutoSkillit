"""Contract tests for open-pr skill — requirement traceability."""

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills"
SKILL = SKILLS_DIR / "open-pr/SKILL.md"


@pytest.fixture()
def text():
    return SKILL.read_text()


def test_open_pr_skill_file_exists():
    assert SKILL.exists(), "open-pr/SKILL.md must exist"


def test_open_pr_fetches_requirements_from_closing_issue(text):
    """open-pr must document extracting ## Requirements from the closing_issue."""
    # Must reference fetching the closing issue body to extract requirements
    has_fetch = ("closing_issue" in text and "## Requirements" in text) or (
        "gh issue view" in text and "## Requirements" in text
    )
    assert has_fetch, "open-pr must document extracting requirements from closing_issue"


def test_open_pr_includes_requirements_in_pr_body(text):
    """open-pr PR body composition must include the ## Requirements section."""
    # The body composition section must reference requirements
    body_idx = text.find("PR body") if "PR body" in text else text.find("pr_body")
    assert body_idx != -1, "open-pr must document PR body composition"
    body_section = text[body_idx : body_idx + 4000]
    assert "## Requirements" in body_section or "requirements" in body_section.lower()


def test_open_pr_requirements_conditional_on_presence(text):
    """open-pr must only include requirements section if the issue has one."""
    lower = text.lower()
    conditional = (
        "if present" in lower
        or "if extracted" in lower
        or "if the issue has" in lower
        or "when requirements exist" in lower
        or "only if" in lower
        and "requirements" in lower
    )
    assert conditional, (
        "Requirements must be included conditionally — only if the issue has a"
        " ## Requirements section"
    )


def test_open_pr_requirements_section_placement(text):
    """Requirements section must appear before Architecture Impact in PR body."""
    req_pos = text.find("## Requirements")
    arch_pos = text.find("## Architecture Impact")
    if req_pos == -1 or arch_pos == -1:
        pytest.skip("Section placement not yet documented")
    assert req_pos < arch_pos, "## Requirements must precede ## Architecture Impact in PR body"
