"""Contract tests for the validate-review-decisions skill SKILL.md."""

from __future__ import annotations

import functools
import re

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import DefaultSkillResolver
from tests.skills.conftest import assert_ticket_grouper_has_minimum_group_floor


@functools.cache
def _skill_text() -> str:
    info = DefaultSkillResolver().resolve("validate-review-decisions")
    assert info is not None, "validate-review-decisions skill not found"
    return info.path.read_text(encoding="utf-8")


class TestValidateReviewDecisionsSkillExists:
    # T-VRD-001
    def test_skill_exists(self) -> None:
        info = DefaultSkillResolver().resolve("validate-review-decisions")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        assert info.path.exists()

    # T-VRD-002
    def test_has_audit_category(self) -> None:
        info = DefaultSkillResolver().resolve("validate-review-decisions")
        assert info is not None
        assert "audit" in info.categories


class TestValidateReviewDecisionsContent:
    # T-VRD-003
    def test_validated_true_marker_required(self) -> None:
        assert "validated: true" in _skill_text()

    # T-VRD-004
    def test_parallel_single_message_dispatch(self) -> None:
        text = _skill_text().lower()
        assert "single message" in text
        assert "parallel" in text

    # T-VRD-005
    def test_three_verdict_types_defined(self) -> None:
        text = _skill_text()
        assert "VALID" in text
        assert "VALID BUT EXCEPTION WARRANTED" in text
        assert "CONTESTED" in text

    # T-VRD-006
    def test_output_files(self) -> None:
        text = _skill_text()
        assert "validated_report_" in text
        assert "contested_findings_" in text

    # T-VRD-007
    def test_handles_review_decisions_format_only(self) -> None:
        text = _skill_text()
        assert "audit-review-decisions" in text
        assert "Review Decisions Audit" in text

    # T-VRD-008
    def test_history_research_agent(self) -> None:
        assert "history research agent" in _skill_text().lower()

    # T-VRD-009
    def test_cross_validation_subagent_is_read_only(self) -> None:
        text = _skill_text().lower()
        assert "cross-valid" in text
        assert "read-only" in text

    # T-VRD-010
    def test_ticket_grouping_manifest_with_finding_ids(self) -> None:
        text = _skill_text().lower()
        assert "grouping manifest" in text
        assert "finding id" in text

    # T-VRD-011
    def test_validation_summary_separate_file(self) -> None:
        assert "validation_summary_" in _skill_text()

    # T-VRD-012
    def test_validated_findings_contains_only_valid(self) -> None:
        text = _skill_text()
        assert "do NOT include VALID BUT EXCEPTION WARRANTED" in text

    # T-VRD-013
    def test_interactive_headless_distinction(self) -> None:
        assert "Interactive vs Headless" in _skill_text()

    # T-VRD-014
    def test_audit_run_dir_support(self) -> None:
        assert "AUTOSKILLIT_AUDIT_RUN_DIR" in _skill_text()

    # T-VRD-015
    def test_pr_provenance_metadata_preserved(self) -> None:
        text = _skill_text()
        assert "PR provenance" in text or "PR Number" in text
        assert "Reviewer Quote" in text

    # T-VRD-016
    def test_review_decisions_source_name(self) -> None:
        text = _skill_text()
        assert "review_decisions" in text

    # T-VRD-017
    def test_validated_report_path_emit(self) -> None:
        assert "validated_report_path" in _skill_text()


class TestValidateReviewDecisionsIntentAnalysis:
    # T-VRD-018
    def test_intent_analysis_step_exists(self) -> None:
        text = _skill_text().lower()
        assert "intent analysis" in text

    # T-VRD-019
    def test_intent_analysis_docstring_check(self) -> None:
        text = _skill_text().lower()
        assert "docstring" in text

    # T-VRD-020
    def test_intent_analysis_git_provenance(self) -> None:
        text = _skill_text().lower()
        assert "git log" in text or "git provenance" in text

    # T-VRD-021
    def test_intent_analysis_test_coverage(self) -> None:
        text = _skill_text().lower()
        assert "test coverage" in text

    # T-VRD-022
    def test_intent_analysis_contract_analysis(self) -> None:
        text = _skill_text().lower()
        assert "contract analysis" in text or "consumer" in text

    # T-VRD-023
    def test_intent_analysis_architectural_constraint(self) -> None:
        text = _skill_text()
        assert "architectural constraint" in text or "IL import" in text

    # T-VRD-024
    def test_intent_analysis_behavioral_simulation(self) -> None:
        text = _skill_text().lower()
        assert "simulation" in text or "simulate" in text


class TestValidateReviewDecisionsEvidenceGatheringRules:
    # T-VRD-025
    def test_rule_docstring_as_contract(self) -> None:
        text = _skill_text().lower()
        assert "docstring" in text
        assert "contract" in text

    # T-VRD-026
    def test_rule_deliberate_change_detection(self) -> None:
        text = _skill_text().lower()
        assert "deliberate" in text
        assert "intentional" in text

    # T-VRD-027
    def test_rule_test_as_intent_signal(self) -> None:
        text = _skill_text().lower()
        assert "test-as-intent-signal" in text

    # T-VRD-028
    def test_rule_consumer_impact_verification(self) -> None:
        text = _skill_text().lower()
        assert "consumer" in text
        assert "impact" in text

    # T-VRD-029
    def test_rule_architectural_feasibility_check(self) -> None:
        text = _skill_text().lower()
        assert "architectural" in text
        assert "feasibility" in text

    # T-VRD-030
    def test_rule_behavioral_simulation(self) -> None:
        text = _skill_text().lower()
        assert "behavioral" in text
        assert "simulation" in text

    # T-VRD-031
    def test_rule_symmetry_as_design(self) -> None:
        text = _skill_text().lower()
        assert "symmetry" in text
        assert "design" in text


class TestValidateReviewDecisionsInputHandling:
    # T-VRD-032
    def test_auto_discover_audit_review_decisions(self) -> None:
        assert "{{AUTOSKILLIT_TEMP}}/audit-review-decisions/" in _skill_text()

    # T-VRD-033
    def test_evidence_rules_are_generalizable(self) -> None:
        # Must not reference specific finding IDs like F01, F07, F13
        text = _skill_text()
        finding_ids = re.findall(r"F\d{2}", text)
        assert len(finding_ids) == 0, f"Found specific finding IDs: {finding_ids}"


class TestValidateReviewDecisionsTicketGrouper:
    def test_ticket_grouper_has_minimum_group_floor(self) -> None:
        """Ticket Grouper instructions must enforce a minimum group count."""

        assert_ticket_grouper_has_minimum_group_floor(_skill_text())
