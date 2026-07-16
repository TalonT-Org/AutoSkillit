"""Lossless output-spill contracts for execution MCP tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.server._response_budget import RESPONSE_SPILL_METADATA_KEY
from autoskillit.server.tools.tools_execution import run_cmd, run_python

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_cmd_spills_large_stdout_under_calling_project(tool_ctx_kitchen_open, tmp_path):
    stdout = "head-sentinel\n" + ("x" * 10_000) + "\ntail-sentinel"
    with patch(
        "autoskillit.server.tools.tools_execution._run_subprocess",
        new=AsyncMock(return_value=(0, stdout, "")),
    ):
        data = json.loads(await run_cmd("bounded-command", str(tmp_path)))

    artifact = data["stdout_artifact_path"]
    assert artifact.startswith(str(tmp_path / ".autoskillit" / "temp" / "run_cmd"))
    assert open(artifact, encoding="utf-8").read() == stdout
    assert "head-sentinel" in data["stdout"]
    assert "tail-sentinel" in data["stdout"]
    assert data["success"] is True
    assert data["exit_code"] == 0


@pytest.mark.anyio
async def test_run_cmd_small_output_shape_is_unchanged(tool_ctx_kitchen_open, tmp_path):
    with patch(
        "autoskillit.server.tools.tools_execution._run_subprocess",
        new=AsyncMock(return_value=(0, "small", "")),
    ):
        raw = await run_cmd("bounded-command", str(tmp_path))

    assert raw == json.dumps({"success": True, "exit_code": 0, "stdout": "small", "stderr": ""})


@pytest.mark.anyio
async def test_run_python_preserves_routing_keys_and_full_json(tool_ctx_kitchen_open, tmp_path):
    result = {"success": True, "verdict": "GO", "payload": "x" * 10_000}
    with patch(
        "autoskillit.server.tools.tools_execution._import_and_call",
        new=AsyncMock(return_value=result),
    ):
        data = json.loads(await run_python("package.module.callable", work_dir=str(tmp_path)))

    assert data["success"] is True
    assert data["verdict"] == "GO"
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert json.loads(open(metadata["artifact_path"], encoding="utf-8").read()) == result


@pytest.mark.anyio
async def test_run_python_small_dict_is_byte_identical(tool_ctx_kitchen_open, tmp_path):
    result = {"success": True, "verdict": "GO"}
    with patch(
        "autoskillit.server.tools.tools_execution._import_and_call",
        new=AsyncMock(return_value=result),
    ):
        raw = await run_python("package.module.callable", work_dir=str(tmp_path))

    assert raw == json.dumps(result)
