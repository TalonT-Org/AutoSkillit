"""Contract tests for open-pr skill — requirement traceability."""

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
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
    body_section = text[body_idx:]
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


def test_open_pr_skill_self_retrieves_token_summary(text):
    """SKILL.md must document self-retrieval of token summary via cwd_filter."""
    assert "load_from_log_dir" in text, "SKILL.md must document load_from_log_dir self-retrieval"
    assert "cwd_filter" in text, "SKILL.md must document cwd_filter scoping key"
    assert "PIPELINE_CWD" in text, "SKILL.md must document PIPELINE_CWD=$(pwd) discovery"


def test_open_pr_skill_removes_token_summary_path_arg(text):
    """token_summary_path must no longer be documented as a positional arg."""
    assert "token_summary_path" not in text, (
        "token_summary_path arg must be removed; skill self-retrieves now"
    )


def test_part_suffix_stripped_in_bash_block(text):
    """Step 2 bash block must strip the '— PART X ONLY' suffix from BASE_TITLE."""
    # Verify the sed strip is chained on the same line as the BASE_TITLE assignment,
    # and requires a pipe — guards against sed and PART pattern appearing separately
    pattern = r"BASE_TITLE=.*\|.*sed.*PART \[A-Z\] ONLY"
    assert re.search(pattern, text), (
        "BASE_TITLE extraction must pipe through sed stripping PART X ONLY suffix"
    )


def test_step2_prose_instructs_suffix_stripping(text):
    """Step 2 prose must explicitly instruct stripping the PART X ONLY suffix."""
    step2_idx = text.find("### Step 2")
    assert step2_idx != -1, "Step 2 must exist"
    step2_section = text[step2_idx : step2_idx + 2000]
    assert "PART" in step2_section and "ONLY" in step2_section, (
        "Step 2 prose must mention stripping the PART X ONLY suffix"
    )
