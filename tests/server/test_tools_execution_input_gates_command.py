"""Tests for run_skill command-format gates (`_validate_skill_command`) and
prefix-time command normalization.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunSkillPrefix:
    """run_skill passes prefixed command to subprocess."""

    @pytest.mark.anyio
    async def test_run_skill_prefixes_skill_command(self, tool_ctx_kitchen_open):
        from pathlib import Path

        from tests.conftest import _make_result

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert cmd[prompt_idx].startswith("Use the /investigate skill error")
        actual_cwd = tool_ctx_kitchen_open.runner.call_args_list[-1][1]
        assert actual_cwd == Path("/tmp").resolve(), (
            f"Subprocess cwd mismatch: {actual_cwd} != {Path('/tmp').resolve()}"
        )

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
    async def test_run_skill_format_error_includes_slash_examples(self, tool_ctx_kitchen_open):
        """FRICT-6-1: error message for invalid format includes concrete slash-command examples."""
        result = json.loads(await run_skill("investigate this bug", "/tmp"))
        assert result["success"] is False
        assert "/autoskillit:" in result["result"]
        assert "/" in result["result"]

    @pytest.mark.anyio
    async def test_run_skill_includes_completion_directive(self, tool_ctx_kitchen_open):
        from pathlib import Path

        from tests.conftest import _make_result

        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(
            _make_result(
                0,
                '{"type": "result", "subtype": "success", "is_error": false, '
                '"result": "done", "session_id": "s1"}',
                "",
            )
        )
        await run_skill("/investigate error", "/tmp")
        cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
        prompt_idx = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
        assert "%%ORDER_UP::" in cmd[prompt_idx]

        actual_cwd = tool_ctx_kitchen_open.runner.call_args_list[-1][1]
        assert actual_cwd == Path("/tmp").resolve(), (
            f"Subprocess cwd mismatch: {actual_cwd} != {Path('/tmp').resolve()}"
        )


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
