"""Tests for record_pipeline_step MCP tool (init / gate-closed / status)."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step
from tests.server._pipeline_test_helpers import _grant_success_credit

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRecordPipelineStepInit:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        tool_ctx_kitchen_open.active_recipe_steps = {
            "review": {},
            "implement": {},
            "verify": {},
        }
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_init_creates_tracker_file(self):
        result = json.loads(
            await record_pipeline_step(
                pipeline_id="AB",
                op="init",
                dependencies={"implement": ["review"]},
            )
        )
        assert result["success"] is True
        assert result["step_count"] == 3
        assert result["dependency_count"] == 1

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        assert tracker_path.exists()
        tracker = json.loads(tracker_path.read_text())
        assert tracker["pipeline_id"] == "AB"
        assert tracker["kitchen_id"] == "test-kitchen"
        assert set(tracker["steps"].keys()) == {"review", "implement", "verify"}
        assert tracker["dependencies"] == {"implement": ["review"]}

    @pytest.mark.anyio
    async def test_init_marks_locked_steps_as_skipped(self):
        overlay_path = self.tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text(json.dumps({"locked_steps": {"AB": {"verify": False}}}))

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is True

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        tracker = json.loads(tracker_path.read_text())
        assert tracker["steps"]["verify"]["status"] == "skipped"
        assert tracker["steps"]["review"]["status"] == "pending"

    @pytest.mark.anyio
    async def test_init_rejects_corrupt_overlay_state(self):
        overlay_path = self.tmp_path / ".autoskillit" / "temp" / ".hook_config_overlay.json"
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text("{ malformed")

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))

        assert result["success"] is False
        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        assert not tracker_path.exists()

    @pytest.mark.anyio
    async def test_init_pipeline_id_fallback_to_env(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "XY")
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is True
        assert result["pipeline_id"] == "XY"

        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "XY.json"
        assert tracker_path.exists()

    @pytest.mark.anyio
    async def test_init_rejects_duplicate_pipeline_id(self):
        await record_pipeline_step(pipeline_id="AB", op="init")
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False
        assert "already been initialized" in result["error"]

    @pytest.mark.anyio
    async def test_init_empty_pipeline_id_no_env_returns_error(self, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_DISPATCH_ID", raising=False)
        result = json.loads(await record_pipeline_step(pipeline_id="", op="init"))
        assert result["success"] is False
        assert "pipeline_id is required" in result["error"]

    @pytest.mark.anyio
    async def test_terminal_completion_releases_manual_tracker_lease(self):
        self.ctx.active_recipe_steps = {"review": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        assert [key.owner_kind for key in self.ctx.tracker_leases] == ["manual"]
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is True
        assert completed["done"] == completed["total"] == 1
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_close_kitchen_releases_partial_pipeline_lease(self):
        from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

        self.ctx.active_recipe_steps = {"review": {}, "implement": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        key, lease = next(iter(self.ctx.tracker_leases.items()))
        tracker_path = key.target.path
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is True
        assert completed["done"] == 1
        assert completed["total"] == 2
        assert self.ctx.tracker_leases == {key: lease}
        assert not lease.closed

        _close_kitchen_handler()

        assert self.ctx.tracker_leases == {}
        assert lease.closed
        assert not tracker_path.exists()

    @pytest.mark.anyio
    async def test_kitchen_release_preserves_manual_tracker_lease(self):
        from autoskillit.server.tools.tools_kitchen import (
            _release_kitchen_tracker_authority,
            _retain_kitchen_tracker_authority,
        )
        from autoskillit.server.tools.tools_pipeline_tracker import _release_context_tracker

        _retain_kitchen_tracker_authority(self.ctx)
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        manual_key = next(key for key in self.ctx.tracker_leases if key.owner_kind == "manual")

        _release_kitchen_tracker_authority(self.ctx, unregister=False, retire=False)

        assert list(self.ctx.tracker_leases) == [manual_key]
        assert not self.ctx.tracker_leases[manual_key].closed
        _release_context_tracker(self.ctx, manual_key)

    def test_kitchen_release_does_external_work_outside_lease_lock(self, monkeypatch):
        from autoskillit.server.tools import tools_kitchen

        tools_kitchen._retain_kitchen_tracker_authority(self.ctx)
        lock_states = []

        def record_lock_state(_value):
            lock_states.append(getattr(self.ctx.tracker_leases_lock, "_is_owned")())

        monkeypatch.setattr(tools_kitchen, "unregister_active_kitchen", record_lock_state)
        monkeypatch.setattr(tools_kitchen, "try_retire_tracker", record_lock_state)

        tools_kitchen._release_kitchen_tracker_authority(
            self.ctx,
            unregister=True,
            retire=True,
        )

        assert lock_states == [False, False]

    @pytest.mark.anyio
    async def test_completion_exception_releases_manual_tracker_lease(self, monkeypatch):
        from autoskillit.server.tools import tools_pipeline_tracker

        self.ctx.active_recipe_steps = {"review": {}}
        initialized = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert initialized["success"] is True
        _grant_success_credit(self.ctx, self.tmp_path, "review", pipeline_id="AB")

        def raise_from_marker(*_args, **_kwargs):
            raise OSError("marker failed")

        monkeypatch.setattr(tools_pipeline_tracker, "mark_step_complete", raise_from_marker)
        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is False
        assert completed["is_error"] is True
        assert completed["stage"] == "pipeline_marker"
        assert completed["error"] == "record_pipeline_step: pipeline marker failed."
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_completion_identity_read_exception_releases_manual_lease(self, monkeypatch):
        from autoskillit.server.tools import tools_pipeline_tracker

        lease_observed = False

        def fail_read(*_args, **_kwargs):
            nonlocal lease_observed
            assert any(not lease.closed for lease in self.ctx.tracker_leases.values())
            lease_observed = True
            raise OSError("identity read failed")

        monkeypatch.setattr(tools_pipeline_tracker, "read_tracker_authority", fail_read)
        completed = json.loads(
            await record_pipeline_step(pipeline_id="AB", op="complete", step_name="review")
        )

        assert completed["success"] is False
        assert lease_observed
        assert self.ctx.tracker_leases == {}

    @pytest.mark.anyio
    async def test_manual_init_preserves_existing_corrupt_bytes_and_releases_lease(self):
        tracker_path = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / "AB.json"
        tracker_path.parent.mkdir(parents=True)
        tracker_path.write_bytes(b"{not-json")

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))

        assert result["success"] is False
        assert tracker_path.read_bytes() == b"{not-json"
        assert self.ctx.tracker_leases == {}

    @pytest.mark.parametrize(
        ("op", "handler"), [("init", "_handle_init"), ("status", "_handle_status")]
    )
    @pytest.mark.anyio
    async def test_handler_exception_releases_new_manual_lease(self, monkeypatch, op, handler):
        from autoskillit.server.tools import tools_pipeline_tracker

        lease_observed = False

        def fail(*_args, **_kwargs):
            nonlocal lease_observed
            assert any(not lease.closed for lease in self.ctx.tracker_leases.values())
            lease_observed = True
            raise OSError("handler failed")

        monkeypatch.setattr(tools_pipeline_tracker, handler, fail)
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op=op))

        assert result["success"] is False
        assert lease_observed
        assert self.ctx.tracker_leases == {}


class TestRecordPipelineStepGateClosed:
    @pytest.mark.anyio
    async def test_init_rejects_without_kitchen_open(self, tool_ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx.project_dir = tmp_path
        tool_ctx.active_recipe_steps = {"step_a": {}}
        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="init"))
        assert result["success"] is False


class TestRecordPipelineStepStatus:
    @pytest.fixture(autouse=True)
    def _setup(self, tool_ctx_kitchen_open, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.kitchen_id = "test-kitchen"
        self.ctx = tool_ctx_kitchen_open
        self.tmp_path = tmp_path

    @pytest.mark.anyio
    async def test_status_returns_current_state(self):
        tracker_dir = self.tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
        tracker_dir.mkdir(parents=True)
        tracker_dir.joinpath("AB.json").write_text(
            json.dumps(
                {
                    "pipeline_id": "AB",
                    "kitchen_id": "test-kitchen",
                    "initialized_at": "2026-05-31T01:00:00Z",
                    "steps": {
                        "review": {"status": "complete", "completed_at": "2026-05-31T01:05:00Z"},
                        "implement": {"status": "pending"},
                        "verify": {"status": "skipped"},
                    },
                    "dependencies": {"implement": ["review"]},
                }
            )
        )

        result = json.loads(await record_pipeline_step(pipeline_id="AB", op="status"))
        assert result["success"] is True
        assert result["complete"] == 1
        assert result["pending"] == 1
        assert result["skipped"] == 1
        assert result["total"] == 3
