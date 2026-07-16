"""Lossless output-spill contracts for execution MCP tools."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.server._response_budget import RESPONSE_SPILL_METADATA_KEY
from autoskillit.server.tools.tools_execution import run_cmd, run_python, run_skill
from tests.conftest import _make_result

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
async def test_run_cmd_resolves_absolute_cwd_only_for_spill_anchor(
    tool_ctx_kitchen_open, tmp_path
):
    project = tmp_path / "project"
    project.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(project, target_is_directory=True)
    stdout = "x" * 10_000
    subprocess = AsyncMock(return_value=(0, stdout, ""))

    with patch(
        "autoskillit.server.tools.tools_execution._run_subprocess",
        new=subprocess,
    ):
        data = json.loads(await run_cmd("bounded-command", str(project_link)))

    assert subprocess.await_args.kwargs["cwd"] == str(project_link)
    assert data["stdout_artifact_path"].startswith(
        str(project / ".autoskillit" / "temp" / "run_cmd")
    )


@pytest.mark.anyio
@pytest.mark.parametrize("cwd", ["", "relative-project"])
async def test_run_cmd_empty_or_relative_cwd_uses_injected_temp_dir(tool_ctx_kitchen_open, cwd):
    stdout = "x" * 10_000
    with patch(
        "autoskillit.server.tools.tools_execution._run_subprocess",
        new=AsyncMock(return_value=(0, stdout, "")),
    ):
        data = json.loads(await run_cmd("bounded-command", cwd))

    assert data["stdout_artifact_path"].startswith(str(tool_ctx_kitchen_open.temp_dir / "run_cmd"))


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

    assert data["success"] is True, data
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


@pytest.mark.anyio
async def test_decorated_run_skill_preserves_routing_scalars_and_full_artifact(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="deadbeef000000000000000000000000"),
    )
    tool_ctx_kitchen_open.output_pattern_resolver = None
    tool_ctx_kitchen_open.completion_required_resolver = None
    tool_ctx_kitchen_open.write_expected_resolver = None
    sentinels = ("HEAD-SENTINEL", "MIDDLE-SENTINEL", "TAIL-SENTINEL")
    producer_result = (
        sentinels[0]
        + ("x" * 30_000)
        + sentinels[1]
        + ("y" * 30_000)
        + sentinels[2]
        + "\n%%ORDER_UP::deadbeef%%"
    )
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": producer_result,
            "session_id": "spill-session",
        }
    )
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=stdout))

    raw = await run_skill("/investigate output budget", str(tmp_path))
    data = json.loads(raw)

    assert data["success"] is True, {
        key: data.get(key) for key in ("success", "subtype", "retry_reason", "is_error", "result")
    }
    assert data["is_error"] is False
    assert data["needs_retry"] is False
    assert data["session_id"] == "spill-session"
    assert data["exit_code"] == 0
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    authoritative = open(metadata["artifact_path"], encoding="utf-8").read()
    authoritative_data = json.loads(authoritative)
    for sentinel in sentinels:
        assert sentinel in authoritative_data["result"]


@pytest.mark.anyio
async def test_decorated_run_skill_small_result_is_byte_identical(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
):
    from autoskillit.server import _notify

    monkeypatch.setattr(
        uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="deadbeef000000000000000000000000"),
    )
    tool_ctx_kitchen_open.output_pattern_resolver = None
    tool_ctx_kitchen_open.completion_required_resolver = None
    tool_ctx_kitchen_open.write_expected_resolver = None
    original_enforce = _notify.enforce_response_budget
    observed: dict[str, str] = {}

    def capture_identity(result, **kwargs):
        observed["input"] = result
        shaped = original_enforce(result, **kwargs)
        observed["output"] = shaped
        return shaped

    monkeypatch.setattr(_notify, "enforce_response_budget", capture_identity)
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "small result\n%%ORDER_UP::deadbeef%%",
            "session_id": "small-session",
        }
    )
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=0, stdout=stdout))

    raw = await run_skill("/investigate output budget", str(tmp_path))

    assert raw == observed["input"] == observed["output"]


@pytest.mark.anyio
async def test_decorated_dispatch_preserves_routing_scalars_artifact_and_small_identity(
    tool_ctx_kitchen_open, monkeypatch
):
    from autoskillit.server.tools import tools_fleet_dispatch

    monkeypatch.setattr(tools_fleet_dispatch, "_require_fleet", lambda _name: None)
    monkeypatch.setattr(
        tools_fleet_dispatch,
        "find_caller_session_id",
        lambda **_kwargs: None,
    )
    large_envelope = json.dumps(
        {
            "success": True,
            "dispatch_status": "success",
            "dispatch_id": "dispatch-1",
            "dispatched_session_id": "child-1",
            "reason": "HEAD-SENTINEL" + ("x" * 120_000) + "TAIL-SENTINEL",
        }
    )
    large_outcome = SimpleNamespace(to_envelope=lambda: large_envelope)
    execute = AsyncMock(return_value=SimpleNamespace(outcome=large_outcome))
    monkeypatch.setattr(tools_fleet_dispatch, "execute_dispatch", execute)

    raw = await tools_fleet_dispatch.dispatch_food_truck(recipe="probe", task="spill")
    data = json.loads(raw)

    assert data["success"] is True
    assert data["dispatch_status"] == "success"
    assert data["dispatch_id"] == "dispatch-1"
    assert data["dispatched_session_id"] == "child-1"
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert open(metadata["artifact_path"], encoding="utf-8").read() == large_envelope

    small_envelope = json.dumps(
        {
            "success": True,
            "dispatch_status": "success",
            "dispatch_id": "dispatch-small",
        }
    )
    small_outcome = SimpleNamespace(to_envelope=lambda: small_envelope)
    execute.return_value = SimpleNamespace(outcome=small_outcome)

    assert (
        await tools_fleet_dispatch.dispatch_food_truck(recipe="probe", task="small")
        == small_envelope
    )
