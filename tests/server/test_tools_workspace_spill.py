"""Lossless output-spill contracts for the test_check MCP tool."""

from __future__ import annotations

import json

import pytest

from autoskillit.core import TestResult
from autoskillit.server.tools.tools_workspace import test_check
from tests.conftest import _make_result
from tests.fakes import InMemoryTestRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]
test_check.__test__ = False  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_failed_test_check_spills_exact_raw_streams(tool_ctx, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "Taskfile.yml").write_text("version: '3'\n")
    stdout = "F" * 10_000 + "\n= 1 failed ="
    stderr = "E" * 10_000
    tool_ctx.runner.push(_make_result(1, stdout, stderr))

    data = json.loads(await test_check(str(worktree)))

    artifact = data["raw_output_artifact_path"]
    assert artifact.startswith(str(worktree / ".autoskillit" / "temp" / "test_check"))
    assert json.loads(open(artifact, encoding="utf-8").read()) == {
        "stdout": stdout,
        "stderr": stderr,
    }
    assert data["passed"] is False


@pytest.mark.anyio
async def test_timed_out_test_check_spills_short_partial_streams(tool_ctx, tmp_path):
    """A deadline keeps partial streams and exposes its configured outer budget."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "Taskfile.yml").write_text("version: '3'\n")
    stdout = "still running"
    stderr = "last progress"
    timeout_seconds = 123.0
    tool_ctx.config.test_check.timeout = int(timeout_seconds)
    tool_ctx.config.output_budget.inline_max_chars = 20
    tool_ctx.config.output_budget.head_chars = 10
    tool_ctx.config.output_budget.tail_chars = 10
    tool_ctx.tester = InMemoryTestRunner(
        [
            TestResult(
                passed=False,
                stdout=stdout,
                stderr=stderr,
                outer_timeout_seconds=timeout_seconds,
            )
        ]
    )

    data = json.loads(await test_check(str(worktree)))

    assert data["passed"] is False
    assert data["timed_out"] is True
    assert data["outer_timeout_seconds"] == timeout_seconds
    artifact = data["raw_output_artifact_path"]
    assert json.loads(open(artifact, encoding="utf-8").read()) == {
        "stdout": stdout,
        "stderr": stderr,
    }
