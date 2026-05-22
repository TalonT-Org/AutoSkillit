"""End-to-end tests verifying run_headless_core routes command construction through ctx.backend."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _mock_backend(
    *,
    pty_required: bool = True,
    channel_b_capable: bool = True,
) -> Mock:
    caps = replace(
        CLAUDE_CODE_CAPABILITIES,
        pty_required=pty_required,
        channel_b_capable=channel_b_capable,
    )
    backend = Mock()
    backend.name = "claude-code"
    backend.capabilities = caps
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "test-prompt"),
        env={"AUTOSKILLIT_HEADLESS": "1"},
    )
    backend.build_resume_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "emit marker", "--resume", "test-session"),
        env={},
    )
    backend.write_tool_names.return_value = frozenset({"Write", "Edit"})
    backend.result_parser.return_value = Mock()
    return backend


@pytest.mark.anyio
async def test_run_headless_core_uses_ctx_backend_for_command_construction(minimal_ctx):
    backend = _mock_backend()
    minimal_ctx.backend = backend

    mock_runner = AsyncMock()
    mock_result = Mock()
    mock_result.stdout = ""
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_result.pid = 12345
    mock_result.termination = Mock()
    mock_result.termination.value = "NATURAL_EXIT"
    mock_result.proc_snapshots = None
    mock_result.start_ts = "2026-01-01T00:00:00+00:00"
    mock_result.end_ts = "2026-01-01T00:01:00+00:00"
    mock_result.elapsed_seconds = 60.0
    mock_result.tracked_comm = None
    mock_result.orphaned_tool_result = False
    mock_runner.return_value = mock_result
    minimal_ctx.runner = mock_runner

    with patch("autoskillit.execution.headless._build_skill_result") as mock_build_result:
        mock_skill_result = Mock()
        mock_skill_result.success = True
        mock_skill_result.needs_retry = False
        mock_skill_result.session_id = "test-session"
        mock_skill_result.worktree_path = None
        mock_skill_result.subtype = "success"
        mock_skill_result.cli_subtype = None
        mock_skill_result.exit_code = 0
        mock_skill_result.kill_reason = Mock()
        mock_skill_result.kill_reason.value = "NATURAL_EXIT"
        mock_skill_result.token_usage = None
        mock_skill_result.evidence = Mock()
        mock_skill_result.evidence.write_call_count = 0
        mock_skill_result.evidence.fs_writes_detected = False
        mock_skill_result.evidence.git_writes_detected = False
        mock_skill_result.write_path_warnings = []
        mock_skill_result.retry_reason = None
        mock_skill_result.last_stop_reason = None
        mock_skill_result.provider = None
        mock_build_result.return_value = mock_skill_result

        from autoskillit.execution.headless import run_headless_core

        await run_headless_core(
            "/autoskillit:test-skill",
            "/tmp/test-cwd",
            minimal_ctx,
            completion_marker="%%DONE%%",
        )

    backend.build_skill_session_cmd.assert_called_once()
