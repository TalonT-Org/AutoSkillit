"""Focused outcome-accounting tests for the test_check MCP tool."""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

import autoskillit.server.tools.tools_workspace as tools_workspace
from autoskillit.core import WorkspaceOutcomeKind
from autoskillit.server.tools.tools_workspace import test_check
from tests.conftest import _make_result
from tests.server._outcome_ledger_fakes import _FailingLedger, _RecordingLedger
from tests.server._recipe_segment_test_helpers import (
    assert_recovery_recipe_segment,
    install_prepared_recipe_segment,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _worktree(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "Taskfile.yml").write_text("version: '3'\n")
    return worktree


@pytest.mark.anyio
async def test_wire_passed_value_records_one_realpath_utc_outcome(tool_ctx, tmp_path):
    worktree = _worktree(tmp_path)
    ledger = _RecordingLedger()
    tool_ctx.workspace_outcome_ledger = ledger
    tool_ctx.runner.push(_make_result(0, "= 1 passed =\n", ""))

    result = json.loads(await test_check(str(worktree / ".")))

    assert result["passed"] is True
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.workspace == os.path.realpath(worktree)
    assert record.kind is WorkspaceOutcomeKind.TEST_RUN
    assert record.succeeded is True
    assert record.commit_sha is None
    assert record.failure_class is None
    assert record.timed_out is False
    assert record.infrastructure_missing is False
    assert datetime.fromisoformat(record.recorded_at).utcoffset() is not None


@pytest.mark.anyio
async def test_accounting_uses_wire_passed_instead_of_internal_effective_value(
    tool_ctx, tmp_path, monkeypatch
):
    worktree = _worktree(tmp_path)
    ledger = _RecordingLedger()
    tool_ctx.workspace_outcome_ledger = ledger
    tool_ctx.runner.push(_make_result(0, "= 1 passed =\n", ""))
    monkeypatch.setattr(
        tools_workspace,
        "_build_test_check_response",
        lambda *args, **kwargs: {
            "passed": False,
            "timed_out": False,
            "stdout": "wire says failed",
            "stderr": "",
        },
    )

    result = json.loads(await test_check(str(worktree)))

    assert result["passed"] is False
    assert len(ledger.records) == 1
    assert ledger.records[0].succeeded is False


@pytest.mark.anyio
async def test_infrastructure_failure_is_recorded_once(tool_ctx, tmp_path):
    missing = tmp_path / "missing-worktree"
    ledger = _RecordingLedger()
    tool_ctx.workspace_outcome_ledger = ledger

    result = json.loads(await test_check(str(missing)))

    assert result["passed"] is False
    assert result["infrastructure_missing"] is True
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.succeeded is False
    assert record.infrastructure_missing is True


@pytest.mark.anyio
async def test_ledger_failure_returns_recovery_envelope_without_recursive_retry(
    tool_ctx, tmp_path, monkeypatch
):
    worktree = _worktree(tmp_path)
    ledger = _FailingLedger()
    tool_ctx.workspace_outcome_ledger = ledger
    tool_ctx.runner.push(_make_result(0, "= 1 passed =\n", ""))
    install_prepared_recipe_segment(
        monkeypatch,
        tools_workspace,
        step_name="gate",
    )

    result = json.loads(await test_check(str(worktree), step_name="gate"))

    assert result["passed"] is False
    assert "outcome recording failed" in result["error"]
    assert_recovery_recipe_segment(result, step_name="gate")
    assert ledger.calls == 1
    assert len(tool_ctx.runner.call_args_list) == 1
    assert any(entry["step_name"] == "gate" for entry in tool_ctx.timing_log.get_report())
