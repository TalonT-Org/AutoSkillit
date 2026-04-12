"""Tests verifying run_skill_retry was removed and run_skill handles all sessions."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason, TerminationReason
from autoskillit.server.tools_execution import run_skill
from tests.conftest import _make_result

# Deterministic UUID for tests that need to predict the per-invocation marker.
_DETERMINISTIC_HEX = "a1b2c3d4e5f6a7b890123456"
_DETERMINISTIC_MARKER = f"%%ORDER_UP::{_DETERMINISTIC_HEX[:8]}%%"


class _FixedUUID:
    hex = _DETERMINISTIC_HEX


def _patch_uuid4(monkeypatch):
    """Monkeypatch uuid4 to return a deterministic value for marker prediction."""
    monkeypatch.setattr("uuid.uuid4", lambda: _FixedUUID())


class TestRunSkillRetryRemoved:
    """run_skill_retry must not exist as a separate MCP tool."""

    def test_run_skill_retry_not_in_tools_execution(self):
        """run_skill_retry is not importable from tools_execution."""
        import autoskillit.server.tools_execution as module

        assert not hasattr(module, "run_skill_retry"), (
            "run_skill_retry still exists in tools_execution — it should be removed"
        )

    def test_run_skill_retry_not_in_all(self):
        """run_skill_retry is not in tools_execution.__all__ (if defined)."""
        import autoskillit.server.tools_execution as module

        # tools_execution does not define __all__; verify via direct attribute check
        assert not hasattr(module, "run_skill_retry"), (
            "run_skill_retry should not be a public attribute of tools_execution"
        )


class TestRunSkillSessionOutcome:
    """run_skill correctly classifies all Claude Code session outcomes."""

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
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.anyio
    async def test_detects_context_limit(self, tool_ctx):
        """'Prompt is too long' -> needs_retry=True, retry_reason='resume'."""
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
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.RESUME

    @pytest.mark.anyio
    async def test_success_not_retriable(self, tool_ctx, monkeypatch):
        """Normal success -> needs_retry=False."""
        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        assistant = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Edit", "id": "tu1"}]},
            }
        )
        result_record = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Done. {marker}",
                "session_id": "s1",
            }
        )
        stdout = assistant + "\n" + result_record
        tool_ctx.runner.push(_make_result(0, stdout, ""))
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
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
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
        assert result["needs_retry"] is False

    @pytest.mark.anyio
    async def test_unparseable_stdout_not_retriable(self, tool_ctx):
        """Non-JSON stdout -> needs_retry=False."""
        tool_ctx.runner.push(_make_result(1, "crash dump", "segfault"))
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
        assert result["needs_retry"] is False


class TestRunSkillAgentResult:
    """run_skill result field contains actionable text."""

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
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
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
        result = json.loads(await run_skill("/investigate plan.md", "/tmp"))
        assert result["result"] == "Done."


class TestRunSkillPassesAddDir:
    """run_skill passes ValidatedAddDir instances to executor."""

    @pytest.mark.anyio
    async def test_run_skill_passes_validated_add_dirs_to_executor(self, tool_ctx):
        """add_dirs forwarded to ctx.executor.run() are ValidatedAddDir instances."""
        from unittest.mock import AsyncMock

        from autoskillit.core import ValidatedAddDir

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
        tool_ctx.executor = type("MockExec", (), {"run": mock_run})()
        await run_skill("/investigate something", "/tmp")

        add_dirs = mock_run.call_args.kwargs.get("add_dirs", ())
        assert len(add_dirs) >= 1
        assert all(isinstance(d, ValidatedAddDir) for d in add_dirs)


class TestRunSkillFields:
    """run_skill includes needs_retry and retry_reason."""

    @pytest.mark.anyio
    async def test_includes_needs_retry_false(self, tool_ctx, monkeypatch):
        """run_skill response includes needs_retry=False on normal success."""
        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Done. {marker}",
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

    @pytest.mark.anyio
    async def test_includes_empty_output_reason_on_empty_natural_exit_session(self, tool_ctx):
        """run_skill returns retry_reason=empty_output for empty natural-exit sessions.

        NATURAL_EXIT + rc=0 + empty_output subtype: the session exited cleanly but
        produced no output. No partial progress exists on disk. The retry_reason
        must be EMPTY_OUTPUT so the orchestrator falls through to on_failure rather
        than incorrectly routing to on_context_limit.
        """
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "empty_output",
                "is_error": True,
                "result": "",
                "session_id": "",
            }
        )
        tool_ctx.runner.push(
            _make_result(0, stdout, "", termination_reason=TerminationReason.NATURAL_EXIT)
        )
        result = json.loads(await run_skill("/investigate error", "/tmp"))
        assert result["needs_retry"] is True
        assert result["retry_reason"] == RetryReason.EMPTY_OUTPUT
        # Verify the JSON payload contains the exact string "empty_output"
        assert '"retry_reason": "empty_output"' in json.dumps(result)
