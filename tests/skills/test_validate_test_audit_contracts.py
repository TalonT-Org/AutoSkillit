"""Contract tests for the validate-test-audit skill SKILL.md."""

from __future__ import annotations

import functools
import re

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import DefaultSkillResolver


@functools.cache
def _skill_text() -> str:
    info = DefaultSkillResolver().resolve("validate-test-audit")
    assert info is not None, "validate-test-audit skill not found"
    return info.path.read_text(encoding="utf-8")


class TestValidateTestAuditSkillExists:
    # T-VTA-001
    def test_validate_test_audit_skill_exists(self) -> None:
        info = DefaultSkillResolver().resolve("validate-test-audit")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        assert info.path.exists()

    # T-VTA-002
    def test_validate_test_audit_has_audit_category(self) -> None:
        info = DefaultSkillResolver().resolve("validate-test-audit")
        assert info is not None
        assert "audit" in info.categories


class TestValidateTestAuditContent:
    # T-VTA-003
    def test_validated_true_marker_required(self) -> None:
        assert "validated: true" in _skill_text()

    # T-VTA-004
    def test_parallel_single_message_dispatch(self) -> None:
        text = _skill_text().lower()
        assert "single message" in text
        assert "parallel" in text

    # T-VTA-005
    def test_three_verdict_types_defined(self) -> None:
        text = _skill_text()
        assert "VALID" in text
        assert "VALID BUT EXCEPTION WARRANTED" in text
        assert "CONTESTED" in text

    # T-VTA-006
    def test_output_files(self) -> None:
        text = _skill_text()
        assert "validated_report_" in text
        assert "contested_findings_" in text

    # T-VTA-007
    def test_handles_audit_tests_format_only(self) -> None:
        text = _skill_text()
        assert "audit-tests" in text
        assert "Test Suite Audit" in text

    # T-VTA-008
    def test_output_dir_matches_validate_audit(self) -> None:
        assert "{{AUTOSKILLIT_TEMP}}/validate-audit/" in _skill_text()

    # T-VTA-009
    def test_history_research_agent(self) -> None:
        assert "history research agent" in _skill_text().lower()

    # T-VTA-010
    def test_cross_validation_subagent_is_read_only(self) -> None:
        text = _skill_text().lower()
        assert "cross-valid" in text
        assert "read-only" in text

    # T-VTA-011
    def test_ticket_grouping_manifest_with_finding_ids(self) -> None:
        text = _skill_text().lower()
        assert "grouping manifest" in text
        assert "finding id" in text

    # T-VTA-012
    def test_validation_summary_separate_file(self) -> None:
        assert "validation_summary_" in _skill_text()

    # T-VTA-013
    def test_validated_findings_contains_only_valid(self) -> None:
        text = _skill_text()
        assert (
            "do NOT include VALID BUT EXCEPTION WARRANTED" in text
            or "exception-warranted findings go exclusively" in text.lower()
        )

    # T-VTA-014
    def test_interactive_headless_distinction(self) -> None:
        assert "Interactive vs Headless" in _skill_text()


class TestValidateTestAuditSemanticRules:
    # T-VTA-015
    def test_semantic_rule_import_path_as_contract(self) -> None:
        text = _skill_text().lower()
        assert "import" in text, "expected 'import' in skill text"
        assert "contract" in text, "expected 'contract' in skill text"
        assert "existence guard" in text

    # T-VTA-016
    def test_semantic_rule_precondition_as_assertion(self) -> None:
        text = _skill_text().lower()
        assert "precondition" in text, "expected 'precondition' in skill text"
        assert "assertion" in text, "expected 'assertion' in skill text"

    # T-VTA-017
    def test_semantic_rule_provenance_verification(self) -> None:
        text = _skill_text().lower()
        assert "__module__" in text or "provenance" in text
        assert "provenance guard" in text or "provenance verification" in text

    # T-VTA-018
    def test_semantic_rule_split_era_lifecycle(self) -> None:
        text = _skill_text().lower()
        assert "split-era" in text, "expected 'split-era' in skill text"
        assert "lifecycle" in text or "structural contract" in text

    # T-VTA-019
    def test_semantic_rule_deletion_vs_improvement(self) -> None:
        text = _skill_text().lower()
        assert "deletion" in text, "expected 'deletion' in skill text"
        assert "improvement" in text, "expected 'improvement' in skill text"
        assert "improving" in text or "improve" in text


class TestValidateTestAuditIntentAnalysis:
    # T-VTA-020
    def test_intent_analysis_step_exists(self) -> None:
        text = _skill_text().lower()
        assert "intent analysis" in text or "intent determination" in text

    # T-VTA-021
    def test_intent_analysis_git_provenance(self) -> None:
        text = _skill_text().lower()
        assert "git log" in text or "git provenance" in text
        assert "introducing commit" in text or "commit message" in text

    # T-VTA-022
    def test_intent_analysis_co_creation_context(self) -> None:
        text = _skill_text().lower()
        assert "co-creation" in text or "same commit" in text

    # T-VTA-023
    def test_intent_analysis_naming_signals(self) -> None:
        text = _skill_text().lower()
        assert "naming signal" in text or "test file name" in text or "function name" in text

    # T-VTA-024
    def test_intent_analysis_redundancy_check(self) -> None:
        text = _skill_text().lower()
        assert "redundancy check" in text or "covered by another test" in text


class TestValidateTestAuditInputHandling:
    # T-VTA-025
    def test_auto_discover_audit_tests(self) -> None:
        assert "{{AUTOSKILLIT_TEMP}}/audit-tests/" in _skill_text()

    # T-VTA-026
    def test_semantic_rules_are_generalizable(self) -> None:
        # Must not reference specific finding IDs like C1-3, C1-4
        text = _skill_text()
        finding_ids = re.findall(r"C\d+-\d+", text)
        assert len(finding_ids) == 0, f"Found specific finding IDs: {finding_ids}"
