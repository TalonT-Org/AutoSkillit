"""Tests for the reset_dispatch MCP tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.fleet import DispatchRecord, write_initial_state
from autoskillit.fleet.state_types import DispatchStatus

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _setup_state(
    tmp_path: Path,
    dispatch_name: str = "impl-issue-42",
    dispatch_id: str = "d-abc123",
    status: DispatchStatus = DispatchStatus.FAILURE,
    sidecar_path: str | None = None,
) -> Path:
    state_path = tmp_path / "dispatches" / "campaign.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    record = DispatchRecord(
        name=dispatch_name,
        dispatch_id=dispatch_id,
        status=status,
        sidecar_path=sidecar_path,
    )
    write_initial_state(
        state_path,
        campaign_id="cid-1",
        campaign_name="test",
        manifest_path="",
        dispatches=[record],
    )
    return state_path


def _write_sidecar(path: Path, pr_url: str | None = None) -> None:
    entry = {
        "issue_url": "https://github.com/owner/repo/issues/1",
        "status": "completed",
        "pr_url": pr_url,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry) + "\n")


def _setup_tool(tool_ctx, monkeypatch, state_path: Path) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_reset._require_enabled",
        lambda: None,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_reset._require_fleet",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "autoskillit.fleet._label_cleanup.discover_campaign_state_files",
        lambda _project_dir: [state_path],
    )
    from autoskillit.core import SubprocessResult

    tool_ctx.runner = AsyncMock(return_value=SubprocessResult(returncode=0, stdout="", stderr=""))
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})


class TestResetDispatchHappyPath:
    @pytest.mark.anyio
    async def test_reset_dispatch_happy_path_queued(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url="https://github.com/owner/repo/pull/1")
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123", reset_to="queued")
        result = json.loads(raw)

        assert result["success"] is True
        assert result["labels_reset"] is True
        assert result["worktree_removed"] is True
        assert result["sidecar_removed"] is True
        assert result["local_branch_deleted"] is True
        assert result["remote_branch_deleted"] is True
        assert "https://github.com/owner/repo/pull/1" in result["prs_closed"]
        assert result["reset_to"] == "queued"

    @pytest.mark.anyio
    async def test_reset_dispatch_happy_path_fail(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url="https://github.com/owner/repo/pull/2")
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123", reset_to="fail")
        result = json.loads(raw)

        assert result["success"] is True
        assert result["labels_reset"] is True
        assert result["state_updated"] is True
        assert result["reset_to"] == "fail"


class TestResetDispatchErrors:
    @pytest.mark.anyio
    async def test_reset_dispatch_not_found(self, build_ctx_open, tmp_path, monkeypatch) -> None:
        state_path = _setup_state(tmp_path)
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="nonexistent")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_reset_not_found"

    @pytest.mark.anyio
    async def test_reset_dispatch_invalid_reset_to(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        state_path = _setup_state(tmp_path)
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123", reset_to="invalid")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_reset_invalid_target"

    @pytest.mark.anyio
    async def test_reset_dispatch_non_fleet_rejected(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        state_path = _setup_state(tmp_path)
        build_ctx_open()
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: json.dumps({"success": False, "error": "not_fleet"}),
        )
        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "not_fleet"

    @pytest.mark.anyio
    async def test_reset_dispatch_gate_closed(self, build_ctx_open, tmp_path, monkeypatch) -> None:
        state_path = _setup_state(tmp_path)
        build_ctx_open()
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: json.dumps({"success": False, "error": "gate_closed"}),
        )
        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "gate_closed"

    @pytest.mark.anyio
    async def test_reset_dispatch_running_rejected(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        state_path = _setup_state(tmp_path, status=DispatchStatus.RUNNING)
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "fleet_reset_still_running"


class TestResetDispatchEdgeCases:
    @pytest.mark.anyio
    async def test_reset_dispatch_partial_failure(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url=None)
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: None,
        )
        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        from autoskillit.core import SubprocessResult

        async def _runner(cmd, **_kwargs):
            if "branch" in cmd and "-D" in cmd:
                return SubprocessResult(returncode=1, stdout="", stderr="branch missing")
            return SubprocessResult(returncode=0, stdout="", stderr="")

        tool_ctx.runner = _runner
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["local_branch_deleted"] is False
        assert result["labels_reset"] is True
        assert any("branch" in e for e in result["errors"])

    @pytest.mark.anyio
    async def test_reset_dispatch_no_sidecar(self, build_ctx_open, tmp_path, monkeypatch) -> None:
        state_path = _setup_state(tmp_path, sidecar_path=None)
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["labels_reset"] is True

    @pytest.mark.anyio
    async def test_reset_dispatch_pr_fallback_search(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url=None)
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: None,
        )
        monkeypatch.setattr(
            "autoskillit.fleet._label_cleanup.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        from autoskillit.core import SubprocessResult

        async def _runner(cmd, **_kwargs):
            if "list" in cmd and "pr" in cmd:
                return SubprocessResult(
                    returncode=0,
                    stdout=json.dumps([{"url": "https://github.com/owner/repo/pull/99"}]),
                    stderr="",
                )
            return SubprocessResult(returncode=0, stdout="", stderr="")

        tool_ctx.runner = _runner
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True
        assert "https://github.com/owner/repo/pull/99" in result["prs_closed"]
