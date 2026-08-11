"""Contract tests for the file-audit-issues skill SKILL.md."""

from __future__ import annotations

import functools
import re

import pytest

from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


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


class TestFileAuditIssuesMutationContracts:
    def test_issue_creation_produces_node_id_maps(self) -> None:
        text = _skill_text()

        assert "issue { id number url }" in text
        assert "alias-to-issue-ID map" in text
        assert "ticket-body-file-to-issue-ID" in text

    def test_label_ids_have_inventory_producers(self) -> None:
        text = _skill_text()

        inventory = text.index("gh label list --limit 1000 --json name,id")
        creation = text.index("createLabel")
        application = text.index("addLabelsToLabelable")
        assert inventory < creation < application
        assert "label-name-to-node-ID map" in text
        assert "actual source label" in text

    def test_graphql_mutations_use_prior_bounded_payload_files(self) -> None:
        text = _skill_text()

        assert "--input -" not in text
        assert 'echo "$MUTATION_JSON"' not in text
        assert 'echo "$LABEL_MUTATION"' not in text
        assert text.count("separate completed tool call") >= 3
        assert text.count('gh api graphql --input "/absolute/audit-run/') == 3

    def test_mutation_chunks_retain_pacing_and_size(self) -> None:
        text = _skill_text()

        assert "chunked at 20 issues per" in text
        assert "chunks of 20" in text
        assert "sleep 1" in text
        assert "one-second pacing between every consecutive mutating" in text
