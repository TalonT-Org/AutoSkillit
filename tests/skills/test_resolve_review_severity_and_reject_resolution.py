from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)
assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"
SKILL_TEXT = SKILL_PATH.read_text()


def test_info_findings_not_filtered_out() -> None:
    """Step 3 must NOT filter out info findings — no 'skip info findings entirely' instruction."""
    assert "Skip `info` findings entirely" not in SKILL_TEXT, (
        "Step 3 must not drop info findings — they must flow to Step 3.5"
    )
    assert "skipped — below threshold" not in SKILL_TEXT, (
        "Step 3 must not mark info findings as 'skipped — below threshold'"
    )


def test_info_findings_reach_intent_validation() -> None:
    """Step 3.5 domain grouping must cover all findings, not just critical+warning."""
    assert "critical+warning findings" not in SKILL_TEXT, (
        "Domain grouping instruction must include all severities, not restrict to critical+warning"
    )
    # Verify the new all-severity phrasing is present
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1, "SKILL.md must have a Step 3.5 section"
    step4_idx = SKILL_TEXT.find("### Step 4")
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert "Group all findings" in step35_section, (
        "Step 3.5 domain grouping must say 'Group all findings' (all severities)"
    )


def test_intent_validation_scope_includes_all_severities() -> None:
    """Step 3.5 intro must validate every finding, not just critical and warning."""
    assert "validate every critical and warning finding" not in SKILL_TEXT, (
        "Step 3.5 must not restrict validation to only critical and warning findings"
    )
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1, "SKILL.md must have a Step 3.5 section"
    step4_idx = SKILL_TEXT.find("### Step 4")
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert "validate every finding" in step35_section, (
        "Step 3.5 must say 'validate every finding' (all severities)"
    )


def test_reject_threads_added_to_addressed_thread_ids() -> None:
    """REJECT findings must have their thread_node_id appended to addressed_thread_ids."""
    gate_idx = SKILL_TEXT.lower().find("classification gate")
    assert gate_idx != -1, "SKILL.md must define a classification gate section"
    # Find the end of the gate section (next heading or skip section)
    skip_idx = SKILL_TEXT.lower().find("skip a finding", gate_idx)
    assert skip_idx != -1, "SKILL.md must have a 'skip a finding' section after the gate"
    gate_section = SKILL_TEXT[gate_idx:skip_idx]
    # REJECT must now add to addressed_thread_ids
    assert "addressed_thread_ids" in gate_section, (
        "The REJECT path in the classification gate must append to addressed_thread_ids"
    )
    # The old blanket "Do NOT add these findings'" must not appear (it covered both REJECT+DISCUSS)
    assert (
        "Do NOT add these findings' `thread_node_id` to `addressed_thread_ids`" not in SKILL_TEXT
    ), "The blanket 'Do NOT add' instruction covering both REJECT and DISCUSS must be removed"


def test_discuss_threads_still_excluded_from_addressed_thread_ids() -> None:
    """DISCUSS findings must still NOT add to addressed_thread_ids (regression guard)."""
    gate_idx = SKILL_TEXT.lower().find("classification gate")
    assert gate_idx != -1, "SKILL.md must define a classification gate section"
    skip_idx = SKILL_TEXT.lower().find("skip a finding", gate_idx)
    assert skip_idx != -1
    gate_section = SKILL_TEXT[gate_idx:skip_idx]
    # DISCUSS must still be excluded
    assert "Do NOT add DISCUSS findings" in gate_section or (
        "discuss" in gate_section.lower() and "do not add" in gate_section.lower()
    ), "DISCUSS findings must still be excluded from addressed_thread_ids"


def test_step6_5_scope_includes_all_analyzed_comments() -> None:
    """Step 6.5 must not restrict scope to 'critical+warning filter' — all analyzed findings."""
    assert "critical+warning\nfilter in Step 3" not in SKILL_TEXT
    assert "critical+warning filter in Step 3" not in SKILL_TEXT, (
        "Step 6.5 scope must not reference the removed critical+warning filter"
    )
    step65_idx = SKILL_TEXT.find("### Step 6.5")
    assert step65_idx != -1, "SKILL.md must have a Step 6.5 section"
    step66_idx = SKILL_TEXT.find("### Step 6.6", step65_idx)
    step65_section = (
        SKILL_TEXT[step65_idx:step66_idx]
        if step66_idx != -1
        else SKILL_TEXT[step65_idx : step65_idx + 800]
    )
    assert "intent validation" in step65_section.lower() or "step 3.5" in step65_section, (
        "Step 6.5 scope should reference intent validation / Step 3.5 instead of the old severity filter"
    )


def test_report_does_not_mark_info_as_skipped() -> None:
    """Step 7 report template must not annotate info count with '(skipped — below threshold)'."""
    assert "(skipped — below threshold)" not in SKILL_TEXT, (
        "Step 7 report must not mark info findings as skipped — info is now fully assessed"
    )
    # The info count line must still be present (just without the skip annotation)
    step7_idx = SKILL_TEXT.find("### Step 7")
    assert step7_idx != -1, "SKILL.md must have a Step 7 (Report)"
    report_section = SKILL_TEXT[step7_idx:]
    assert "info: {n}" in report_section, "Step 7 report must still include the info count line"


def test_reject_no_code_changes_still_enforced() -> None:
    """REJECT path must still say 'no code changes are applied' (regression guard)."""
    gate_idx = SKILL_TEXT.lower().find("classification gate")
    assert gate_idx != -1
    skip_idx = SKILL_TEXT.lower().find("skip a finding", gate_idx)
    assert skip_idx != -1
    gate_section = SKILL_TEXT[gate_idx:skip_idx].lower()
    assert "no code changes are applied" in gate_section, (
        "REJECT path must still state 'no code changes are applied' — resolving the thread"
        " does not mean applying code changes"
    )
