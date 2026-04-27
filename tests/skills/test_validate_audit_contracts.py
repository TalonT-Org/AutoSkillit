"""Contract tests for the validate-audit skill SKILL.md."""

from __future__ import annotations

import functools

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import DefaultSkillResolver


@functools.cache
def _skill_text() -> str:
    info = DefaultSkillResolver().resolve("validate-audit")
    assert info is not None, "validate-audit skill not found"
    return info.path.read_text()


class TestValidateAuditSkillExists:
    # T-VAL-006
    def test_validate_audit_skill_exists(self) -> None:
        info = DefaultSkillResolver().resolve("validate-audit")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        assert info.path.exists()

    # T-VAL-007
    def test_validate_audit_has_audit_category(self) -> None:
        info = DefaultSkillResolver().resolve("validate-audit")
        assert info is not None
        assert "audit" in info.categories


class TestValidateAuditContent:
    # T-VAL-008
    def test_validated_true_marker_required(self) -> None:
        assert "validated: true" in _skill_text()

    # T-VAL-009
    def test_parallel_single_message_dispatch(self) -> None:
        text = _skill_text().lower()
        assert "single message" in text
        assert "parallel" in text

    # T-VAL-010
    def test_three_verdict_types_defined(self) -> None:
        text = _skill_text()
        assert "VALID" in text
        assert "VALID BUT EXCEPTION WARRANTED" in text
        assert "CONTESTED" in text

    # T-VAL-011
    def test_two_output_files(self) -> None:
        text = _skill_text()
        assert "validated_report_" in text
        assert "contested_findings_" in text

    # T-VAL-012
    def test_handles_all_three_audit_formats(self) -> None:
        text = _skill_text()
        assert "audit-arch" in text
        assert "audit-tests" in text
        assert "audit-cohesion" in text

    # T-VAL-013
    def test_interactive_headless_distinction(self) -> None:
        assert "Interactive vs Headless" in _skill_text()

    # T-VAL-014
    def test_output_dir(self) -> None:
        assert "{{AUTOSKILLIT_TEMP}}/validate-audit/" in _skill_text()

    # T-VAL-015
    def test_history_research_agent(self) -> None:
        assert "history research agent" in _skill_text().lower()


class TestValidateAuditNewSteps:
    # T-VAL-016
    def test_cross_validation_subagent_is_read_only(self) -> None:
        text = _skill_text().lower()
        assert "cross-valid" in text  # "cross-validator" or "cross-validation"
        assert "read-only" in text

    # T-VAL-017
    def test_ticket_grouping_manifest_with_finding_ids(self) -> None:
        text = _skill_text().lower()
        assert "grouping manifest" in text
        assert "finding id" in text

    # T-VAL-018
    def test_validation_summary_separate_file(self) -> None:
        assert "validation_summary_" in _skill_text()

    # T-VAL-019
    def test_validated_findings_contains_only_valid(self) -> None:
        text = _skill_text()
        # Must explicitly exclude exception-warranted findings from the validated report body
        assert (
            "do NOT include VALID BUT EXCEPTION WARRANTED" in text
            or "exception-warranted findings go exclusively" in text.lower()
            or "exception-warranted findings must not appear" in text.lower()
        )
