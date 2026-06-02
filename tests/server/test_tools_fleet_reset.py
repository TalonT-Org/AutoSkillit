"""Tests for the reset_dispatch MCP tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import TerminationReason
from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

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


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from autoskillit.core import SubprocessResult

    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


def _setup_tool(tool_ctx, monkeypatch, state_path: Path) -> None:
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", tool_ctx)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_reset._require_enabled",
        lambda: None,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_reset._require_fleet",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_fleet_reset.discover_campaign_state_files",
        lambda _project_dir: [state_path],
    )

    tool_ctx.runner = AsyncMock(return_value=_make_subprocess_result())
    tool_ctx.github_client = AsyncMock()
    tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})


def _write_resume_gate_state(tmp_path: Path, resume_attempted: dict[str, bool]) -> Path:
    state_path = tmp_path / ".autoskillit" / "temp" / "resume_gate_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"resume_attempted": resume_attempted}))
    return state_path


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

    @pytest.mark.anyio
    async def test_reset_dispatch_accepts_force_true(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url="https://github.com/owner/repo/pull/1")
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123", force=True)
        result = json.loads(raw)

        assert result["success"] is True
        assert result["reset_to"] == "queued"


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
        from autoskillit.server import _state

        state_path = _setup_state(tmp_path)
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(_state, "_ctx", tool_ctx)
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: json.dumps({"success": False, "error": "not_fleet"}),
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is False
        assert result["error"] == "not_fleet"

    @pytest.mark.anyio
    async def test_reset_dispatch_gate_closed(self, build_ctx_open, tmp_path, monkeypatch) -> None:
        from autoskillit.server import _state

        state_path = _setup_state(tmp_path)
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(_state, "_ctx", tool_ctx)
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: json.dumps({"success": False, "error": "gate_closed"}),
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset.discover_campaign_state_files",
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
        from autoskillit.server import _state

        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url=None)
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(_state, "_ctx", tool_ctx)
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        async def _runner(cmd, **_kwargs):
            if "branch" in cmd and "-D" in cmd:
                return _make_subprocess_result(returncode=1, stderr="branch missing")
            return _make_subprocess_result()

        tool_ctx.runner = _runner
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["local_branch_deleted"] is False
        assert result["labels_reset"] is True

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
    async def test_reset_dispatch_missing_sidecar_fail_does_not_mark_labels_cleaned(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "gone_sidecar.jsonl"
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123", reset_to="fail")
        result = json.loads(raw)
        assert result["success"] is True
        assert result["labels_reset"] is False
        assert len(result["errors"]) > 0

        from autoskillit.fleet.state import read_state

        state = read_state(state_path)
        assert state is not None
        d = next(d for d in state.dispatches if d.name == "impl-issue-42")
        assert d.labels_cleaned is False

    @pytest.mark.anyio
    async def test_reset_dispatch_pr_fallback_search(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.server import _state

        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url=None)
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        monkeypatch.setattr(_state, "_ctx", tool_ctx)
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_enabled",
            lambda: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset._require_fleet",
            lambda _name: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_fleet_reset.discover_campaign_state_files",
            lambda _project_dir: [state_path],
        )

        async def _runner(cmd, **_kwargs):
            if "list" in cmd and "pr" in cmd:
                return _make_subprocess_result(
                    stdout=json.dumps([{"url": "https://github.com/owner/repo/pull/99"}]),
                )
            return _make_subprocess_result()

        tool_ctx.runner = _runner
        tool_ctx.github_client = AsyncMock()
        tool_ctx.github_client.swap_labels = AsyncMock(return_value={"success": True})

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True
        assert "https://github.com/owner/repo/pull/99" in result["prs_closed"]

    @pytest.mark.anyio
    async def test_reset_dispatch_cleans_up_resume_gate_state(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url="https://github.com/owner/repo/pull/1")
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        resume_state_path = _write_resume_gate_state(
            tmp_path, {"d-abc123": True, "other-id": True}
        )

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="d-abc123")
        result = json.loads(raw)
        assert result["success"] is True

        state = json.loads(resume_state_path.read_text())
        assert "d-abc123" not in state["resume_attempted"]
        assert state["resume_attempted"]["other-id"] is True

    @pytest.mark.anyio
    async def test_reset_dispatch_cleans_up_both_name_and_uuid(
        self, build_ctx_open, tmp_path, monkeypatch
    ) -> None:
        sidecar = tmp_path / "sidecar.jsonl"
        _write_sidecar(sidecar, pr_url="https://github.com/owner/repo/pull/1")
        state_path = _setup_state(tmp_path, sidecar_path=str(sidecar))
        tool_ctx = build_ctx_open()
        _setup_tool(tool_ctx, monkeypatch, state_path)

        resume_state_path = _write_resume_gate_state(
            tmp_path, {"d-abc123": True, "impl-issue-42": True}
        )

        from autoskillit.server.tools.tools_fleet_reset import reset_dispatch

        raw = await reset_dispatch(dispatch_id="impl-issue-42")
        result = json.loads(raw)
        assert result["success"] is True

        state = json.loads(resume_state_path.read_text())
        assert "impl-issue-42" not in state["resume_attempted"]
        assert "d-abc123" not in state["resume_attempted"]
