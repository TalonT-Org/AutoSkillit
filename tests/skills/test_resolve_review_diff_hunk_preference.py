"""Guards: resolve-review Step 3.5 prefers diff_hunk over source file reads.

Enforces the 3-tier context resolution hierarchy:
  Tier 1: diff_context_map pre-built code_region (richest, from review-pr handoff)
  Tier 2: diff_hunk from the GitHub API review comment (always available)
  Tier 3: source file read (last resort — only when hunk is insufficient)
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)


def _step35_section() -> str:
    assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"
    text = SKILL_PATH.read_text()
    start = text.find("### Step 3.5")
    assert start != -1, "### Step 3.5 not found in SKILL.md"
    end = text.find("### Step 4", start)
    assert end != -1, "### Step 4 not found after Step 3.5 in SKILL.md"
    return text[start:end]


def test_diff_hunk_documented_as_preferred_context_in_step35() -> None:
    section = _step35_section().lower()
    assert "use" in section and "diff_hunk" in section and "context" in section, (
        "Step 3.5 must document diff_hunk as a preferred context source"
    )


def test_file_read_conditional_on_hunk_insufficiency() -> None:
    section = _step35_section().lower()
    assert (
        "only" in section
        and "read" in section
        and ("source file" in section or "file" in section)
        and ("if" in section)
    ), "Source file reads must be conditional on hunk insufficiency"


def test_external_reference_documented_as_file_read_trigger() -> None:
    section = _step35_section().lower()
    assert "outside" in section and "hunk" in section, (
        "Step 3.5 must document external references as a file-read trigger"
    )


def test_truncated_hunk_documented_as_file_read_trigger() -> None:
    section = _step35_section().lower()
    assert "truncated" in section or "missing" in section, (
        "Step 3.5 must document truncated/missing hunk as a file-read trigger"
    )


def test_inline_shortcut_prefers_diff_hunk() -> None:
    section = _step35_section().lower()
    shortcut_start = section.find("inline classification shortcut")
    assert shortcut_start != -1, "Inline classification shortcut not found"
    shortcut_text = section[shortcut_start : shortcut_start + 600]
    assert "diff_hunk" in shortcut_text, "Inline classification shortcut must mention diff_hunk"


def test_three_tier_hierarchy_documented() -> None:
    section = _step35_section()
    hierarchy_start = section.find("Context resolution hierarchy")
    assert hierarchy_start != -1, "Context resolution hierarchy not found in Step 3.5"
    hierarchy = section[hierarchy_start : hierarchy_start + 600]
    pos_dcm = hierarchy.find("diff_context_map")
    pos_hunk = hierarchy.find("diff_hunk")
    pos_file_read = hierarchy.lower().find("source file read")
    if pos_file_read == -1:
        pos_file_read = hierarchy.lower().find("file read")
    assert pos_dcm != -1, "diff_context_map not found in hierarchy"
    assert pos_hunk != -1, "diff_hunk not found in hierarchy"
    assert pos_file_read != -1, "file read fallback not found in hierarchy"
    assert pos_dcm < pos_hunk < pos_file_read, (
        "Three-tier hierarchy must be in order: diff_context_map > diff_hunk > file read"
    )


def test_diff_hunk_not_just_passthrough_field() -> None:
    section = _step35_section().lower()
    assert ("use the" in section and "diff_hunk" in section) or (
        "use" in section and "diff_hunk" in section and "context" in section
    ), "Step 3.5 must have a directive to USE diff_hunk, not just list it"
