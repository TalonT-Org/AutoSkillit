"""Structural guards for server-owned review-audit ingestion and finalization."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)


def _section(text: str, start_heading: str, end_heading: str | None = None) -> str:
    start = text.index(start_heading)
    if end_heading is None:
        return text[start:]
    end = text.index(end_heading, start + len(start_heading))
    return text[start:end]


def test_review_audit_plan_collect_and_finalize_are_the_only_ingestion_path() -> None:
    text = _SKILL_PATH.read_text()
    step3 = _section(text, "### Step 3", "### Step 4")
    step4 = _section(text, "### Step 4", "### Step 4.5")
    step8 = _section(text, "### Step 8")
    step3_normalized = " ".join(step3.split())
    step4_normalized = " ".join(step4.split())
    step8_normalized = " ".join(step8.split())

    plan_call = text.index("plan_review_audit(")
    collect_call = text.index("collect_review_audit(")
    finalize_call = text.index("finalize_review_audit(")
    assert plan_call < collect_call < finalize_call
    assert "collect_review_audit(" in step3

    removed_transcription_paths = (
        "STANDARD_AUDITOR_RAW_FINDINGS",
        "json.loads(STANDARD",
        "STANDARD_VALIDATION_ERRORS",
        "validate_experimental_auditor_outputs(",
        "aggregate_combined_review_candidates(",
        "determine_experimental_review_verdict(",
        "select_experimental_review_dispatch(",
        "deletion_regression_is_eligible(",
        "EXPERIMENTAL_OUTCOMES_BY_NAME",
        "AUDITOR_STATUS_BY_NAME",
        "parent-generated",
    )
    assert all(phrase not in text for phrase in removed_transcription_paths)

    assert "marker_line" in step3_normalized
    assert "slot_token" in step3_normalized
    assert "relaunch" in step3_normalized.lower()
    assert "Start every child prompt with that slot's `marker_line` verbatim" in step3_normalized
    contract = "End your final message with exactly one fenced code block"
    prompt_contracts: list[str] = []
    cursor = 0
    for _ in range(2):
        start = step3_normalized.index(contract, cursor)
        end = step3_normalized.index(
            "Do not emit any other json block in the final message.", start
        )
        prompt_contracts.append(step3_normalized[start:end])
        cursor = end
    for prompt_contract in prompt_contracts:
        assert "```json" in prompt_contract
        assert (
            "whose closing line is" in prompt_contract
            or "closes with a line containing only" in prompt_contract
        )
        assert "complete JSON array of findings" in prompt_contract
        assert "empty array [] in that block" in prompt_contract

    assert "Transcribe, merge, summarize, repair, or re-type any auditor output" in text
    assert "Compute, assume, or hard-code the gate state, audit state" in text
    for field in ("candidate_id", "reason_code", "explanation"):
        assert field in step4_normalized
    assert "one disposition per `EXPERIMENTAL_CANDIDATES` item" in step4_normalized
    assert "FINAL_REVIEW_FINDINGS = FILTERED_FINDINGS = survivors" in step4_normalized
    assert "UNPOSTABLE_FINDINGS = unpostable" in step4_normalized
    assert "REVIEW_LEVEL_FINDINGS = review_level_findings" in step4_normalized
    assert 'verdict = AUDIT_FINALIZATION["verdict"]' in step4_normalized

    assert "finalize-issued `AUDITOR_RECORDS` terminal-status authority" in step8_normalized
    assert 'verdict = "' not in text[plan_call:]


def test_review_audit_plan_uses_caller_repository_and_no_model_supplied_head() -> None:
    text = _SKILL_PATH.read_text()
    plan_start = text.index("plan_review_audit(")
    plan_end = text.index(")", plan_start) + 1
    plan_call = " ".join(text[plan_start:plan_end].split())
    step6 = " ".join(_section(text, "### Step 6", "### Step 7").split())

    assert 'repository="{repository}"' in plan_call
    assert "pr_head_sha" not in plan_call
    assert "repository" in step6
    assert "owner/repo" in step6


def test_review_audit_dispositions_have_only_the_documented_fields() -> None:
    text = _SKILL_PATH.read_text()
    step4 = " ".join(_section(text, "### Step 4", "### Step 4.5").split())
    dispositions_start = step4.index("AUDIT_DISPOSITIONS")
    disposition_section = step4[dispositions_start:]

    assert all(
        field in disposition_section for field in ("candidate_id", "reason_code", "explanation")
    )
    assert "Each entry contains exactly `candidate_id`, `reason_code`, and `explanation`" in step4
    assert "parent-generated `disposition_id`" not in disposition_section
    assert "server mints disposition identities" in disposition_section
