"""
Contract tests verifying the promote-to-main / review-promotion split.

promote-to-main must be a bundled skill in skills_extended/ with no domain analysis
or quality assessment. review-promotion must be a new bundled skill with full
reviewer-facing analysis capabilities.
"""

import re


class TestPromoteToMainBundled:

    def test_promote_to_main_exists_in_skills_extended(self):
        """promote-to-main must be a bundled skill in skills_extended/ (not just local)."""
        from autoskillit.core.paths import pkg_root

        skill_path = pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md"
        assert skill_path.exists()

    def test_promote_to_main_has_no_domain_analysis(self):
        """Streamlined promote-to-main must not perform domain risk analysis."""
        from autoskillit.core.paths import pkg_root

        content = (pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md").read_text()
        # partition_files_by_domain is the marker for domain analysis logic
        assert "partition_files_by_domain" not in content
        assert "risk_score" not in content

    def test_promote_to_main_has_no_quality_assessment(self):
        """Streamlined promote-to-main must not contain quality assessment subagents."""
        from autoskillit.core.paths import pkg_root

        content = (pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md").read_text()
        assert "Test Coverage Delta" not in content
        assert "Breaking Change Audit" not in content
        assert "Regression Risk Assessment" not in content

    def test_promote_to_main_has_at_most_five_phases(self):
        """Streamlined promote-to-main must not exceed 5 top-level phases."""
        from autoskillit.core.paths import pkg_root

        content = (pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md").read_text()
        # Count top-level ### Phase N: headers
        phase_headers = re.findall(r"^###\s+Phase\s+\d+", content, re.MULTILINE)
        # Phase 0 counts, so 5 phases = Phase 0..4
        assert len(phase_headers) <= 6, (
            f"promote-to-main has {len(phase_headers)} phase headers; expected <= 6 (Phases 0-4 + sub-steps)"
        )

    def test_promote_to_main_outputs_pr_url_token(self):
        """promote-to-main must emit pr_url structured output token."""
        from autoskillit.core.paths import pkg_root

        content = (pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md").read_text()
        assert "pr_url = " in content

    def test_promote_to_main_arch_lens_loop_is_guarded(self):
        """The arch-lens diagram generation loop must have an anti-prose guard."""
        from autoskillit.core.paths import pkg_root

        content = (pkg_root() / "skills_extended" / "promote-to-main" / "SKILL.md").read_text()
        # Must have the anti-prose guard near the arch-lens loop
        assert re.search(r"do not output any prose", content, re.IGNORECASE), (
            "promote-to-main arch-lens loop must have anti-prose guard"
        )


class TestReviewPromotionSkill:

    def _content(self) -> str:
        from autoskillit.workspace.skills import SkillResolver

        info = SkillResolver().resolve("review-promotion")
        assert info is not None, "review-promotion skill must exist"
        return info.path.read_text()

    def test_review_promotion_skill_exists(self):
        """review-promotion must be discoverable via SkillResolver."""
        from autoskillit.workspace.skills import SkillResolver

        assert SkillResolver().resolve("review-promotion") is not None

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
        # Must define the three possible verdict values
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
        assert "Subagent Autonomy Grant" in content or "spawn additional subagents" in content.lower()
