"""Tests for fleet._api module (Group J)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import anyio
import pytest
import structlog.testing

from autoskillit.fleet import (
    DispatchRecord,
    _write_pid,
    write_initial_state,
)
from tests.fleet._helpers import _setup_dispatch

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


class TestWritePidExceptionSwallow:
    def test_nonexistent_state_logs_warning(self, tmp_path: Path) -> None:
        bogus = tmp_path / "nope" / "state.json"
        with structlog.testing.capture_logs() as logs:
            _write_pid(bogus, "d1", "id1", 123, 0, boot_id="")
        assert any(
            "_write_pid" in entry.get("event", "")
            for entry in logs
            if entry.get("log_level") == "warning"
        )

    def test_runtime_error_logs_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with structlog.testing.capture_logs() as logs:
            _write_pid(sp, "d1", "id1", 123, 0, boot_id="")
        assert any(
            "_write_pid" in entry.get("event", "")
            for entry in logs
            if entry.get("log_level") == "warning"
        )


class TestExecuteDispatchCancelledErrorLockRelease:
    @pytest.mark.anyio
    async def test_cancelled_error_propagates_and_releases_lock(
        self, tool_ctx, monkeypatch
    ) -> None:
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)
        fleet_lock = tool_ctx.fleet_lock
        active_count_at_cancel: list[int] = []

        async def _raise_cancelled(**_kwargs):
            active_count_at_cancel.append(fleet_lock.active_count)
            raise asyncio.CancelledError

        monkeypatch.setattr("autoskillit.fleet._api._run_dispatch", _raise_cancelled)

        with pytest.raises(asyncio.CancelledError):
            from autoskillit.fleet import execute_dispatch

            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="test-dispatch",
                timeout_sec=None,
                prompt_builder=lambda *a, **kw: "prompt",
                quota_checker=lambda *a, **kw: None,
                quota_refresher=lambda *a, **kw: None,
            )

        assert active_count_at_cancel == [1], "lock must be held when _run_dispatch is called"
        assert fleet_lock.active_count == 0

    @pytest.mark.anyio
    async def test_execute_dispatch_passes_resume_session_id_to_run_dispatch(
        self, tool_ctx, monkeypatch
    ) -> None:
        """Verify resume_session_id is forwarded to _run_dispatch.

        Raises CancelledError from the _run_dispatch mock intentionally: this
        short-circuits execute_dispatch after kwarg capture, avoiding the need
        to wire a full dispatch chain while still asserting kwarg forwarding.
        """
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        captured_kwargs: list[dict] = []

        async def _capture(**kwargs):
            captured_kwargs.append(kwargs)
            raise asyncio.CancelledError

        monkeypatch.setattr("autoskillit.fleet._api._run_dispatch", _capture)

        with pytest.raises(asyncio.CancelledError):
            from autoskillit.fleet import execute_dispatch

            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="test-dispatch",
                timeout_sec=None,
                prompt_builder=lambda *a, **kw: "prompt",
                quota_checker=lambda *a, **kw: None,
                quota_refresher=lambda *a, **kw: None,
                resume_session_id="abc-123",
            )

        assert captured_kwargs, "_run_dispatch was never called"
        assert captured_kwargs[0].get("resume_session_id") == "abc-123"

    @pytest.mark.anyio
    async def test_execute_dispatch_passes_caller_instructions_to_run_dispatch(
        self, tool_ctx, monkeypatch
    ) -> None:
        """Verify caller_instructions is forwarded to _run_dispatch.

        Raises CancelledError from the _run_dispatch mock intentionally: this
        short-circuits execute_dispatch after kwarg capture, avoiding the need
        to wire a full dispatch chain while still asserting kwarg forwarding.
        """
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        captured_kwargs: list[dict] = []

        async def _capture(**kwargs):
            captured_kwargs.append(kwargs)
            raise asyncio.CancelledError

        monkeypatch.setattr("autoskillit.fleet._api._run_dispatch", _capture)

        with pytest.raises(asyncio.CancelledError):
            from autoskillit.fleet import execute_dispatch

            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="test-dispatch",
                timeout_sec=None,
                prompt_builder=lambda *a, **kw: "prompt",
                quota_checker=lambda *a, **kw: None,
                quota_refresher=lambda *a, **kw: None,
                caller_instructions="skip review",
            )

        assert captured_kwargs, "_run_dispatch was never called"
        assert captured_kwargs[0].get("caller_instructions") == "skip review"

    @pytest.mark.anyio
    async def test_cancelled_dispatch_triggers_label_cleanup(self, tool_ctx, monkeypatch) -> None:
        """swap_labels is called for the claimed issue when dispatch is cancelled.

        Patches dispatch_food_truck (not _run_dispatch) so the real finally block
        runs — this directly covers the gap in the existing CancelledError test.
        """
        from unittest.mock import AsyncMock

        from autoskillit.fleet import execute_dispatch
        from autoskillit.fleet.sidecar import sidecar_path as make_sidecar_path
        from tests.fleet._helpers import _no_sleep_quota_checker, _noop_quota_refresher

        _setup_dispatch(tool_ctx, monkeypatch)

        swap_labels_mock = AsyncMock(return_value={"success": True})
        github_client = AsyncMock()
        github_client.swap_labels = swap_labels_mock
        tool_ctx.github_client = github_client

        async def _write_sidecar_then_cancel(**kwargs):
            sidecar = make_sidecar_path(kwargs["dispatch_id"], tool_ctx.project_dir)
            sidecar.write_text(
                json.dumps(
                    {
                        "issue_url": "https://github.com/owner/repo/issues/42",
                        "status": "completed",
                        "ts": "2026-01-01T00:00:00Z",
                    }
                )
                + "\n"
            )
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _write_sidecar_then_cancel

        with pytest.raises(asyncio.CancelledError):
            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="t",
                ingredients=None,
                dispatch_name=None,
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        swap_labels_mock.assert_called_once()
        call = swap_labels_mock.call_args
        assert "in-progress" in call.kwargs["remove_labels"]
        assert "fail" in call.kwargs["add_labels"]


# ---------------------------------------------------------------------------
# requires_packs forwarding helpers
# ---------------------------------------------------------------------------


async def _no_sleep_quota_checker(config, **kwargs) -> dict:
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config, **kwargs) -> None:
    pass


async def _run(tool_ctx, recipe="test-recipe", ingredients=None):
    from autoskillit.fleet._api import execute_dispatch

    result = await execute_dispatch(
        tool_ctx=tool_ctx,
        recipe=recipe,
        task="t",
        ingredients=ingredients,
        dispatch_name=None,
        timeout_sec=None,
        prompt_builder=lambda **kwargs: f"prompt-for-{kwargs.get('recipe', 'unknown')}",
        quota_checker=_no_sleep_quota_checker,
        quota_refresher=_noop_quota_refresher,
    )
    return json.loads(result.outcome.to_envelope())


class TestRequiresPacksForwarding:
    @pytest.mark.anyio
    async def test_run_dispatch_forwards_requires_packs_to_executor(self, tool_ctx, monkeypatch):
        _setup_dispatch(tool_ctx, monkeypatch, requires_packs=["github", "ci"])
        await _run(tool_ctx)
        call = tool_ctx.executor.dispatch_calls[0]
        assert list(call.requires_packs) == ["github", "ci"]

    @pytest.mark.anyio
    async def test_run_dispatch_defaults_kitchen_core_when_requires_packs_empty(
        self, tool_ctx, monkeypatch
    ):
        _setup_dispatch(tool_ctx, monkeypatch)
        await _run(tool_ctx)
        call = tool_ctx.executor.dispatch_calls[0]
        assert list(call.requires_packs) == ["kitchen-core"]


class TestDispatchResultWrapper:
    """execute_dispatch returns a DispatchResult with per_dispatch_state_path set on success."""

    @pytest.mark.anyio
    async def test_execute_dispatch_returns_dispatch_result_with_state_path_on_success(
        self, tool_ctx, monkeypatch
    ):
        """execute_dispatch must return DispatchResult with non-None per_dispatch_state_path."""
        from autoskillit.fleet import DispatchResult
        from autoskillit.fleet._api import execute_dispatch
        from tests.fleet._helpers import _no_sleep_quota_checker, _noop_quota_refresher

        _setup_dispatch(tool_ctx, monkeypatch)
        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe="test-recipe",
            task="t",
            ingredients=None,
            dispatch_name=None,
            timeout_sec=None,
            prompt_builder=lambda **kwargs: "prompt",
            quota_checker=_no_sleep_quota_checker,
            quota_refresher=_noop_quota_refresher,
        )
        assert isinstance(result, DispatchResult)
        assert result.per_dispatch_state_path is not None
        assert result.per_dispatch_state_path.exists()


class TestTouchDispatchMarker:
    """Unit tests for _touch_dispatch_marker helper."""

    @pytest.mark.anyio
    async def test_touch_dispatch_marker_creates_heartbeat_loop(self, tmp_path: Path) -> None:
        """Heartbeat loop refreshes mtime until trigger is set."""
        import os

        from autoskillit.fleet._api import _touch_dispatch_marker

        marker = tmp_path / "test.marker"
        marker.touch()
        os.utime(marker, (0, 0))  # Set to epoch 0 so any real touch shows as newer
        original_mtime_ns = marker.stat().st_mtime_ns

        trigger = anyio.Event()

        async def _heartbeat_wrapper() -> None:
            await _touch_dispatch_marker(marker, interval=0.05, trigger=trigger)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_heartbeat_wrapper)
            # 1.0s gives ~20 heartbeat cycles; tolerates WSL2 CLOCK_REALTIME backward
            # jumps of up to ~0.9s from NTP sync without causing a spurious failure.
            await anyio.sleep(1.0)
            trigger.set()

        new_mtime_ns = marker.stat().st_mtime_ns
        assert new_mtime_ns > original_mtime_ns, "marker mtime should have been refreshed"

    @pytest.mark.anyio
    async def test_touch_dispatch_marker_oserror_does_not_propagate(self, tmp_path: Path) -> None:
        """OSError during touch logs but does not raise."""
        from autoskillit.fleet._api import _touch_dispatch_marker

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        marker_file = subdir / "test.marker"
        marker_file.touch()  # create the file first
        marker_file.chmod(0o000)  # make unwritable so touch() fails
        trigger = anyio.Event()

        async def _heartbeat_wrapper() -> None:
            await _touch_dispatch_marker(marker_file, interval=0.05, trigger=trigger)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_heartbeat_wrapper)
                await anyio.sleep(0.12)
                trigger.set()
        except OSError:
            pytest.fail("_touch_dispatch_marker should not propagate OSError")
        finally:
            marker_file.chmod(0o644)  # restore for cleanup


class TestDispatchMarkerLifecycle:
    """Tests for marker lifecycle integration in _run_dispatch."""

    @pytest.mark.anyio
    async def test_run_dispatch_forwards_marker_dir_and_session_id_to_executor(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """dispatch_food_truck is called with marker_dir and session_id kwargs."""
        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: marker_dir,
        )

        await _run(tool_ctx)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        call = tool_ctx.executor.dispatch_calls[0]
        assert call.marker_dir == marker_dir
        assert call.session_id is not None

    @pytest.mark.anyio
    async def test_run_dispatch_continues_when_marker_dir_unavailable(
        self, tool_ctx, monkeypatch
    ) -> None:
        """execute_dispatch succeeds even when claude_code_project_dir raises OSError."""
        _setup_dispatch(tool_ctx, monkeypatch)
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: (_ for _ in ()).throw(OSError("no project dir")),
        )

        result = await _run(tool_ctx)

        # Should complete without raising (returns DispatchCompleted or DispatchRejected)
        assert result.get("success") is not None

    @pytest.mark.anyio
    async def test_run_dispatch_marker_cleaned_up_after_success(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """No .marker files remain after successful dispatch."""
        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: marker_dir,
        )

        await _run(tool_ctx)

        remaining = list(marker_dir.glob("*.marker"))
        assert len(remaining) == 0, f"Expected no marker files, found {remaining}"

    @pytest.mark.anyio
    async def test_run_dispatch_marker_cleaned_up_after_failure(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """No .marker files remain after dispatch raises (caught as DispatchRejected)."""
        from functools import wraps

        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: marker_dir,
        )

        original_dispatch_food_truck = tool_ctx.executor.dispatch_food_truck

        @wraps(original_dispatch_food_truck)
        async def _raise_dispatch(*args, **kwargs):
            await original_dispatch_food_truck(*args, **kwargs)
            raise RuntimeError("dispatch failed")

        tool_ctx.executor.dispatch_food_truck = _raise_dispatch

        result = await _run(tool_ctx)

        # execute_dispatch catches Exception and returns DispatchRejected
        assert result.get("success") is False, "dispatch should be rejected on failure"
        remaining = list(marker_dir.glob("*.marker"))
        assert len(remaining) == 0, f"Expected no marker files after failure, found {remaining}"

    @pytest.mark.anyio
    async def test_run_dispatch_creates_marker_file(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """Marker file is created during dispatch execution."""
        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: marker_dir,
        )

        marker_seen: list[bool] = []
        original_dispatch = tool_ctx.executor.dispatch_food_truck

        async def _capture_marker(*args, **kwargs):
            markers_found = list(marker_dir.glob("*.marker"))
            marker_seen.append(len(markers_found) > 0)
            return await original_dispatch(*args, **kwargs)

        tool_ctx.executor.dispatch_food_truck = _capture_marker

        await _run(tool_ctx)

        assert len(marker_seen) == 1, "dispatch_food_truck should be called once"
        assert marker_seen[0] is True, "marker file should exist during dispatch execution"

    @pytest.mark.anyio
    async def test_run_dispatch_marker_contains_expected_json(
        self, tool_ctx, monkeypatch, tmp_path: Path
    ) -> None:
        """Marker file JSON contains dispatch_id, orchestrator_pid, and session_id."""
        _setup_dispatch(tool_ctx, monkeypatch)
        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setattr(
            "autoskillit.fleet._api.claude_code_project_dir",
            lambda cwd: marker_dir,
        )

        captured_json: list[dict] = []

        original_dispatch = tool_ctx.executor.dispatch_food_truck

        async def _capture_marker(*args, **kwargs):
            markers = list(marker_dir.glob("*.marker"))
            if markers:
                content = markers[0].read_text()
                captured_json.append(json.loads(content))
            return await original_dispatch(*args, **kwargs)

        tool_ctx.executor.dispatch_food_truck = _capture_marker

        await _run(tool_ctx)

        assert len(captured_json) == 1, "should have captured marker JSON"
        data = captured_json[0]
        assert "dispatch_id" in data
        assert "orchestrator_pid" in data
        assert "session_id" in data
        assert isinstance(data["orchestrator_pid"], int)
        assert isinstance(data["session_id"], str)


class TestWriteDispatchToCampaignStateRefusal:
    """_write_dispatch_to_campaign_state must persist the diagnostic message for rejections."""

    def test_refusal_persists_diagnostic_message_to_campaign(self, tmp_path: Path) -> None:
        """REFUSED records in campaign state must carry the human-readable diagnostic message."""
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import (
            DispatchRecord,
            DispatchRejected,
            read_state,
            write_initial_state,
        )
        from autoskillit.server.tools.tools_fleet_dispatch import _write_dispatch_to_campaign_state

        campaign_state_path = tmp_path / "campaign.json"
        write_initial_state(
            campaign_state_path,
            "cmp-refuse",
            "test-campaign",
            "/m.yaml",
            [DispatchRecord(name="dispatch-1")],
        )

        _write_dispatch_to_campaign_state(
            str(campaign_state_path),
            "dispatch-1",
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
                message="RuntimeError: database connection lost",
            ),
        )

        state = read_state(campaign_state_path)
        assert state is not None
        refused = [d for d in state.dispatches if d.status.value == "refused"]
        assert len(refused) == 1
        assert refused[0].diagnostic_message == "RuntimeError: database connection lost"
        assert refused[0].reason == "fleet_l3_startup_or_crash"

    def test_refusal_without_message_defaults_empty(self, tmp_path: Path) -> None:
        """Omitting message defaults to empty string."""
        from autoskillit.core import FleetErrorCode
        from autoskillit.fleet import (
            DispatchRecord,
            DispatchRejected,
            read_state,
            write_initial_state,
        )
        from autoskillit.server.tools.tools_fleet_dispatch import _write_dispatch_to_campaign_state

        campaign_state_path = tmp_path / "campaign.json"
        write_initial_state(
            campaign_state_path,
            "cmp-nomsg",
            "test-campaign",
            "/m.yaml",
            [DispatchRecord(name="dispatch-2")],
        )

        _write_dispatch_to_campaign_state(
            str(campaign_state_path),
            "dispatch-2",
            DispatchRejected(
                error_code=FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
                message="",
            ),
        )

        state = read_state(campaign_state_path)
        assert state is not None
        refused = [d for d in state.dispatches if d.status.value == "refused"]
        assert len(refused) == 1
        assert refused[0].diagnostic_message == ""
        assert refused[0].reason == "fleet_unknown_ingredient"


class TestResolveDispatchTimeout:
    def test_uses_config_when_none(self) -> None:
        """resolve_dispatch_timeout must use fleet.default_timeout_sec when timeout_sec is None."""
        from autoskillit.fleet._api import resolve_dispatch_timeout

        result = resolve_dispatch_timeout(None, default_timeout_sec=3600)
        assert result == 3600.0

    def test_uses_explicit_when_provided(self) -> None:
        """resolve_dispatch_timeout must use the explicit value when provided."""
        from autoskillit.fleet._api import resolve_dispatch_timeout

        result = resolve_dispatch_timeout(1200, default_timeout_sec=3600)
        assert result == 1200.0

    def test_handles_zero(self) -> None:
        """resolve_dispatch_timeout must treat 0 as an explicit value, not as None."""
        from autoskillit.fleet._api import resolve_dispatch_timeout

        result = resolve_dispatch_timeout(0, default_timeout_sec=3600)
        assert result == 0.0


class TestCancelledErrorRecordsInterruptedState:
    @pytest.mark.anyio
    async def test_cancelled_error_records_interrupted_state(self, tool_ctx, monkeypatch) -> None:
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        _on_spawn_called = [False]

        async def _spawn_then_cancel(**kwargs):
            on_spawn = kwargs.get("on_spawn")
            if on_spawn:
                on_spawn(99999, 0)
                _on_spawn_called[0] = True
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _spawn_then_cancel

        with pytest.raises(asyncio.CancelledError):
            from autoskillit.fleet import execute_dispatch

            await execute_dispatch(
                tool_ctx=tool_ctx,
                recipe="test-recipe",
                task="do something",
                ingredients=None,
                dispatch_name="test-dispatch",
                timeout_sec=None,
                prompt_builder=lambda **kw: "prompt",
                quota_checker=_no_sleep_quota_checker,
                quota_refresher=_noop_quota_refresher,
            )

        assert _on_spawn_called[0]

        state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
        assert len(state_files) == 1
        state = json.loads(state_files[0].read_text())
        d = state["dispatches"][0]
        assert d["status"] == "interrupted"
        assert d["reason"] == "signal_induced_cancellation"
        assert tool_ctx.fleet_lock.active_count == 0
