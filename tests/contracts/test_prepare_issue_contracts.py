"""Contract tests for the prepare-issue SKILL.md."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/prepare-issue/SKILL.md"


def _lines():
    return SKILL_MD.read_text().splitlines()


def test_label_create_calls_include_force():
    """All gh label create calls in prepare-issue must include --force."""
    for line in _lines():
        if "gh label create" in line:
            assert "--force" in line, f"Missing --force in: {line}"


def test_no_batch_labels_applied():
    """prepare-issue must never apply batch:N labels."""
    batch_pattern = re.compile(r"batch:\d+")
    for line in _lines():
        if "gh issue edit" in line or "add-label" in line:
            assert not batch_pattern.search(line), f"batch label found in: {line}"


def test_only_known_recipe_routes_applied():
    """Only recipe:implementation and recipe:remediation are valid route labels."""
    for line in _lines():
        if "recipe:" in line and "add-label" in line:
            assert "recipe:implementation" in line or "recipe:remediation" in line, (
                f"Unknown recipe label in: {line}"
            )


def test_prepare_issue_generates_requirements_on_implementation_route():
    """Skill must document requirement generation triggered by recipe:implementation route."""
    text = SKILL_MD.read_text()
    assert "recipe:implementation" in text
    # Requirement generation step must appear after classification
    impl_pos = text.find("recipe:implementation")
    req_gen_pos = (
        text.find("Requirement Generation")
        if "Requirement Generation" in text
        else text.find("## Requirements")
    )
    assert req_gen_pos > impl_pos, (
        "Requirement generation must appear after implementation route classification"
    )


def test_prepare_issue_appends_requirements_section():
    """Skill must document appending ## Requirements section to the issue body."""
    text = SKILL_MD.read_text()
    assert "## Requirements" in text


def test_prepare_issue_uses_req_id_format():
    """Skill must document REQ- format identifiers."""
    text = SKILL_MD.read_text()
    assert "REQ-" in text


def test_prepare_issue_uses_gh_issue_edit_for_requirements():
    """Skill must use gh issue edit to append requirements (not just labels)."""
    text = SKILL_MD.read_text()
    # Must document gh issue edit AND requirements_generated — the label-only edit
    # that already exists in the current skill does not satisfy this test.
    assert "gh issue edit" in text
    assert "requirements_generated" in text


def test_prepare_issue_result_block_includes_requirements_generated():
    """Result block schema must include requirements_generated field."""
    text = SKILL_MD.read_text()
    assert "requirements_generated" in text


def test_prepare_issue_skips_requirements_on_remediation():
    """Remediation route must skip requirement generation."""
    text = SKILL_MD.read_text()
    # Requirement generation must be gated by the implementation route check.
    # The skill must document the step number or label that gates generation to
    # recipe:implementation only — evidenced by "requirements_generated" appearing
    # in the skill and the implementation route being explicitly referenced there.
    assert "requirements_generated" in text
    assert "recipe:implementation" in text
    # The requirements_generated field must appear in a section that references the
    # implementation-only gate, not as a global unconditional step.
    req_gen_idx = text.find("requirements_generated")
    impl_idx = text.find("recipe:implementation")
    # requirements_generated must appear after the first implementation route reference
    assert req_gen_idx > impl_idx, (
        "requirements_generated must appear after the recipe:implementation gate, not before it"
    )


def test_prepare_issue_handles_vague_issues():
    """Skill must document behavior when requirements cannot be cleanly extracted."""
    text = SKILL_MD.read_text()
    vague_handled = (
        "can't be cleanly extracted" in text.lower()
        or "cannot be cleanly extracted" in text.lower()
        or "flag" in text.lower()
        and "more detail" in text.lower()
        or "needs more detail" in text.lower()
        or "suggest remediation" in text.lower()
    )
    assert vague_handled, (
        "Skill must document behavior when issue is too vague for requirement extraction"
    )


# ---------------------------------------------------------------------------
# TRIG: Trigger phrase and When to Use tests
# ---------------------------------------------------------------------------


def test_prepare_issue_trigger_phrases_in_description_frontmatter():
    """The description: frontmatter must include natural language trigger phrases."""
    raw = SKILL_MD.read_text()
    parts = raw.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have YAML frontmatter"
    fm = yaml.safe_load(parts[1])
    desc = fm.get("description", "").lower()
    trigger_phrases = [
        "open an issue",
        "create an issue",
        "file a bug",
        "file an issue",
        "make a new issue",
        "open a github issue",
        "create a github issue",
        "i want to open up a github issue",
    ]
    assert any(p in desc for p in trigger_phrases), (
        f"description: frontmatter must contain natural language trigger phrases; got: {desc!r}"
    )


def test_prepare_issue_has_when_to_use_section():
    """SKILL.md must contain a ## When to Use section."""
    text = SKILL_MD.read_text()
    assert "## When to Use" in text


# ---------------------------------------------------------------------------
# DEDUP: Multi-candidate display and extend-path tests
# ---------------------------------------------------------------------------


def test_prepare_issue_dedup_shows_all_candidates():
    """Dedup step must document displaying all found candidates with number, title, and URL."""
    text = SKILL_MD.read_text()
    # Anchor to the dedup section to avoid matching unrelated occurrences elsewhere
    dedup_start = text.find("### Step 4: Dedup Check")
    assert dedup_start != -1, "SKILL.md must contain Step 4 dedup section"
    dedup_end = text.find("### Step 4a:", dedup_start)
    dedup_section = text[dedup_start:dedup_end] if dedup_end != -1 else text[dedup_start:]
    # The option-menu block must use the [1]–[{N}] indexed format within the dedup section
    assert "[1]" in dedup_section and "[{N}]" in dedup_section, (
        "Dedup must display candidates in [1]–[{N}] indexed format within Step 4"
    )
    # Per-candidate URL must appear in the dedup display block (not just in shell commands)
    assert "{url}" in dedup_section, (
        "Dedup section must show per-candidate {url} in the candidate display block"
    )


def test_prepare_issue_dedup_prompt_has_extend_option():
    """Interactive dedup prompt must include an 'Add to / extend' option."""
    text = SKILL_MD.read_text()
    assert "extend" in text.lower(), (
        "Dedup interactive prompt must offer 'Add to / extend an existing issue' option"
    )


def test_prepare_issue_dedup_extend_runs_triage():
    """Extending an existing issue must route to LLM Classification, not exit immediately."""
    text = SKILL_MD.read_text()
    assert "extend" in text.lower()
    # Anchor to the dedup section to avoid matching unrelated 'extend' occurrences
    dedup_start = text.find("### Step 4: Dedup Check")
    assert dedup_start != -1, "SKILL.md must contain Step 4 dedup section"
    dedup_end = text.find("### Step 4a:", dedup_start)
    dedup_section = text[dedup_start:dedup_end] if dedup_end != -1 else text[dedup_start:]
    assert "extend" in dedup_section.lower(), "Step 4 dedup section must document the extend path"
    # The extend path within the dedup section must reference continuing to Step 6
    has_triage_ref = (
        "Step 6" in dedup_section
        or "LLM Classification" in dedup_section
        or "classification" in dedup_section.lower()
    )
    assert has_triage_ref, (
        "Extend path must reference continuing to LLM Classification (Step 6), "
        "not exit immediately"
    )


def test_prepare_issue_dedup_bypass_with_issue_flag_still_documented():
    """--issue N flag must still bypass dedup (existing contract, must remain intact)."""
    text = SKILL_MD.read_text()
    # Both the interface docs and the step header must reference the bypass
    assert "--issue N" in text and "issue_number" in text
    assert "skip" in text.lower() or "bypass" in text.lower() or "skip if" in text.lower()


# ---------------------------------------------------------------------------
# VALIDATED REPORT: Detection and handling tests
# ---------------------------------------------------------------------------


def test_prepare_issue_detects_validated_audit_report_input():
    """Skill must document detection of 'validated: true' marker for validated report inputs."""
    text = SKILL_MD.read_text()
    assert "validated: true" in text, (
        "prepare-issue SKILL.md must document detecting the 'validated: true' marker"
    )
    assert "is_validated_report" in text, (
        "prepare-issue must use 'is_validated_report' flag to gate validated-report behavior"
    )


def test_prepare_issue_skips_requirements_for_validated_report():
    """Step 7a must explicitly skip requirements generation when is_validated_report is true."""
    text = SKILL_MD.read_text()
    # Find the requirements generation step
    req_step_pos = text.find("Step 7a")
    assert req_step_pos != -1, "Step 7a must exist in the skill"
    req_step_text = text[req_step_pos : req_step_pos + 600]
    assert "is_validated_report" in req_step_text, (
        "Step 7a must reference is_validated_report to gate requirements generation"
    )
    skip_keywords = ["skip", "Skip"]
    assert any(kw in req_step_text for kw in skip_keywords), (
        "Step 7a must document skipping requirements generation for validated reports"
    )


def test_prepare_issue_excludes_contested_refs_from_validated_report_body():
    """Skill must document removing the contested findings reference line from the issue body."""
    text = SKILL_MD.read_text()
    assert "contested_findings_" in text, (
        "Skill must reference 'contested_findings_' in the strip rules for validated report body"
    )
    # Anchor: strip rules must appear after validated report handling is introduced
    validated_pos = text.find("is_validated_report")
    assert validated_pos != -1
    body_section = text[validated_pos:]
    assert "contested" in body_section.lower(), (
        "Contested findings exclusion must be documented within the validated report handling"
        " section"
    )


def test_prepare_issue_strips_artifact_paths_from_validated_report_body():
    """Skill must document stripping 'Original report:' and artifact paths from issue body."""
    text = SKILL_MD.read_text()
    validated_pos = text.find("is_validated_report")
    assert validated_pos != -1
    body_section = text[validated_pos:]
    assert "Original report" in body_section, (
        "Skill must document removing the 'Original report:' line (artifact path) from the body"
    )


# ---------------------------------------------------------------------------
# DETERMINISTIC STRIP: New tests for validated-report strip completeness
# ---------------------------------------------------------------------------


def test_prepare_issue_does_not_keep_findings_with_exceptions():
    """## Findings with Exceptions must be stripped, not kept."""
    text = SKILL_MD.read_text()
    # Confirm the section name is referenced (validates the test is meaningful)
    assert "Findings with Exceptions" in text, (
        "Sanity: 'Findings with Exceptions' not found at all in SKILL.md"
    )
    # No line in the file may simultaneously contain 'Keep' and 'Findings with Exceptions'
    for line in text.splitlines():
        assert not ("Keep" in line and "Findings with Exceptions" in line), (
            f"prepare-issue must NOT Keep '## Findings with Exceptions'. Offending line: {line!r}"
        )


def test_prepare_issue_strips_contested_table_rows():
    """prepare-issue must explicitly strip '| CONTESTED |' table rows."""
    text = SKILL_MD.read_text()
    validated_pos = text.find("is_validated_report")
    assert validated_pos != -1, "Sanity: 'is_validated_report' not found"
    body_section = text[validated_pos:]
    assert "| CONTESTED |" in body_section, (
        "prepare-issue validated-report section must reference '| CONTESTED |' "
        "as a strip target (e.g. grep -v or Remove rule)"
    )


def test_prepare_issue_strips_exception_warranted_table_rows():
    """prepare-issue must explicitly strip '| VALID BUT EXCEPTION WARRANTED |' rows."""
    text = SKILL_MD.read_text()
    validated_pos = text.find("is_validated_report")
    assert validated_pos != -1, "Sanity: 'is_validated_report' not found"
    body_section = text[validated_pos:]
    assert (
        "VALID BUT EXCEPTION WARRANTED" in body_section or "EXCEPTION WARRANTED" in body_section
    ), (
        "prepare-issue validated-report section must reference 'VALID BUT EXCEPTION WARRANTED' "
        "as a strip target"
    )


def test_prepare_issue_validated_report_uses_body_file():
    """prepare-issue must use --body-file (not inline --body) for validated report creation."""
    text = SKILL_MD.read_text()
    validated_pos = text.find("is_validated_report")
    assert validated_pos != -1, "Sanity: 'is_validated_report' not found"
    body_section = text[validated_pos:]
    # --body-file must appear in the validated-report section
    assert "--body-file" in body_section, (
        "prepare-issue must use 'gh issue create --body-file' for validated-report input, "
        "not inline '--body'"
    )
    # The temp file must live under AUTOSKILLIT_TEMP
    assert "AUTOSKILLIT_TEMP" in body_section and "issue_body_" in body_section, (
        "prepare-issue must write the issue body to "
        "{{AUTOSKILLIT_TEMP}}/prepare-issue/issue_body_*.md before calling gh issue create"
    )


def test_prepare_issue_never_constraint_prohibits_inline_body():
    """CRITICAL CONSTRAINTS must explicitly prohibit inline --body for validated-report path."""
    text = SKILL_MD.read_text()
    # Find the NEVER block within Critical Constraints
    never_pos = text.find("**NEVER:**")
    assert never_pos != -1, "Sanity: '**NEVER:**' block not found"
    # Find end of NEVER block (next **ALWAYS:** or end of constraints section)
    always_pos = text.find("**ALWAYS:**", never_pos)
    never_block = (
        text[never_pos:always_pos] if always_pos != -1 else text[never_pos : never_pos + 800]
    )
    lower = never_block.lower()
    assert "--body" in never_block and "inline" in lower, (
        "prepare-issue NEVER block must prohibit inline '--body' for validated-report "
        "issue creation. Add: 'Use --body inline for the path — always use --body-file'"
    )


def test_prepare_issue_requirements_append_uses_body_file():
    """prepare-issue Step 7a (requirements append via gh issue edit) must use --body-file."""
    import re

    text = SKILL_MD.read_text()
    # Ensure no gh issue edit call uses inline --body with shell substitution ($(...))
    inline_pattern = re.compile(r'gh issue edit[^\n]+--body\s+"\$\(')
    matches = inline_pattern.findall(text)
    assert not matches, (
        "prepare-issue 'gh issue edit' (requirements append) must use --body-file, "
        "not inline --body shell substitution"
    )


def test_prepare_issue_no_tmp_paths():
    """prepare-issue must not use /tmp/ paths — all temp files go in {{AUTOSKILLIT_TEMP}}."""
    text = SKILL_MD.read_text()
    assert "/tmp/" not in text, (
        "prepare-issue uses /tmp/ path(s). All temp files must live in "
        "{{AUTOSKILLIT_TEMP}}/prepare-issue/ per project convention"
    )
