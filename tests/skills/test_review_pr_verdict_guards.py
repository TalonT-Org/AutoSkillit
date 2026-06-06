"""Behavioral guard tests for review-pr/SKILL.md verdict logic.

These tests verify that the review-pr skill's finding schema and verdict logic
use the correct classification axis (actionability via requires_decision) rather
than a count-based threshold (len(warning_findings) > N).
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

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


def test_finding_schema_includes_requires_decision():
    """Finding schema in subagent prompt must include requires_decision field."""
    text = _skill_text()
    assert "requires_decision" in text, (
        "review-pr/SKILL.md subagent finding schema must include a 'requires_decision' "
        "field so subagents can classify actionability vs. genuine ambiguity."
    )


def test_verdict_logic_does_not_use_warning_count_threshold():
    """Verdict logic must not gate needs_human on len(warning_findings) > N."""
    text = _skill_text()
    assert "len(warning_findings)" not in text, (
        "review-pr/SKILL.md must not use len(warning_findings) to determine the "
        "'needs_human' verdict. The classification axis must be actionability "
        "(requires_decision field), not warning count."
    )


def test_needs_human_not_gated_on_numeric_threshold():
    """needs_human verdict must not appear adjacent to any numeric count condition."""
    text = _skill_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "needs_human" in line:
            context = "\n".join(lines[max(0, i - 4) : i + 5])
            assert not re.search(r"len\(\w+\)\s*>\s*\d+", context), (
                "needs_human verdict must not be gated on a numeric count threshold. "
                f"Found count-based condition near 'needs_human':\n{context}"
            )


def test_subagent_prompt_instructs_requires_decision_semantics():
    """Subagent prompt must explain when to set requires_decision true vs false."""
    text = _skill_text()
    # The prompt must mention both the true case (ambiguous) and false case (clear fix)
    assert "requires_decision" in text, (
        "Subagent prompt must instruct on requires_decision field usage."
    )
    lower = text.lower()
    has_false_guidance = "requires_decision=false" in lower or "requires_decision = false" in lower
    has_true_guidance = "requires_decision=true" in lower or "requires_decision = true" in lower
    assert has_false_guidance and has_true_guidance, (
        "Subagent prompt must instruct when to set requires_decision=true (genuine "
        "ambiguity only) AND requires_decision=false (all clear fixes, bugs, style)."
    )


def test_verdict_emits_on_final_stdout_line():
    """verdict= must appear as a final-line emit instruction in the skill."""
    text = _skill_text()
    assert "verdict=" in text, (
        "review-pr/SKILL.md must instruct the skill to emit 'verdict=' on the final "
        "output line so recipe capture blocks can extract it."
    )


def test_needs_human_prose_describes_genuine_ambiguity():
    """needs_human prose description must reference ambiguity, not warning count."""
    text = _skill_text()
    lower = text.lower()
    # The skill must describe needs_human in terms of decision/ambiguity
    has_ambiguity_framing = any(
        kw in lower for kw in ["ambig", "uncertain", "trade-off", "tradeoff", "decision"]
    )
    assert has_ambiguity_framing, (
        "needs_human verdict prose must describe it as triggered by genuine ambiguity "
        "or decisions requiring human judgment — not by a count of findings."
    )


def test_review_pr_own_pr_comment_retry():
    """SKILL.md must handle own-PR REQUEST_CHANGES 422 by retrying with COMMENT event."""
    skill_md = _skill_text()
    assert re.search(r"422.{0,300}COMMENT|COMMENT.{0,300}422", skill_md, re.DOTALL), (
        "SKILL.md must describe retrying with COMMENT event when batch POST returns "
        "HTTP 422 due to own-PR restriction"
    )


REVIEW_RESEARCH_PR_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-research-pr"
    / "SKILL.md"
)
AUDIT_CLAIMS_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "audit-claims"
    / "SKILL.md"
)


def _extract_degradation_block(text: str) -> str:
    """Extract the graceful-degradation block from a SKILL.md.

    Returns text from the first 'unavailable' trigger line through the next
    step heading (or 20 lines if no heading found).
    """
    lines = text.splitlines()
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if re.search(r"unavailable|graceful.{0,20}degrada", line, re.IGNORECASE):
            start = i
            break
    if start is None:
        return ""
    for i in range(start + 1, min(len(lines), start + 20)):
        if re.search(r"^#{1,3}\s+Step\s+\d", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def test_graceful_degradation_does_not_emit_approved() -> None:
    """review-pr degradation block must not emit verdict=approved."""
    text = _skill_text()
    block = _extract_degradation_block(text)
    assert block, "Could not find graceful degradation block in review-pr/SKILL.md"
    assert "verdict=approved" not in block and "verdict = approved" not in block, (
        "review-pr/SKILL.md must not emit verdict=approved on the graceful-degradation path. "
        "Use verdict=needs_human instead to distinguish zero-validation exits from real reviews."
    )
    assert "verdict=needs_human" in block or "verdict = needs_human" in block, (
        "review-pr/SKILL.md graceful-degradation block must emit verdict=needs_human."
    )


def test_review_research_pr_graceful_degradation_does_not_emit_approved() -> None:
    """review-research-pr degradation block must not emit verdict=approved."""
    text = REVIEW_RESEARCH_PR_SKILL_PATH.read_text()
    block = _extract_degradation_block(text)
    assert block, "Could not find graceful degradation block in review-research-pr/SKILL.md"
    assert "verdict=approved" not in block and "verdict = approved" not in block, (
        "review-research-pr/SKILL.md must not emit verdict=approved on the graceful-degradation "
        "path. Use verdict=needs_human instead."
    )
    assert "verdict=needs_human" in block or "verdict = needs_human" in block, (
        "review-research-pr/SKILL.md graceful-degradation block must emit verdict=needs_human."
    )


def test_audit_claims_graceful_degradation_does_not_emit_approved() -> None:
    """audit-claims degradation block must not emit verdict=approved."""
    text = AUDIT_CLAIMS_SKILL_PATH.read_text()
    block = _extract_degradation_block(text)
    assert block, "Could not find graceful degradation block in audit-claims/SKILL.md"
    assert "verdict=approved" not in block and "verdict = approved" not in block, (
        "audit-claims/SKILL.md must not emit verdict=approved on the graceful-degradation path. "
        "Use verdict=needs_human instead."
    )
    assert "verdict=needs_human" in block or "verdict = needs_human" in block, (
        "audit-claims/SKILL.md graceful-degradation block must emit verdict=needs_human."
    )


def test_contract_yamls_include_approved_with_comments() -> None:
    """All 3 contract YAML files must include approved_with_comments in
    expected_output_patterns and pattern_examples for the review-pr contract."""
    from autoskillit.core.io import load_yaml

    contracts_dir = (
        Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipes" / "contracts"
    )
    contract_files = [
        contracts_dir / "implementation.yaml",
        contracts_dir / "remediation.yaml",
        contracts_dir / "implementation-groups.yaml",
    ]
    for contract_path in contract_files:
        data = load_yaml(contract_path)
        review_pr = data.get("skills", {}).get("review-pr", {})
        patterns = review_pr.get("expected_output_patterns", [])
        examples = review_pr.get("pattern_examples", [])
        pattern_str = " ".join(patterns)
        assert "approved_with_comments" in pattern_str, (
            f"{contract_path.name}: expected_output_patterns for review-pr must include "
            "'approved_with_comments'"
        )
        examples_str = " ".join(examples)
        assert "approved_with_comments" in examples_str, (
            f"{contract_path.name}: pattern_examples for review-pr must include "
            "'approved_with_comments'"
        )
