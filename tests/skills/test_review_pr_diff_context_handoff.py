"""Guards: review-pr writes diff_context handoff file in Step 8 before verdict emission."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def _step8_section() -> str:
    text = _skill_text()
    start = text.find("### Step 8")
    # Step 8 is the last step; take to end of file
    return text[start:]


def test_step8_writes_diff_context_file():
    """Step 8 must declare writing diff_context_{pr_number}.json."""
    section = _step8_section()
    assert "diff_context_{pr_number}.json" in section or "diff_context_" in section


def test_diff_context_path_uses_review_pr_temp_dir():
    """Handoff file must live in AUTOSKILLIT_TEMP/review-pr/, not resolve-review/."""
    section = _step8_section()
    assert "review-pr/diff_context" in section


def test_diff_context_schema_has_context_entries():
    """Handoff JSON schema must include context_entries field."""
    section = _step8_section()
    assert "context_entries" in section


def test_diff_context_entries_have_code_region():
    """Each context entry must include a code_region field."""
    section = _step8_section()
    assert "code_region" in section


def test_code_region_extracted_from_annotated_diff():
    """code_region must be sourced from ANNOTATED_DIFF — no additional file reads."""
    section = _step8_section()
    assert "ANNOTATED_DIFF" in section
    # Must state zero additional reads
    lower = section.lower()
    assert "zero" in lower or "no additional" in lower or "no file read" in lower


def test_handoff_write_precedes_verdict_emission():
    """diff_context write must appear before 'verdict = ' in Step 8 text."""
    section = _step8_section()
    write_pos = section.find("diff_context")
    verdict_pos = section.find("verdict = ")
    assert write_pos != -1, "diff_context not found in Step 8"
    assert verdict_pos != -1, "verdict = not found in Step 8"
    assert write_pos < verdict_pos, "diff_context write must precede verdict = emission"


def test_diff_context_covers_critical_and_warning():
    """All critical and warning findings must be captured — not just critical."""
    section = _step8_section()
    assert "critical" in section and "warning" in section


def test_diff_context_schema_version_field():
    """JSON schema must include schema_version for future-proofing."""
    section = _step8_section()
    assert "schema_version" in section
