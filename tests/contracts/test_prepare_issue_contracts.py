"""Contract tests for the prepare-issue SKILL.md."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills/prepare-issue/SKILL.md"


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
    # Must show a numbered list of candidates, not just one "high title overlap" check
    has_numbered_list = "[1]" in text or "(1)" in text or "numbered" in text.lower()
    assert has_numbered_list, "Dedup must display candidates in a numbered list"
    # Must include URL in each candidate display
    assert "url" in text.lower()


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
    # Find the first occurrence of "extend" and check the surrounding window
    # references continuing to LLM classification (Step 6), not immediate exit
    extend_idx = text.lower().find("extend")
    post_extend = text[extend_idx : extend_idx + 1000]
    has_triage_ref = (
        "Step 6" in post_extend
        or "LLM Classification" in post_extend
        or "classification" in post_extend.lower()
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
