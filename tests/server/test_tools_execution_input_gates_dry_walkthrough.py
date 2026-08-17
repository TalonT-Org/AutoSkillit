"""Tests for the dry-walkthrough gate (`_check_dry_walkthrough`) and prefix-time
firing for /implement-worktree variants.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.server._guards import _check_dry_walkthrough
from autoskillit.server._state import _get_config
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestCheckDryWalkthrough:
    """Dry-walkthrough gate blocks both /implement-worktree variants."""

    def test_dry_walkthrough_gate_blocks_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate blocks /implement-worktree-no-merge when plan lacks marker."""
        plan = tmp_path / "plan.md"
        plan.write_text("# My Plan\n\nSome content")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True
        assert "dry-walked" in parsed["result"].lower()

    def test_dry_walkthrough_gate_passes_implement_no_merge(self, tool_ctx, tmp_path):
        """Gate allows /implement-worktree-no-merge when plan has marker."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        plan = plan_dir / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# My Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is None

    def test_dry_walkthrough_gate_still_works_for_implement_worktree(self, tool_ctx, tmp_path):
        """Original /implement-worktree gating is not broken."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = _check_dry_walkthrough(f"/implement-worktree {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["is_error"] is True

    def test_dry_walkthrough_gate_ignores_unrelated_skills(self, tool_ctx):
        """Gate ignores skills that are not implement-worktree variants."""
        result = _check_dry_walkthrough("/autoskillit:investigate some-error", "/tmp")
        assert result is None

    def test_dry_walkthrough_gate_with_part_a_named_file_marked(self, tmp_path, tool_ctx):
        """Gate accepts _part_a.md file when marker is present."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        plan = plan_dir / "task_plan_2026-01-01_part_a.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n\nContent here")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is None

    def test_dry_walkthrough_gate_with_part_b_named_file_unmarked(self, tmp_path, tool_ctx):
        """Gate blocks _part_b.md file when marker is absent."""
        plan = tmp_path / "task_plan_2026-01-01_part_b.md"
        plan.write_text("> **PART B ONLY.**\n\nNo walkthrough marker here")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "gate_error"

    def test_dry_walkthrough_gate_distinguishes_parts_independently(self, tmp_path, tool_ctx):
        """Gate correctly distinguishes marked part_a from unmarked part_b."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        part_a = plan_dir / "task_plan_part_a.md"
        part_b = tmp_path / "task_plan_part_b.md"
        part_a.write_text("Dry-walkthrough verified = TRUE\n\nPart A content")
        part_b.write_text("> **PART B ONLY.**\n\nPart B content — no marker")

        result_a = _check_dry_walkthrough(f"/implement-worktree-no-merge {part_a}", str(tmp_path))
        result_b = _check_dry_walkthrough(f"/implement-worktree-no-merge {part_b}", str(tmp_path))
        assert result_a is None
        assert result_b is not None
        assert json.loads(result_b)["subtype"] == "gate_error"

    def test_gate_with_trailing_markdown_header_finds_plan(self, tmp_path, tool_ctx):
        """Trailing markdown headers must not corrupt the plan path."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        plan = plan_dir / "plan.md"
        plan.write_text(_get_config().implement_gate.marker + "\n\nrest")
        cmd = f"/implement-worktree-no-merge {plan}\n\n## Base Branch\nimpl-926"
        assert _check_dry_walkthrough(cmd, str(tmp_path)) is None

    def test_gate_with_extra_token_after_path(self, tmp_path, tool_ctx):
        """Space-separated token after path must not corrupt the plan path."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        plan = plan_dir / "plan.md"
        plan.write_text(_get_config().implement_gate.marker + "\n\nrest")
        cmd = f"/implement-worktree-no-merge {plan} impl-926"
        assert _check_dry_walkthrough(cmd, str(tmp_path)) is None

    def test_gate_multiline_no_marker_reports_dry_walk_error(self, tmp_path, tool_ctx):
        """With trailing headers and plan missing marker: dry-walk error, not file-not-found."""
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan title\n\nNo marker here")
        cmd = f"/implement-worktree-no-merge {plan}\n\n## Base Branch\nimpl-926"
        result = _check_dry_walkthrough(cmd, str(tmp_path))
        assert result is not None
        data = json.loads(result)
        message = data.get("result", "").lower()
        assert "not found" not in message, "Should fail on marker absence, not path lookup"
        assert "dry-walk" in message or "dry-walked" in message

    # --- Plan-origin (allowed_plan_dirs) validation tests ---

    def test_dry_walkthrough_gate_blocks_plan_at_wrong_directory(self, tool_ctx, tmp_path):
        """Gate blocks plan file not in an allowed origin directory."""
        wrong_dir = tmp_path / "dry-walkthrough"
        wrong_dir.mkdir()
        plan = wrong_dir / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert (
            "original location" in parsed["result"].lower()
            or "allowed" in parsed["result"].lower()
        )

    def test_dry_walkthrough_gate_allows_plan_at_make_plan_dir(self, tool_ctx, tmp_path):
        """Gate allows plan file in the make-plan directory."""
        plan_dir = tmp_path / "make-plan"
        plan_dir.mkdir()
        plan = plan_dir / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is None

    def test_dry_walkthrough_gate_allows_plan_at_rectify_dir(self, tool_ctx, tmp_path):
        """Gate allows plan file in the rectify directory."""
        plan_dir = tmp_path / "rectify"
        plan_dir.mkdir()
        plan = plan_dir / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is None

    def test_dry_walkthrough_gate_blocks_plan_at_arbitrary_dir(self, tool_ctx, tmp_path):
        """Gate blocks plan at arbitrary non-origin directory."""
        arb_dir = tmp_path / "audit-impl"
        arb_dir.mkdir()
        plan = arb_dir / "plan.md"
        plan.write_text("Dry-walkthrough verified = TRUE\n# Plan")
        result = _check_dry_walkthrough(f"/implement-worktree-no-merge {plan}", str(tmp_path))
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "gate_error"

    def test_implement_gate_config_default_allowed_dirs(self, tool_ctx):
        """Default allowed_plan_dirs includes make-plan and rectify."""
        from autoskillit.server._state import _get_config

        config = _get_config()
        assert "make-plan" in config.implement_gate.allowed_plan_dirs
        assert "rectify" in config.implement_gate.allowed_plan_dirs


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.anyio
    async def test_gate_still_fires_for_implement_skill(self, tool_ctx_kitchen_open, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(await run_skill(f"/implement-worktree {plan}", str(tmp_path)))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()
