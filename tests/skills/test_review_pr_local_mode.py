"""Tests for review-pr/SKILL.md local mode (mode=local) behavior.

Tests assert on SKILL.md content patterns for the local review round feature
(reducing GitHub API calls during iterative local review).
"""

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


def test_review_pr_skill_documents_mode_parameter():
    """Assert SKILL.md contains mode= parameter documentation in Arguments section."""
    text = _skill_text()
    assert "mode=<local|github>" in text, (
        "review-pr/SKILL.md Arguments section must document the mode= keyword argument "
        "with format: mode=<local|github>"
    )


def test_review_pr_local_mode_writes_local_findings():
    """Assert SKILL.md contains instructions to write findings to local_findings_{pr_number}.json
    when mode=local."""
    text = _skill_text()
    assert "local_findings" in text, (
        "review-pr/SKILL.md must contain 'local_findings' when mode=local, "
        "specifying the output file path pattern: "
        "{{AUTOSKILLIT_TEMP}}/review-pr/local_findings_{pr_number}.json"
    )
    assert "mode=local" in text, "review-pr/SKILL.md must reference 'mode=local' behavior"


def test_review_pr_local_mode_no_github_posts():
    """Assert SKILL.md contains explicit instruction to skip GitHub API calls when mode=local."""
    text = _skill_text()
    # Find the mode=local section
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0, "SKILL.md must contain 'mode=local'"
    # Check that within the local mode section, GitHub API posting is skipped
    after_local = text[local_mode_idx:]
    # The mode=local section should say to skip GitHub API calls
    assert any(
        phrase in after_local.lower()
        for phrase in [
            "skip all github api",
            "skip github api",
            "no github api",
            "do not post to github",
            "do not call github",
        ]
    ), (
        "review-pr/SKILL.md mode=local section must explicitly instruct to skip "
        "GitHub API calls for posting comments"
    )


def test_review_pr_local_mode_emits_gate_tokens():
    """Assert SKILL.md states that gate tokens (%%REVIEW_GATE::*) are emitted in both modes."""
    text = _skill_text()
    # Gate tokens must be emitted in both modes
    assert "%%REVIEW_GATE::LOOP_REQUIRED%%" in text, (
        "review-pr/SKILL.md must emit %%REVIEW_GATE::LOOP_REQUIRED%% on changes_requested"
    )
    assert "%%REVIEW_GATE::CLEAR%%" in text, (
        "review-pr/SKILL.md must emit %%REVIEW_GATE::CLEAR%% on approved/needs_human"
    )
    # Verify gate tokens are documented as mode-independent
    gate_doc_idx = text.lower().find("gate token")
    assert gate_doc_idx >= 0, "SKILL.md must document gate tokens"
    # Check near gate token documentation that both modes are mentioned
    gate_context = text[max(0, gate_doc_idx - 200) : gate_doc_idx + 400].lower()
    assert "mode" in gate_context, (
        "Gate token documentation must mention that emission is mode-independent"
    )


def test_review_pr_local_mode_default_is_github():
    """Assert SKILL.md states that absent/unrecognized mode defaults to github."""
    text = _skill_text()
    # Find the mode documentation — use a phrase specific to mode default, not generic "default:"
    assert "absent or unrecognized" in text.lower() or 'default to "github"' in text.lower(), (
        "review-pr/SKILL.md must document the default value for mode parameter using a "
        "specific phrase like 'absent or unrecognized' or 'default to \"github\"'"
    )
    # Verify github is the default
    local_mode_idx = text.lower().find("mode=github")
    assert local_mode_idx >= 0
    # The github mode should be described as the default (absent/unrecognized)
    mode_context = text[max(0, local_mode_idx - 300) : local_mode_idx + 200]
    assert "default" in mode_context.lower() or "absent" in mode_context.lower(), (
        "review-pr/SKILL.md must state that mode=github is the default when "
        "mode is absent or unrecognized"
    )


def test_review_pr_local_mode_iteration_tracking():
    """Assert SKILL.md tracks iteration number when writing local_findings for round counting."""
    text = _skill_text()
    assert "iteration" in text.lower(), (
        "review-pr/SKILL.md must track iteration number when writing local_findings.json "
        "to support round counting across local review cycles"
    )


def test_review_pr_local_mode_still_writes_diff_context():
    """Assert SKILL.md still writes diff_context_{pr_number}.json in local mode."""
    text = _skill_text()
    # Find mode=local section
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    assert "diff_context" in after_local, (
        "review-pr/SKILL.md mode=local section must still write diff_context_{pr_number}.json "
        "(mode-independent handoff file for resolve-review)"
    )


def test_review_pr_local_mode_still_writes_raw_findings():
    """Assert SKILL.md still writes raw_findings_{pr_number}.json in local mode."""
    text = _skill_text()
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    assert "raw_findings" in after_local, (
        "review-pr/SKILL.md mode=local section must still write raw_findings_{pr_number}.json "
        "(mode-independent)"
    )


def test_review_pr_local_mode_skips_step6_and_step7():
    """Assert SKILL.md mode=local skips Step 6 (GitHub posting) and Step 7 (review submission)."""
    text = _skill_text()
    local_mode_idx = text.lower().find("mode=local")
    assert local_mode_idx >= 0
    after_local = text[local_mode_idx : local_mode_idx + 2000]
    # Should skip to Step 8 after writing local file
    assert "step 8" in after_local.lower() or "skip" in after_local.lower(), (
        "review-pr/SKILL.md mode=local must skip to Step 8 (verdict emission) "
        "after writing local_findings.json, bypassing Step 6 GitHub posting and Step 7 submission"
    )


def test_review_pr_local_mode_json_format():
    """Assert SKILL.md specifies the JSON schema for local_findings output."""
    text = _skill_text()
    # Find the local_findings JSON format description
    local_findings_idx = text.find("local_findings")
    assert local_findings_idx >= 0
    after_local_findings = text[local_findings_idx : local_findings_idx + 1500]
    # Should have fields like path, line, body, severity, dimension, verdict, iteration
    assert '"findings"' in after_local_findings or "findings" in after_local_findings, (
        "review-pr/SKILL.md must specify the findings array in local_findings JSON schema"
    )
    assert "iteration" in after_local_findings.lower(), (
        "review-pr/SKILL.md must include iteration field in local_findings JSON schema"
    )


def test_review_pr_step6_mode_branching_header():
    """Assert Step 6 begins with MODE BRANCHING header that separates local vs github paths."""
    text = _skill_text()
    step6_idx = text.find("### Step 6")
    assert step6_idx >= 0, "SKILL.md must contain Step 6"
    step6_section = text[step6_idx : step6_idx + 200]
    assert "MODE" in step6_section or "mode" in step6_section, (
        "Step 6 must begin with mode branching to separate local vs github behavior"
    )
