"""Contract tests: MCP tool response fields use correct enum types."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_session_result(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    termination_reason: TerminationReason = TerminationReason.NATURAL_EXIT,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
) -> SubprocessResult:
    """Create a SubprocessResult for mocking run_managed_async."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=termination_reason,
        pid=12345,
        channel_confirmation=channel_confirmation,
    )


class TestResponseFieldsAreTypeSafe:
    """Every discriminator field in MCP tool responses uses enum values."""

    @pytest.mark.anyio
    async def test_retry_reason_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": False,
                "session_id": "s1",
                "num_turns": 200,
                "errors": [],
            }
        )
        tool_ctx.runner.push(_make_session_result(1, stdout, ""))
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}

    @pytest.mark.anyio
    async def test_retry_reason_none_is_enum_value(self, tool_ctx):
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done.",
                "session_id": "s1",
                "num_turns": 50,
            }
        )
        tool_ctx.runner.push(_make_session_result(0, stdout, ""))
        result = json.loads(await run_skill("/retry-worktree plan.md", "/tmp"))
        assert result["retry_reason"] in {e.value for e in RetryReason}
