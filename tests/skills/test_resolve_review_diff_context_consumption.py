"""Guards: resolve-review loads and uses diff_context handoff file from review-pr."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def _step2_section() -> str:
    text = _skill_text()
    start = text.find("### Step 2")
    end = text.find("### Step 3", start)
    return text[start:end]


def _step35_section() -> str:
    text = _skill_text()
    start = text.find("### Step 3.5")
    assert start != -1, "### Step 3.5 not found in SKILL.md"
    end = text.find("### Step 4", start)
    return text[start:end]


def _step4_section() -> str:
    text = _skill_text()
    start = text.find("### Step 4")
    assert start != -1, "### Step 4 not found in SKILL.md"
    end = text.find("### Step 5", start)
    end = end if end != -1 else None
    return text[start:end]


def test_step2_checks_for_diff_context_file():
    """Step 2 must check for the review-pr diff_context handoff file."""
    section = _step2_section()
    assert "diff_context" in section


def test_step2_loads_diff_context_map():
    """Step 2 must build a diff_context_map lookup structure."""
    section = _step2_section()
    assert "diff_context_map" in section


def test_step2_fallback_when_file_absent():
    """Step 2 must fall back to empty map when diff_context file is absent."""
    section = _step2_section()
    # Must mention fallback, absence, or empty-map behavior
    lower = section.lower()
    assert "absent" in lower or "not found" in lower or "fallback" in lower or "{}" in section


def test_step35_uses_prebuilt_code_region():
    """Step 3.5 sub-agent prompt must use pre-loaded code_region when available."""
    section = _step35_section()
    assert "diff_context_map" in section or "code_region" in section


def test_step35_skips_file_read_when_context_available():
    """Step 3.5 must skip 'read file' instruction when pre-built context is present."""
    section = _step35_section()
    lower = section.lower()
    # Must indicate the file-read instruction is conditional or skipped
    assert (
        "instead of" in lower
        or "skip" in lower
        or "do not read" in lower
        or "use the pre" in lower
    )


def test_step4_skips_understanding_read_when_context_present():
    """Step 4 must skip the ±20 line understanding read when diff_context_map has entry."""
    section = _step4_section()
    assert "diff_context_map" in section
    lower = section.lower()
    assert "skip" in lower or "omit" in lower or "already available" in lower


def test_step4_still_reads_file_for_editing():
    """Step 4 must still read the file for applying actual edits even with pre-built context."""
    section = _step4_section()
    lower = section.lower()
    # Must mention that file read for editing is still needed
    assert "still read" in lower or "read the file" in lower


def test_diff_context_path_matches_review_pr_output_path():
    """resolve-review's diff_context path must match what review-pr writes."""
    full = _skill_text()
    assert "review-pr/diff_context" in full
