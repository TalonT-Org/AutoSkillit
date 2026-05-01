"""Structural guards for resolve-review SKILL.md token-optimization edits.

Enforces that redundant addressed_thread_ids rules are collapsed to a single decision
table, best-effort language is unified across Steps 6 and 6.5, the Output section is
pruned of redundant graceful-degradation detail, the Step 3.5 JSON example is removed,
per-finding file/git-log reads are deduplicated, and the inline classification shortcut
for simple PRs is documented.
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
assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"
SKILL_TEXT = SKILL_PATH.read_text()


# ── 1. addressed_thread_ids decision table ────────────────────────────────────


def test_addressed_thread_ids_consolidated_decision_table() -> None:
    """All thread_node_id tracking rules must live in a single decision table."""
    # The plan collapses 5 scattered rules into one table.
    # Require the table header row that summarises all four cases.
    assert "Append to `addressed_thread_ids`" in SKILL_TEXT, (
        "SKILL.md must contain a consolidated decision table for thread_node_id tracking"
    )


def test_addressed_thread_ids_not_repeated_in_accept_flow() -> None:
    """After ACCEPT fix commit, inline 'append addressed_thread_ids' must be removed."""
    step4_idx = SKILL_TEXT.find("### Step 4")
    step5_idx = SKILL_TEXT.find("### Step 5", step4_idx)
    step4_text = SKILL_TEXT[step4_idx:step5_idx]
    # Count occurrences of the old inline rule inside Step 4's commit block
    accept_block_end = step4_text.find("**Classification gate")
    accept_block = step4_text[:accept_block_end] if accept_block_end != -1 else step4_text[:800]
    assert "Append the finding's `thread_node_id` to `addressed_thread_ids`" not in accept_block, (
        "The inline 'append thread_node_id' after ACCEPT commit must be removed; "
        "use the consolidated decision table instead"
    )


def test_addressed_thread_ids_not_in_skip_flow() -> None:
    """The 'skip a finding' flow must not repeat addressed_thread_ids guidance."""
    skip_idx = SKILL_TEXT.lower().find("skip a finding flow")
    assert skip_idx != -1, "SKILL.md must have a 'skip a finding flow' section"
    skip_section = SKILL_TEXT[skip_idx : skip_idx + 400]
    assert "`addressed_thread_ids`" not in skip_section, (
        "The 'skip a finding flow' section must not repeat thread_node_id guidance; "
        "all cases are covered by the consolidated decision table"
    )


def test_addressed_thread_ids_not_in_file_level_guard() -> None:
    """File-level comment guard must not repeat addressed_thread_ids guidance."""
    guard_idx = SKILL_TEXT.find("File-level comment guard")
    assert guard_idx != -1, "SKILL.md must have a file-level comment guard"
    guard_section = SKILL_TEXT[guard_idx : guard_idx + 300]
    assert "`addressed_thread_ids`" not in guard_section, (
        "File-level comment guard must not repeat thread_node_id guidance; "
        "the consolidated decision table covers this case"
    )


# ── 2. Best-effort language unified ──────────────────────────────────────────


def test_best_effort_unified_across_steps_6_and_65() -> None:
    """Step 6 best-effort statement must cover both Steps 6 and 6.5."""
    step6_idx = SKILL_TEXT.find("### Step 6:")
    if step6_idx == -1:
        step6_idx = SKILL_TEXT.find("### Step 6\n")
    step65_idx = SKILL_TEXT.find("### Step 6.5", step6_idx)
    step6_section = SKILL_TEXT[step6_idx:step65_idx]
    assert "6.5" in step6_section, (
        "Step 6's best-effort statement must reference Step 6.5 so it covers both steps"
    )


def test_step65_no_standalone_best_effort_statement() -> None:
    """Step 6.5 must not repeat a standalone 'best-effort' / exit-code statement."""
    step65_idx = SKILL_TEXT.find("### Step 6.5")
    assert step65_idx != -1, "SKILL.md must have a Step 6.5 section"
    step66_idx = SKILL_TEXT.find("### Step 6.6", step65_idx)
    step65_section = (
        SKILL_TEXT[step65_idx:step66_idx]
        if step66_idx != -1
        else SKILL_TEXT[step65_idx : step65_idx + 1200]
    )
    assert (
        "best-effort: failure to post any reply must not affect the exit code"
        not in step65_section
    ), (
        "Step 6.5 must not contain a standalone best-effort/exit-code statement; "
        "that is now unified in Step 6"
    )


# ── 3. Output section pruned ─────────────────────────────────────────────────


def test_output_section_no_graceful_degradation_detail() -> None:
    """The ## Output section must not repeat 'graceful degradation' detail."""
    output_idx = SKILL_TEXT.rfind("## Output")
    assert output_idx != -1, "SKILL.md must have an ## Output section"
    output_section = SKILL_TEXT[output_idx:]
    assert "graceful degradation" not in output_section.lower(), (
        "## Output section must not repeat graceful-degradation language; "
        "Step 1 and Step 7 already state this"
    )


# ── 4. Step 3.5 JSON example removed ─────────────────────────────────────────


def test_step35_json_example_removed() -> None:
    """The illustrative JSON example block (comment_id: 123) must be removed from Step 3.5."""
    assert '"comment_id": 123' not in SKILL_TEXT, (
        "The Step 3.5 JSON example block must be removed; "
        "the schema is fully described by the preceding field list"
    )


# ── 5. Per-unique-file read in sub-agent instructions ────────────────────────


def test_per_unique_file_read_instruction() -> None:
    """Sub-agent instructions must say to read each unique file once, not once per finding."""
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1, "SKILL.md must have a Step 3.5 section"
    step4_idx = SKILL_TEXT.find("### Step 4", step35_idx)
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert "unique file" in step35_section.lower(), (
        "Step 3.5 sub-agent instructions must say 'unique file' (read once per file, "
        "not once per finding)"
    )


def test_per_finding_read_instruction_removed() -> None:
    """The old 'read the actual code at each flagged line' per-finding phrasing must be gone."""
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1
    step4_idx = SKILL_TEXT.find("### Step 4", step35_idx)
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert "read the actual code at the flagged line" not in step35_section.lower(), (
        "The old per-finding file-read instruction must be replaced with the per-unique-file "
        "read instruction"
    )


# ── 6. Per-unique-path git log ────────────────────────────────────────────────


def test_per_unique_path_git_log() -> None:
    """Git log instruction must say 'once per unique path, not once per finding'."""
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1
    step4_idx = SKILL_TEXT.find("### Step 4", step35_idx)
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert (
        "once per unique path" in step35_section.lower()
        or "per unique path" in step35_section.lower()
    ), "Step 3.5 git log instruction must say 'once per unique path' (not once per finding)"


# ── 7. Inline classification shortcut for simple PRs ─────────────────────────


def test_inline_classification_shortcut_documented() -> None:
    """Step 3.5 must document an inline classification path for ≤3 findings."""
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1
    step4_idx = SKILL_TEXT.find("### Step 4", step35_idx)
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    assert (
        "3 or fewer" in step35_section.lower()
        or "≤3" in step35_section
        or "inline" in step35_section.lower()
    ), "Step 3.5 must document an inline classification shortcut for PRs with 3 or fewer findings"


def test_inline_shortcut_requires_single_domain_group() -> None:
    """The inline shortcut condition must require both ≤3 findings AND a single domain group."""
    step35_idx = SKILL_TEXT.find("### Step 3.5")
    assert step35_idx != -1
    step4_idx = SKILL_TEXT.find("### Step 4", step35_idx)
    step35_section = SKILL_TEXT[step35_idx:step4_idx]
    inline_idx = step35_section.lower().find("inline")
    assert inline_idx != -1, "Step 3.5 must mention 'inline' classification"
    inline_context = step35_section[max(0, inline_idx - 200) : inline_idx + 400].lower()
    assert (
        "single domain" in inline_context
        or "one domain" in inline_context
        or ("single" in inline_context and "group" in inline_context)
    ), "The inline classification shortcut must require a single domain group condition"
