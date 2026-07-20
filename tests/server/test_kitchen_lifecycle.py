import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types._type_plugin_source import DirectInstall
from autoskillit.hooks import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.server import _state
from autoskillit.server._factory import make_context
from autoskillit.server.tools.tools_kitchen import (
    _close_kitchen_handler,
    _open_kitchen_handler,
    prune_stale_kitchen_state,
)
from tests.server._helpers import _write_registry

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


async def test_kitchen_open_close_lifecycle(monkeypatch, tmp_path):
    """Gate: disabled→enabled→disabled; hook_config written then removed; task cancelled."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    hook_config_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        # initial state
        assert ctx.gate.enabled is False

        # open kitchen
        result = await _open_kitchen_handler()
        assert result is None  # no failure envelope

        assert ctx.gate.enabled is True
        assert hook_config_path.exists()
        data = json.loads(hook_config_path.read_text())
        assert "quota_guard" in data
        assert "kitchen_id" in data
        task = ctx.quota_refresh_task
        assert task is not None

        # close kitchen
        _close_kitchen_handler()

        assert ctx.gate.enabled is False
        assert not hook_config_path.exists()
        assert ctx.quota_refresh_task is None

    # drain the cancelled task from the event loop
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_open_kitchen_runs_reaper(monkeypatch, tmp_path):
    """Test 1C: _open_kitchen_handler must call reap_stale_dispatches_async.

    Interactive sessions need the reaper at kitchen-open time since they
    never go through _fleet_auto_gate_boot or _food_truck_auto_gate_boot.
    """
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    fake_state_path = tmp_path / "dispatches" / "campaign.json"

    reaper_called = AsyncMock()

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.discover_campaign_state_files",
        lambda _project_dir: [fake_state_path],
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.reap_stale_dispatches_async",
        reaper_called,
    )

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
    ):
        result = await _open_kitchen_handler()
        assert result is None
        reaper_called.assert_awaited_once()
        call_args = reaper_called.await_args
        assert call_args is not None
        assert list(call_args.args[0]) == [fake_state_path]


async def test_close_kitchen_removes_tracker_dir(monkeypatch, tmp_path):
    """Tracker directory is removed by close_kitchen handler."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "AB.json").write_text('{"pipeline_id": "AB", "steps": {}}')

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        await _open_kitchen_handler()
        _close_kitchen_handler()

    assert not tracker_dir.exists()


async def test_back_to_back_open_close_open_resets_infrastructure(monkeypatch, tmp_path):
    """After close, gate_infrastructure_ready must be False so a re-open runs the handler."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        result1 = await _open_kitchen_handler()
        assert result1 is None
        assert ctx.gate_infrastructure_ready is True
        first_task = ctx.quota_refresh_task

        _close_kitchen_handler()
        assert ctx.gate_infrastructure_ready is False
        await asyncio.sleep(0)
        assert first_task.cancelled() or first_task.done()

        result2 = await _open_kitchen_handler()
        assert result2 is None
        assert ctx.gate_infrastructure_ready is True
        second_task = ctx.quota_refresh_task
        assert second_task is not first_task

        _close_kitchen_handler()
        await asyncio.sleep(0)
        assert second_task.cancelled() or second_task.done()


def _write_tracker(tracker_dir, kitchen_id, *, initialized_at=None):
    tracker_dir.mkdir(parents=True, exist_ok=True)
    tracker_data = {
        "kitchen_id": kitchen_id,
        "pipeline_id": kitchen_id,
        "initialized_at": (initialized_at or datetime.now(UTC)).isoformat(),
        "steps": {},
        "dependencies": {},
    }
    (tracker_dir / f"{kitchen_id}.json").write_text(json.dumps(tracker_data))


def test_open_without_close_prunes_dead_kitchen_tracker(monkeypatch, tmp_path):
    """A tracker whose registered PID is dead must be reaped on next open."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 99999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


def test_open_preserves_live_foreign_kitchen_tracker(monkeypatch, tmp_path):
    """A tracker whose registered PID is alive (a different kitchen) must survive."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": None,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()


def test_open_preserves_young_orphan_tracker(monkeypatch, tmp_path):
    """A tracker with no registry entry at all, but within the grace window, survives."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1", initialized_at=datetime.now(UTC))
    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()


def test_open_reaps_aged_orphan_tracker(monkeypatch, tmp_path):
    """A tracker with no registry entry at all, past the grace window, is reaped."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1", initialized_at=datetime.now(UTC) - timedelta(hours=24))
    _write_registry(monkeypatch, tmp_path, [])

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


def test_multi_entry_same_kitchen_one_alive_preserves_tracker(monkeypatch, tmp_path):
    """Fleet-campaign shape: multiple registry entries share one kitchen_id.

    If any matching entry is alive, the tracker must survive even though
    another entry for the same kitchen_id is dead.
    """
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 99999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            },
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": None,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            },
        ],
    )

    prune_stale_kitchen_state(tmp_path, "different-kitchen")

    assert (tracker_dir / "K1.json").exists()


def test_same_process_reopen_replaces_registry_entry(monkeypatch, tmp_path):
    """A tracker survives while its registry entry is alive, then is reaped once dead."""
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(tracker_dir, "K1")
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": os.getpid(),
                "create_time": None,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert (tracker_dir / "K1.json").exists()

    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "K1",
                "pid": 99999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    prune_stale_kitchen_state(tmp_path, "K2")

    assert not (tracker_dir / "K1.json").exists()


async def test_open_kitchen_sweeps_stale_kitchen_state_markers(monkeypatch, tmp_path):
    """_open_kitchen_handler wires sweep_stale_markers() to remove aged kitchen_state markers."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "state"))
    state_dir = tmp_path / "state" / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stale_marker = state_dir / "stale-session.json"
    stale_marker.write_text(
        json.dumps(
            {
                "session_id": "stale-session",
                "opened_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
                "recipe_name": "test",
                "marker_version": 1,
            }
        )
    )
    fresh_marker = state_dir / "fresh-session.json"
    fresh_marker.write_text(
        json.dumps(
            {
                "session_id": "fresh-session",
                "opened_at": datetime.now(UTC).isoformat(),
                "recipe_name": "test",
                "marker_version": 1,
            }
        )
    )

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        result = await _open_kitchen_handler()
        assert result is None

    assert not stale_marker.exists()
    assert fresh_marker.exists()


def test_deferred_recall_open_still_prunes(monkeypatch, tmp_path):
    """The deferred-recall open_kitchen path must prune stale trackers.

    When gate_infrastructure_ready is already True, open_kitchen skips
    _open_kitchen_handler entirely and takes the deferred-recall path.
    prune_stale_kitchen_state must still execute on that path.
    """
    tracker_dir = tmp_path / ".autoskillit" / "temp" / "pipeline_tracker"
    _write_tracker(
        tracker_dir, "dead-kitchen", initialized_at=datetime.now(UTC) - timedelta(hours=2)
    )
    _write_registry(
        monkeypatch,
        tmp_path,
        [
            {
                "kitchen_id": "dead-kitchen",
                "pid": 99999,
                "create_time": 1234567890.0,
                "project_path": str(tmp_path),
                "opened_at": datetime.now(UTC).isoformat(),
            }
        ],
    )

    assert (tracker_dir / "dead-kitchen.json").exists()

    prune_stale_kitchen_state(tmp_path, "live-kitchen")

    assert not (tracker_dir / "dead-kitchen.json").exists()


async def test_close_kitchen_removes_overlay_lock_sidecar(monkeypatch, tmp_path):
    """The overlay lock sidecar file must be removed alongside the overlay file."""
    monkeypatch.chdir(tmp_path)

    ctx = make_context(
        AutomationConfig(),
        runner=None,
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        project_dir=tmp_path,
    )
    monkeypatch.setattr(_state, "_ctx", ctx)
    monkeypatch.setattr(_state, "_startup_ready", None)

    with (
        patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new_callable=AsyncMock),
        patch("autoskillit.core.register_active_kitchen"),
        patch("autoskillit.core.unregister_active_kitchen"),
    ):
        await _open_kitchen_handler()

        from autoskillit.server._misc import _hook_config_overlay_path

        overlay_path = _hook_config_overlay_path(ctx.project_dir)
        overlay_lock_path = overlay_path.with_suffix(".lock")
        overlay_lock_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_lock_path.write_text("")
        assert overlay_lock_path.exists()

        _close_kitchen_handler()

        assert not overlay_lock_path.exists()
