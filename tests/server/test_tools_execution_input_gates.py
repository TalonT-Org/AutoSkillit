"""Tests for run_skill input validation gates and CWD checking."""

from __future__ import annotations

import json

import pytest

from autoskillit.server._guards import _check_dry_walkthrough
from autoskillit.server._state import _get_config
from autoskillit.server.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# Deterministic UUID for tests that need to predict the per-invocation marker.
_DETERMINISTIC_HEX = "a1b2c3d4e5f6a7b890123456"
_DETERMINISTIC_MARKER = f"%%ORDER_UP::{_DETERMINISTIC_HEX[:8]}%%"


class _FixedUUID:
    hex = _DETERMINISTIC_HEX


def _patch_uuid4(monkeypatch):
    """Monkeypatch uuid4 to return a deterministic value for marker prediction."""
    monkeypatch.setattr("uuid.uuid4", lambda: _FixedUUID())


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
        plan = tmp_path / "plan.md"
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
        plan = tmp_path / "task_plan_2026-01-01_part_a.md"
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
        part_a = tmp_path / "task_plan_part_a.md"
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
        plan = tmp_path / "plan.md"
        plan.write_text(_get_config().implement_gate.marker + "\n\nrest")
        cmd = f"/implement-worktree-no-merge {plan}\n\n## Base Branch\nimpl-926"
        assert _check_dry_walkthrough(cmd, str(tmp_path)) is None

    def test_gate_with_extra_token_after_path(self, tmp_path, tool_ctx):
        """Space-separated token after path must not corrupt the plan path."""
        plan = tmp_path / "plan.md"
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


class TestRunSkillPrefix:
    """run_skill passes prefixed command to subprocess."""

    @pytest.mark.anyio
    async def test_run_skill_prefixes_skill_command(self, tool_ctx):
        from tests.conftest import _make_result

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert cmd[prompt_idx].startswith("Use /investigate error")
        # cwd must propagate to the subprocess runner
        from pathlib import Path

        actual_cwd = tool_ctx.runner.call_args_list[0][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"

    @pytest.mark.anyio
    async def test_run_skill_rejects_prose_without_slash(self, tool_ctx):
        """FRICT-6-1: prose command without slash returns gate_error before reaching executor."""
        result = json.loads(await run_skill("Fix the authentication bug in main.py", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        # executor must NOT have been called
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_rejects_empty_skill_command(self, tool_ctx):
        """FRICT-6-1: empty string returns gate_error without hitting executor."""
        result = json.loads(await run_skill("", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_rejects_whitespace_only(self, tool_ctx):
        """FRICT-6-1: whitespace-only command returns gate_error (strip before check)."""
        result = json.loads(await run_skill("   ", "/tmp"))
        assert result["success"] is False
        assert result["is_error"] is True
        assert result["subtype"] == "gate_error"
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_format_error_includes_slash_examples(self, tool_ctx):
        """FRICT-6-1: error message for invalid format includes concrete slash-command examples."""
        result = json.loads(await run_skill("investigate this bug", "/tmp"))
        assert result["success"] is False
        assert "/autoskillit:" in result["result"]
        assert "/" in result["result"]

    @pytest.mark.anyio
    async def test_run_skill_includes_completion_directive(self, tool_ctx):
        from tests.conftest import _make_result

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert "%%ORDER_UP::" in cmd[prompt_idx]
        # cwd must propagate to the subprocess runner
        from pathlib import Path

        actual_cwd = tool_ctx.runner.call_args_list[0][1]
        assert actual_cwd == Path("/tmp"), f"Subprocess cwd mismatch: {actual_cwd} != /tmp"


class TestValidateSkillCommand:
    """Unit tests for _validate_skill_command helper."""

    def test_returns_none_for_slash_command(self, tool_ctx):
        from autoskillit.server._guards import _validate_skill_command

        assert _validate_skill_command("/autoskillit:investigate") is None

    def test_returns_none_for_bare_slash_command(self, tool_ctx):
        from autoskillit.server._guards import _validate_skill_command

        assert _validate_skill_command("/audit-arch") is None

    def test_returns_error_json_for_prose(self, tool_ctx):
        from autoskillit.server._guards import _validate_skill_command

        result = _validate_skill_command("Fix the bug")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["subtype"] == "gate_error"

    def test_returns_error_json_for_empty_string(self, tool_ctx):
        from autoskillit.server._guards import _validate_skill_command

        result = _validate_skill_command("")
        assert result is not None

    def test_strips_whitespace_before_check(self, tool_ctx):
        from autoskillit.server._guards import _validate_skill_command

        # Leading whitespace before slash → valid
        assert _validate_skill_command("  /autoskillit:investigate") is None
        # Leading whitespace before prose → invalid
        result = _validate_skill_command("  investigate bug")
        assert result is not None


class TestDryWalkthroughGateWithPrefix:
    """Dry-walkthrough gate still receives raw command before prefix is applied."""

    @pytest.mark.anyio
    async def test_gate_still_fires_for_implement_skill(self, tool_ctx, tmp_path):
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(await run_skill(f"/implement-worktree {plan}", str(tmp_path)))
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestRunSkillCwdValidation:
    """run_skill rejects non-empty relative cwd at the boundary."""

    @pytest.mark.anyio
    async def test_run_skill_rejects_relative_cwd(self, tool_ctx):
        """Non-empty relative cwd is rejected immediately with a clear diagnostic."""
        result = json.loads(
            await run_skill(
                "/autoskillit:retry-worktree plan.md ../worktrees/impl-fix",
                cwd="../worktrees/impl-fix-20260316",
            )
        )
        assert result["success"] is False
        assert "cwd must be an absolute path" in result["error"]
        assert "../worktrees/impl-fix-20260316" in result["error"]
        assert tool_ctx.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_accepts_empty_cwd(self, tool_ctx, monkeypatch):
        """Empty cwd is accepted (some skills have no specific cwd requirement)."""
        from tests.conftest import _make_result

        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd=""))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_run_skill_accepts_absolute_cwd(self, tool_ctx, monkeypatch):
        """Absolute cwd passes the boundary check and proceeds normally."""
        from tests.conftest import _make_result

        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd="/tmp"))
        assert result["success"] is True
