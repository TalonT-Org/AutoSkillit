"""
Contract tests verifying the promote-to-main / review-promotion split.

Both skills now live in .claude/skills/ (project-local, not bundled). These tests
verify they exist at their new location and maintain their key contract properties.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_LOCAL_SKILLS = _REPO_ROOT / ".claude" / "skills"


class TestPromoteToMainProjectLocal:
    def _content(self) -> str:
        return (_LOCAL_SKILLS / "promote-to-main" / "SKILL.md").read_text()

    def test_promote_to_main_exists_in_project_local(self):
        """promote-to-main must exist as a project-local skill in .claude/skills/."""
        assert (_LOCAL_SKILLS / "promote-to-main" / "SKILL.md").exists()

    def test_promote_to_main_has_no_domain_analysis(self):
        """Streamlined promote-to-main must not perform domain risk analysis."""
        content = self._content()
        assert "partition_files_by_domain" not in content
        assert "risk_score" not in content

    def test_promote_to_main_has_no_quality_assessment(self):
        """Streamlined promote-to-main must not contain quality assessment subagents."""
        content = self._content()
        assert "Test Coverage Delta" not in content
        assert "Breaking Change Audit" not in content
        assert "Regression Risk Assessment" not in content

    def test_promote_to_main_outputs_pr_url_token(self):
        """promote-to-main must emit pr_url structured output token."""
        content = self._content()
        assert "pr_url = " in content

    def test_promote_to_main_arch_lens_loop_is_guarded(self):
        """The arch-lens diagram generation loop must have an anti-prose guard."""
        content = self._content()
        assert re.search(r"do not output any prose", content, re.IGNORECASE), (
            "promote-to-main arch-lens loop must have anti-prose guard"
        )


class TestReviewPromotionProjectLocal:
    def _content(self) -> str:
        return (_LOCAL_SKILLS / "review-promotion" / "SKILL.md").read_text()

    def test_review_promotion_exists_in_project_local(self):
        """review-promotion must exist as a project-local skill in .claude/skills/."""
        assert (_LOCAL_SKILLS / "review-promotion" / "SKILL.md").exists()

    def test_review_promotion_has_domain_analysis(self):
        """review-promotion must contain domain risk analysis."""
        content = self._content()
        assert "risk_score" in content
        assert "review_guidance" in content
        assert "partition_files_by_domain" in content

    def test_review_promotion_has_quality_assessment(self):
        """review-promotion must contain all three quality assessment dimensions."""
        content = self._content()
        assert "Test Coverage" in content
        assert "Breaking Change" in content
        assert "Regression Risk" in content

    def test_review_promotion_outputs_report_path_token(self):
        """review-promotion must emit report_path = <absolute path> token."""
        content = self._content()
        assert "report_path = " in content

    def test_review_promotion_outputs_verdict_token(self):
        """review-promotion must emit verdict = token with reviewer-specific values."""
        content = self._content()
        assert "verdict = " in content
        assert "review_ready" in content
        assert "needs_attention" in content
        assert "blocking_issues" in content

    def test_review_promotion_supports_post_to_pr(self):
        """review-promotion must support --post-to-pr for posting review as PR comment."""
        content = self._content()
        assert "--post-to-pr" in content
        assert "gh pr comment" in content

    def test_review_promotion_writes_to_skill_scoped_temp(self):
        """review-promotion must write to .autoskillit/temp/review-promotion/."""
        content = self._content()
        assert ".autoskillit/temp/review-promotion/" in content

    def test_review_promotion_has_subagent_autonomy_grant(self):
        """review-promotion must carry the Subagent Autonomy Grant."""
        content = self._content()
        assert (
            "Subagent Autonomy Grant" in content or "spawn additional subagents" in content.lower()
        )
