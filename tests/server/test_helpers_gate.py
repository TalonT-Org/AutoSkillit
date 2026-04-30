"""Contract tests: server helpers gate response schema."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestGateDisabledSchema:
    """Gate-disabled response schema matches the expected skill result keys."""

    EXPECTED_SKILL_KEYS = {
        "success",
        "result",
        "session_id",
        "subtype",
        "cli_subtype",
        "is_error",
        "exit_code",
        "kill_reason",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
        "write_path_warnings",
        "write_call_count",
    }

    def test_gate_disabled_schema(self, tool_ctx):
        """Gate-disabled response has standard keys."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._guards import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        response = json.loads(_require_enabled())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS
