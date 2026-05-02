"""Contract tests for the audit-review-decisions skill SKILL.md."""

from __future__ import annotations

import functools

from autoskillit.core.types import SkillSource
from autoskillit.workspace.skills import DefaultSkillResolver


@functools.cache
def _skill_text() -> str:
    info = DefaultSkillResolver().resolve("audit-review-decisions")
    assert info is not None, "audit-review-decisions skill not found"
    return info.path.read_text()


class TestAuditReviewDecisionsExists:
    def test_skill_exists(self) -> None:
        info = DefaultSkillResolver().resolve("audit-review-decisions")
        assert info is not None
        assert info.source == SkillSource.BUNDLED_EXTENDED
        assert info.path.exists()

    def test_has_audit_category(self) -> None:
        info = DefaultSkillResolver().resolve("audit-review-decisions")
        assert info is not None
        assert "audit" in info.categories


class TestAuditReviewDecisionsContent:
    def test_graphql_alias_batching(self) -> None:
        text = _skill_text().lower()
        assert "alias" in text

    def test_ratelimit_in_queries(self) -> None:
        assert "rateLimit" in _skill_text()

    def test_haiku_triage_phase(self) -> None:
        text = _skill_text().lower()
        assert "haiku" in text
        assert "triage" in text or "broad pass" in text

    def test_sonnet_validation_phase(self) -> None:
        text = _skill_text().lower()
        assert "sonnet" in text
        assert "valid" in text

    def test_audit_watermark_marker(self) -> None:
        assert "[AUDIT]" in _skill_text()

    def test_review_flag_detection(self) -> None:
        assert "REVIEW-FLAG" in _skill_text()

    def test_output_dir(self) -> None:
        assert "audit-review-decisions/" in _skill_text()

    def test_report_title_matches_validate_audit_detection(self) -> None:
        """Report title must contain 'Review Decisions Audit' for validate-audit detection."""
        assert "Review Decisions Audit" in _skill_text()

    def test_structured_output_token(self) -> None:
        assert "review_decisions_audit_" in _skill_text()

    def test_sleep_between_mutating_calls(self) -> None:
        assert "sleep 1" in _skill_text()

    def test_idempotent_watermark(self) -> None:
        text = _skill_text().lower()
        assert "idempotent" in text or "already" in text
