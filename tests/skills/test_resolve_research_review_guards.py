"""Behavioral guards for resolve-research-review/SKILL.md.

Tests enforce research-dimension grouping, research REJECT vocabulary,
fix-strategy taxonomy, escalation protocol, ordered fix processing,
configurable validation, and all carry-over contracts from resolve-review.
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-research-review"
    / "SKILL.md"
)
SKILL_TEXT = SKILL_PATH.read_text()


def test_skill_path_exists() -> None:
    """SKILL.md must exist at the expected path."""
    assert SKILL_PATH.exists(), f"SKILL.md not found at {SKILL_PATH}"


# --- Frontmatter ---


def test_has_research_category() -> None:
    """Frontmatter must declare categories: [research]."""
    text = SKILL_TEXT
    assert any("research" in line for line in text.splitlines() if "categories:" in line), (
        "Frontmatter categories: field must include 'research'"
    )


# --- Dimension grouping (not file-path grouping) ---


def test_groups_by_dimension_not_path() -> None:
    """SKILL.md must describe grouping by research dimension, not file path segment."""
    text = SKILL_TEXT
    assert "dimension" in text.lower(), "SKILL.md must mention 'dimension' grouping"
    # Must NOT use top-level path segment grouping language from resolve-review
    assert "top-level path segment" not in text.lower(), (
        "resolve-research-review must NOT use top-level path segment grouping"
    )


def test_dimension_pattern_regex_present() -> None:
    """DIMENSION_PATTERN regex must be documented to extract dimension from comment body."""
    text = SKILL_TEXT
    assert "DIMENSION_PATTERN" in text or "dimension_pattern" in text.lower(), (
        "SKILL.md must define a DIMENSION_PATTERN for extracting dimension from comment body"
    )


def test_all_six_dimension_groups_present() -> None:
    """All six dimension group keys must be defined."""
    text = SKILL_TEXT.lower()
    for group in [
        "statistical",
        "methodology",
        "reproducibility",
        "reporting",
        "hygiene",
        "unknown",
    ]:
        assert group in text, f"SKILL.md must define dimension group '{group}'"


# --- Research REJECT vocabulary ---


def test_research_reject_categories_present() -> None:
    """REJECT must use research-specific category names."""
    text = SKILL_TEXT
    for category in [
        "methodology_misunderstanding",
        "false_positive_intentional",
        "inconclusive_not_deficiency",
        "out_of_scope",
        "stale_comment",
    ]:
        assert category in text, f"SKILL.md must define REJECT category '{category}'"


def test_inconclusive_not_deficiency_reject_category() -> None:
    """inconclusive_not_deficiency REJECT category must be explicitly present."""
    assert "inconclusive_not_deficiency" in SKILL_TEXT, (
        "SKILL.md must define the inconclusive_not_deficiency REJECT category — "
        "inconclusive results are valid findings and must never be rejected as deficiencies"
    )


# --- Fix strategy taxonomy ---


def test_fix_strategies_defined() -> None:
    """All five fix strategies must be defined."""
    text = SKILL_TEXT
    for strategy in ["report_edit", "script_fix", "config_fix", "rerun_required", "design_flaw"]:
        assert strategy in text, f"SKILL.md must define fix strategy '{strategy}'"


def test_report_edit_targets_research_md() -> None:
    """report_edit strategy must target research/*.md files."""
    text = SKILL_TEXT
    assert "report_edit" in text
    report_edit_idx = text.find("report_edit")
    context = text[report_edit_idx : report_edit_idx + 300]
    assert "research/" in context or ".md" in context, (
        "report_edit strategy must reference research/*.md as the edit target"
    )


def test_script_fix_targets_scripts() -> None:
    """script_fix strategy must target scripts/*.py or experiment code."""
    text = SKILL_TEXT
    assert "script_fix" in text
    script_fix_idx = text.find("script_fix")
    context = text[script_fix_idx : script_fix_idx + 300]
    assert "script" in context.lower() or ".py" in context, (
        "script_fix strategy must reference scripts/*.py or experiment code"
    )


# --- Escalation protocol ---


def test_escalation_protocol_documented() -> None:
    """SKILL.md must describe the escalation protocol."""
    text = SKILL_TEXT.lower()
    assert "escalat" in text, "SKILL.md must describe the escalation protocol"


def test_rerun_required_escalates_not_exits() -> None:
    """rerun_required must escalate (not exit non-zero) and NOT add to addressed_thread_ids."""
    text = SKILL_TEXT
    assert "rerun_required" in text
    rr_idx = text.find("rerun_required")
    context = text[rr_idx : rr_idx + 500].lower()
    assert "escalat" in context, "rerun_required must route to ESCALATE, not be applied as a fix"
    assert "do not" in context or "not add" in context or "not resolve" in context, (
        "rerun_required must NOT add to addressed_thread_ids"
    )


def test_design_flaw_escalates_not_exits() -> None:
    """design_flaw must escalate (not exit non-zero) and NOT add to addressed_thread_ids."""
    text = SKILL_TEXT
    assert "design_flaw" in text
    df_idx = text.find("design_flaw")
    context = text[df_idx : df_idx + 500].lower()
    assert "escalat" in context, "design_flaw must route to ESCALATE, not be applied as a fix"


def test_escalation_records_tracked() -> None:
    """Escalation findings must be recorded in escalation_records."""
    assert "escalation_records" in SKILL_TEXT, (
        "SKILL.md must track escalation findings in escalation_records list"
    )


def test_escalation_does_not_cause_exit_nonzero() -> None:
    """Escalation must not cause exit non-zero — only validation failure does."""
    text = SKILL_TEXT.lower()
    escalat_idx = text.find("escalat")
    assert "exit code remains 0" in text or (
        escalat_idx != -1 and "exit non-zero" not in text[escalat_idx : escalat_idx + 400]
    ), "SKILL.md must state escalation does not cause exit non-zero"


def test_escalation_findings_get_inline_reply() -> None:
    """Escalation findings must receive an inline reply (not just be recorded)."""
    text = SKILL_TEXT
    assert "ESCALATION" in text or "escalation" in text.lower()
    # Reply template section must include escalation reply
    reply_idx = text.lower().find("reply template")
    if reply_idx == -1:
        reply_idx = text.lower().find("reply")
    assert reply_idx != -1
    context = text[reply_idx : reply_idx + 800]
    assert "ESCALATION" in context or "escalat" in context.lower(), (
        "Reply templates must include escalation message templates"
    )


# --- Fix processing order ---


def test_fix_order_config_before_script_before_report() -> None:
    """Fix processing order must be config → script → report."""
    text = SKILL_TEXT
    config_idx = text.lower().find("config_fix")
    script_idx = text.lower().find("script_fix", config_idx + 1) if config_idx != -1 else -1
    report_idx = text.lower().find("report_edit", script_idx + 1) if script_idx != -1 else -1
    assert config_idx != -1, "SKILL.md must mention config_fix"
    assert script_idx != -1, "SKILL.md must mention script_fix after config_fix"
    assert report_idx != -1, "SKILL.md must mention report_edit after script_fix"
    assert config_idx < script_idx < report_idx, (
        "Fix order must be config_fix → script_fix → report_edit"
    )


# --- Configurable validation command ---


def test_validation_command_configurable() -> None:
    """SKILL.md must describe reading validation_command from .autoskillit/config.yaml."""
    text = SKILL_TEXT
    assert "validation_command" in text, "SKILL.md must define validation_command config key"
    assert "config.yaml" in text or ".autoskillit/config" in text, (
        "SKILL.md must read validation_command from .autoskillit/config.yaml"
    )


def test_null_validation_command_skips_validation() -> None:
    """null validation_command must skip the validation step."""
    text = SKILL_TEXT
    assert "null" in text or "None" in text or "skip" in text.lower(), (
        "SKILL.md must state null validation_command skips validation"
    )
    # validation_command context must mention skip
    vc_idx = text.find("validation_command")
    context = text[vc_idx : vc_idx + 500].lower()
    assert "null" in context or "skip" in context, (
        "SKILL.md must state that null validation_command means skip"
    )


def test_configured_validation_uses_retry_logic() -> None:
    """Configured validation_command must use retry logic (max 3 iterations)."""
    text = SKILL_TEXT.lower()
    assert "validation" in text and ("retry" in text or "iteration" in text or "iter" in text), (
        "SKILL.md must describe retry logic for the validation command"
    )


# --- Carry-over contracts from resolve-review ---


def test_pr_lookup_by_feature_branch() -> None:
    """PR must be found by feature branch via gh pr list."""
    text = SKILL_TEXT
    assert "gh pr list" in text or "gh pr" in text, "SKILL.md must look up PR by feature branch"


def test_graphql_thread_resolution() -> None:
    """resolveReviewThread mutation must be used to close addressed threads."""
    assert "resolveReviewThread" in SKILL_TEXT


def test_addressed_thread_ids_accumulated() -> None:
    """addressed_thread_ids must be accumulated for ACCEPT+fix findings only."""
    assert "addressed_thread_ids" in SKILL_TEXT


def test_reject_patterns_persisted() -> None:
    """REJECT findings must be persisted to a JSON file."""
    text = SKILL_TEXT
    assert "reject_patterns" in text or "reject patterns" in text.lower(), (
        "SKILL.md must persist reject patterns to JSON"
    )
    reject_idx = text.find("reject_patterns")
    assert reject_idx != -1 and ".json" in text[reject_idx : reject_idx + 50], (
        "SKILL.md must name the reject_patterns file with a .json extension"
    )


def test_max_three_fix_iterations() -> None:
    """Skill must enforce max 3 fix-and-retry iterations."""
    text = SKILL_TEXT.lower()
    assert "max 3" in text or "exceed 3" in text or "3 fix-and-retry" in text, (
        "SKILL.md must enforce a max of 3 fix iterations"
    )


def test_graceful_degradation_no_pr() -> None:
    """Skill must exit 0 gracefully when gh is unavailable or no PR found."""
    text = SKILL_TEXT.lower()
    assert "graceful" in text or (
        "exit 0" in text and ("unavailable" in text or "no pr" in text)
    ), "SKILL.md must describe graceful degradation when gh is unavailable or no PR found"


def test_inline_replies_posted() -> None:
    """Inline replies must be posted for every analyzed comment."""
    text = SKILL_TEXT.lower()
    assert "reply" in text and (
        "for every" in text or "every analyzed" in text or "reply api" in text
    ), "SKILL.md must describe posting inline replies for every analyzed comment"


def test_step7_report_includes_escalations() -> None:
    """Step 7 report must include ESCALATIONS section."""
    text = SKILL_TEXT
    report_idx = text.find("Step 7")
    assert report_idx != -1, "SKILL.md must have a Step 7"
    report_section = text[report_idx:]
    assert (
        "Escalation" in report_section
        or "ESCALATION" in report_section
        or "escalat" in report_section.lower()
    ), "Step 7 report must include escalation count"


def test_temp_dir_uses_resolve_research_review() -> None:
    """Temp files must use .autoskillit/temp/resolve-research-review/ directory."""
    assert ".autoskillit/temp/resolve-research-review/" in SKILL_TEXT, (
        "SKILL.md must use .autoskillit/temp/resolve-research-review/ for temp files"
    )


def test_dimension_groups_file_documented() -> None:
    """Temp file layout must include dimension_groups_{pr}.json."""
    text = SKILL_TEXT
    assert "dimension_groups_" in text or "dimension_groups" in text, (
        "SKILL.md must document dimension_groups_{pr}.json temp file"
    )
