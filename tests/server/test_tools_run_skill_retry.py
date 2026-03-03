"""Tests for run_skill_retry MCP tool handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.execution.headless import _session_log_dir
from autoskillit.server.tools_execution import run_skill, run_skill_retry
from tests.conftest import _make_result


class TestRunSkillRetryGate:
    """run_skill_retry applies dry-walkthrough gate to implement skills."""

    @pytest.mark.anyio
    async def test_run_skill_retry_gates_implement_no_merge(self, tool_ctx, tmp_path):
        """run_skill_retry gates /autoskillit:implement-worktree-no-merge."""
        plan = tmp_path / "plan.md"
        plan.write_text("# No marker plan")
        result = json.loads(
            await run_skill_retry(
                f"/autoskillit:implement-worktree-no-merge {plan}", str(tmp_path)
            )
        )
        assert result["success"] is False
        assert result["is_error"] is True
        assert "dry-walked" in result["result"].lower()


class TestRunSkillRetryPrefix:
    """run_skill_retry passes prefixed command to subprocess."""

    @pytest.mark.anyio
    async def test_run_skill_retry_prefixes_skill_command(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate error", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Use /investigate error")

    @pytest.mark.anyio
    async def test_run_skill_retry_no_prefix_for_plain_prompt(self, tool_ctx):
        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("Fix the bug in main.py", "/tmp")
        cmd = tool_ctx.runner.call_args_list[0][0]
        assert cmd[4].startswith("Fix the bug in main.py")


class TestRunSkillRetryPassesSessionLogDir:
    """run_skill_retry passes session_log_dir derived from cwd."""

    @pytest.mark.anyio
    async def test_run_skill_retry_passes_session_log_dir(self, tool_ctx):
        """run_skill_retry must pass session_log_dir just like run_skill."""
        cfg = AutomationConfig()
        cfg.safety.require_dry_walkthrough = False
        tool_ctx.config = cfg

        tool_ctx.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false,'
                ' "result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill_retry("/investigate foo", "/some/project")

        call_kwargs = tool_ctx.runner.call_args_list[-1][3]
        expected_dir = _session_log_dir("/some/project")
        assert call_kwargs["session_log_dir"] == expected_dir


class TestRunSkillRetryConsolidation:
    """run_skill_retry delegates to ctx.executor.run() with retry-specific config."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext for run_skill_retry consolidation tests."""
        self._tool_ctx = tool_ctx

    @pytest.mark.anyio
    async def test_run_skill_retry_passes_add_dir_to_subprocess(self):
        """add_dir is forwarded to ctx.executor.run()."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp", add_dir="/extra/dir")

        assert mock_run.call_args.kwargs.get("add_dir") == "/extra/dir"

    @pytest.mark.anyio
    async def test_run_skill_retry_uses_retry_timeout_not_skill_timeout(self):
        """run_skill_retry passes RunSkillRetryConfig.timeout (7200) not RunSkillConfig (3600)."""
        mock_result = SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        mock_run = AsyncMock(return_value=mock_result)
        self._tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill_retry("/investigate something", "/tmp")

        assert mock_run.call_args.kwargs.get("timeout") == 7200


class TestRunSkillRetrySessionOutcome:
    """run_skill_retry correctly classifies all Claude Code session outcomes."""

    @pytest.mark.anyio
    async def test_detects_max_turns_via_subtype(self, tool_ctx):
        """error_max_turns in JSON output -> needs_retry=True, retry_reason=RESUME."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "errors": ["Max turns reached"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.anyio
    async def test_detects_context_limit(self, tool_ctx):
        """'Prompt is too long' -> needs_retry=True, retry_reason='retry'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.anyio
    async def test_success_not_retriable(self, tool_ctx):
        """Normal success -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.anyio
    async def test_execution_error_not_retriable(self, tool_ctx):
        """error_during_execution -> needs_retry=False."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "session_id": "s1",
                "errors": ["crashed"],
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False

    @pytest.mark.anyio
    async def test_unparseable_stdout_not_retriable(self, tool_ctx):
        """Non-JSON stdout -> needs_retry=False."""
        tool_ctx.runner.push(_make_result(1, "crash dump", "segfault"))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["needs_retry"] is False


class TestRunSkillRetryAgentResult:
    """run_skill_retry result field contains actionable text."""

    @pytest.mark.anyio
    async def test_context_limit_result_is_actionable(self, tool_ctx):
        """When context is exhausted, result text must NOT say 'Prompt is too long'."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert "prompt is too long" not in result["result"].lower()
        assert result["needs_retry"] is True

    @pytest.mark.anyio
    async def test_normal_success_result_passes_through(self, tool_ctx):
        """Normal success result text is preserved."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill_retry("/retry-worktree plan.md", "/tmp"))
        assert result["result"] == "Done."


class TestRunSkillRetryFields:
    """run_skill includes needs_retry and retry_reason for parity."""

    @pytest.mark.anyio
    async def test_includes_needs_retry_false(self, tool_ctx):
        """run_skill response includes needs_retry=False on normal success."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is False
        assert result["retry_reason"] == RetryReason.NONE

    @pytest.mark.anyio
    async def test_includes_needs_retry_true_on_context_limit(self, tool_ctx):
        """run_skill response includes needs_retry=True when context is exhausted."""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Prompt is too long",
                "session_id": "s1",
            }
        )
        tool_ctx.runner.push(_make_result(1, stdout, ""))
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME
        assert "prompt is too long" not in result["result"].lower()
