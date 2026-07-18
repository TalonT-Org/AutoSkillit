"""Lossless output-spill contracts for the test_check MCP tool."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_workspace import test_check
from tests.conftest import _make_result

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
