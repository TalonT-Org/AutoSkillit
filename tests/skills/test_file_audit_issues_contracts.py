"""Contract tests for the file-audit-issues skill SKILL.md."""

from __future__ import annotations

import functools
import re

from autoskillit.workspace.skills import DefaultSkillResolver


@functools.cache
def _skill_text() -> str:
    info = DefaultSkillResolver().resolve("file-audit-issues")
    assert info is not None, "file-audit-issues skill not found"
    return info.path.read_text()


class TestFileAuditIssuesBodyDiscipline:
    def test_no_validation_summary_append_step(self) -> None:
        """SKILL.md must not contain a step that appends validation summaries."""
        text = _skill_text()
        assert "Append Validation Summar" not in text, (
            "file-audit-issues must not append validation summaries to issue bodies"
        )

    def test_no_gh_issue_edit_with_validation_summary(self) -> None:
        """SKILL.md must not instruct editing issues to add validation summary content."""
        text = _skill_text()
        has_edit = "gh issue edit" in text
        has_summary = (
            "validation_summary" in text.split("## Critical Constraints")[0]
            if "## Critical Constraints" in text
            else "validation_summary" in text
        )
        assert not (has_edit and has_summary), (
            "file-audit-issues must not combine gh issue edit with validation_summary references"
        )

    def test_body_size_ceiling_documented(self) -> None:
        """SKILL.md must document a body size ceiling for ticket bodies."""
        text = _skill_text()
        has_size_guard = bool(
            re.search(
                r"(?:body.*size|size.*limit|exceed|threshold|10.?KB|65.?536)",
                text,
                re.IGNORECASE,
            )
        )
        assert has_size_guard, (
            "file-audit-issues must document a body size ceiling "
            "to prevent oversized issues from reaching GitHub"
        )
