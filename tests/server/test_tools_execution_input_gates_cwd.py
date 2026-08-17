"""CWD validation tests for `run_skill`: rejects non-empty relative cwd at the
boundary, accepts empty and absolute cwd paths.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_execution import run_skill
from tests.server._input_contract_test_helpers import _DETERMINISTIC_MARKER, _patch_uuid4

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunSkillCwdValidation:
    """run_skill rejects non-empty relative cwd at the boundary."""

    @pytest.mark.anyio
    async def test_run_skill_rejects_relative_cwd(self, tool_ctx_kitchen_open):
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
        assert tool_ctx_kitchen_open.runner.call_args_list == []

    @pytest.mark.anyio
    async def test_run_skill_accepts_empty_cwd(self, tool_ctx_kitchen_open, monkeypatch):
        """Empty cwd is accepted (some skills have no specific cwd requirement)."""
        from tests.conftest import _make_result

        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd=""))
        assert "cwd must be an absolute path" not in result.get("error", "")
        assert result.get("subtype") != "gate_error"

    @pytest.mark.anyio
    async def test_run_skill_accepts_absolute_cwd(self, tool_ctx_kitchen_open, monkeypatch):
        """Absolute cwd passes the boundary check and proceeds normally."""
        from tests.conftest import _make_result

        _patch_uuid4(monkeypatch)
        marker = _DETERMINISTIC_MARKER
        success_json = (
            '{"type": "result", "subtype": "success", "is_error": false,'
            f' "result": "done {marker}", "session_id": "s1"}}'
        )
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))  # clone guard snapshot
        tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=success_json))
        result = json.loads(await run_skill("/investigate foo", cwd="/tmp"))
        assert "cwd must be an absolute path" not in result.get("error", "")
        assert result.get("subtype") != "gate_error"
