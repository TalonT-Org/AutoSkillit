"""Contract tests for the validate-audit skill SKILL.md."""

from __future__ import annotations

import functools

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import SkillResolver


@functools.cache
def _skill_text() -> str:
    info = SkillResolver().resolve("validate-audit")
    assert info is not None, "validate-audit skill not found"
    return info.path.read_text()


class TestValidateAuditSkillExists:
    # T-VAL-006
    def test_validate_audit_skill_exists(self) -> None:
        info = SkillResolver().resolve("validate-audit")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        assert info.path.exists()

    # T-VAL-007
    def test_validate_audit_has_audit_category(self) -> None:
        info = SkillResolver().resolve("validate-audit")
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
