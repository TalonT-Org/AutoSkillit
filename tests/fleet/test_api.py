"""Tests for fleet._api module (Group J)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import anyio
import pytest

from autoskillit.fleet import (
    DispatchEffectProvenance,
    DispatchRecord,
    _write_pid,
    write_initial_state,
)
from tests.fleet._helpers import _mock_backend_with_locator, _setup_dispatch

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


class TestWritePidReturnsErrorOnFailure:
    """The new fail-closed contract: ``_write_pid`` returns an error string on failure
    rather than raising. Raising from ``on_spawn`` is unsafe because
    ``_execute_claude_headless`` catches runner exceptions and returns
    ``SkillResult.crashed`` — closure-scoped error state surfaces the failure
    to the outer ``execute_dispatch`` wrapper instead.
    """

    def test_missing_state_returns_error_string(self, tmp_path: Path) -> None:
        bogus = tmp_path / "nope" / "state.json"
        result = _write_pid(bogus, "d1", "id1", 123, 0)
        assert result is not None
        assert "_on_spawn transition failed" in result

    def test_mark_dispatch_running_exception_returns_error_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = _write_pid(sp, "d1", "id1", 123, 0)
        assert result is not None
        assert "RuntimeError" in result
        assert "boom" in result


class TestWritePidIssueUrlForwarding:
    def test_write_pid_forwards_issue_url(self, tmp_path: Path) -> None:
        from autoskillit.fleet import read_state

        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        _write_pid(sp, "d1", "id1", 123, 0, issue_url="https://github.com/o/r/issues/1")
        state = read_state(sp)
        assert state is not None
        assert state.dispatches[0].issue_url == "https://github.com/o/r/issues/1"


# --- Fail-closed spawn (L2) -----------------------------------------------------
#
# Inverts the contract codified by TestWritePidExceptionSwallow above: rather than
# swallowing exceptions from mark_dispatch_running, the new fail-closed path must
# (a) kill the spawned child via kill_process_tree, and (b) record the error
# via closure-scoped error state so the caller can surface a structured
# envelope. Raising from _on_spawn is unsafe because _execute_claude_headless
# catches runner exceptions and returns SkillResult.crashed — the propagated
# exception would never reach the outer execute_dispatch wrapper.


class TestOnSpawnFailClosed:
    def test_on_spawn_kills_child_via_kill_process_tree_when_mark_running_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When mark_dispatch_running raises, kill_process_tree is invoked on the spawned pid
        and _write_pid returns the error string instead of raising (closure-scoped error state)."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))

        killed: list[int] = []
        # kill_process_tree is imported lazily inside _write_pid from
        # autoskillit.execution — monkeypatch the actual symbol used.
        monkeypatch.setattr(
            "autoskillit.execution.kill_process_tree",
            lambda pid, timeout=2.0: killed.append(pid),
        )
        # Force mark_dispatch_running to raise inside _write_pid.
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(
                ValueError("invalid transition FAILURE→RUNNING")
            ),
        )

        result = _write_pid(sp, "d1", "id1", pid=4242, starttime_ticks=0)

        assert result is not None
        assert "transition failed" in result
        assert killed == [4242]

    def test_on_spawn_does_not_swallow_state_machine_exceptions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ValueError from mark_dispatch_running surfaces as an error string from _write_pid.

        The new fail-closed contract: kill the child AND return an error
        string the caller can use to surface a structured envelope.
        """
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        monkeypatch.setattr(
            "autoskillit.execution.kill_process_tree",
            lambda pid, timeout=2.0: None,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("boom")),
        )

        result = _write_pid(sp, "d1", "id1", pid=9999, starttime_ticks=0)

        # The error message preserves the original failure context — the
        # caller can use this to surface a structured envelope.
        assert result is not None
        assert "boom" in result

    def test_on_spawn_propagates_error_for_unrecoverable_transition_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transition failure surfaces as a returned error string — never silently swallowed."""
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        monkeypatch.setattr(
            "autoskillit.execution.kill_process_tree",
            lambda pid, timeout=2.0: None,
        )
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(ValueError("FAILURE → RUNNING illegal")),
        )

        result = _write_pid(sp, "d1", "id1", pid=7, starttime_ticks=0)
        assert result is not None
        # The wrapped message preserves the original failure context.
        assert "FAILURE" in result and "RUNNING" in result and "illegal" in result


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
    async def test_execute_dispatch_passes_provider_capability_overrides_to_run_dispatch(
        self, tool_ctx, monkeypatch
    ) -> None:
        """Verify provider_capability_overrides is forwarded to _run_dispatch.

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
                provider_capability_overrides={"backend_supports_git_write": "true"},
            )

        assert captured_kwargs, "_run_dispatch was never called"
        assert captured_kwargs[0].get("provider_capability_overrides") == {
            "backend_supports_git_write": "true"
        }

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


class TestTouchMarker:
    """Unit tests for _touch_marker helper (core._execution_marker)."""

    @pytest.mark.anyio
    async def test_touch_marker_creates_heartbeat_loop(self, tmp_path: Path) -> None:
        """Heartbeat loop refreshes mtime until cancelled."""
        import os

        from autoskillit.core._execution_marker import _touch_marker

        marker = tmp_path / "test.marker"
        marker.touch()
        os.utime(marker, (0, 0))
        original_mtime_ns = marker.stat().st_mtime_ns

        async with anyio.create_task_group() as tg:
            tg.start_soon(_touch_marker, marker, 0.05)
            await anyio.sleep(1.0)
            tg.cancel_scope.cancel()

        new_mtime_ns = marker.stat().st_mtime_ns
        assert new_mtime_ns > original_mtime_ns, "marker mtime should have been refreshed"

    @pytest.mark.anyio
    async def test_touch_marker_oserror_does_not_propagate(self, tmp_path: Path) -> None:
        """OSError during touch logs but does not raise."""
        from autoskillit.core._execution_marker import _touch_marker

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        marker_file = subdir / "test.marker"
        marker_file.touch()
        marker_file.chmod(0o000)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_touch_marker, marker_file, 0.05)
                await anyio.sleep(0.12)
                tg.cancel_scope.cancel()
        except OSError:
            pytest.fail("_touch_marker should not propagate OSError")
        finally:
            marker_file.chmod(0o644)


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
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

        await _run(tool_ctx)

        assert len(tool_ctx.executor.dispatch_calls) == 1
        call = tool_ctx.executor.dispatch_calls[0]
        assert call.marker_dir == marker_dir
        assert call.session_id is not None

    @pytest.mark.anyio
    async def test_run_dispatch_continues_when_marker_dir_unavailable(
        self, tool_ctx, monkeypatch
    ) -> None:
        """execute_dispatch succeeds even when backend's session_locator is unavailable."""
        _setup_dispatch(tool_ctx, monkeypatch)
        tool_ctx.backend = None

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
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

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
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

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
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

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
        tool_ctx.backend = _mock_backend_with_locator(project_log_dir=marker_dir)

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
        assert "label" in data
        assert "orchestrator_pid" in data
        assert "session_id" in data
        assert isinstance(data["orchestrator_pid"], int)
        assert isinstance(data["session_id"], str)
        assert data["label"] == "dispatch"


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
                effect_provenance=DispatchEffectProvenance(operation_id="test"),
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
                effect_provenance=DispatchEffectProvenance(operation_id="test"),
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
        assert (
            d["dispatched_session_id"] == ""
        )  # No session_id available when CancelledError fires before dispatch completes
        assert d["effect_provenance"]["retry_disposition"] == "reconcile_required"
        spawn_effect = next(
            effect
            for effect in d["effect_provenance"]["effects"]
            if effect["name"] == "process_spawn"
        )
        assert spawn_effect["phase"] == "started"
        assert tool_ctx.fleet_lock.active_count == 0


class TestSessionIdEagerPersistence:
    """Tests for the on_session_id_resolved callback fired during live execution.

    These tests verify that the session identity is eagerly persisted to state
    when discovered during the subprocess execution task group, so that it
    survives a CancelledError that arrives before normal completion.
    """

    @pytest.mark.anyio
    async def test_post_completion_cancel_persists_session_id(self, tool_ctx, monkeypatch) -> None:
        """CancelledError after session completes must persist dispatched_session_id."""
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        async def _resolve_session_then_cancel(**kwargs):
            on_spawn = kwargs.get("on_spawn")
            if on_spawn:
                on_spawn(99999, 0)
            on_session_id_resolved = kwargs.get("on_session_id_resolved")
            if on_session_id_resolved:
                on_session_id_resolved("test-session-abc")
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _resolve_session_then_cancel

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

        state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
        assert len(state_files) == 1
        state = json.loads(state_files[0].read_text())
        d = state["dispatches"][0]
        assert d["dispatched_session_id"] == "test-session-abc"
        assert d["status"] == "interrupted"

    @pytest.mark.anyio
    async def test_mid_session_cancel_persists_session_id_via_callback(
        self, tool_ctx, monkeypatch
    ) -> None:
        """CancelledError during execution must still persist session_id via eager callback."""
        from tests.fleet._helpers import _setup_dispatch

        _setup_dispatch(tool_ctx, monkeypatch)

        async def _spawn_resolve_session_then_cancel(**kwargs):
            on_spawn = kwargs.get("on_spawn")
            if on_spawn:
                on_spawn(99999, 0)
            on_session_id_resolved = kwargs.get("on_session_id_resolved")
            if on_session_id_resolved:
                on_session_id_resolved("early-session-xyz")
            raise asyncio.CancelledError

        tool_ctx.executor.dispatch_food_truck = _spawn_resolve_session_then_cancel

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

        state_files = list((tool_ctx.temp_dir / "dispatches").glob("*.json"))
        assert len(state_files) == 1
        state = json.loads(state_files[0].read_text())
        d = state["dispatches"][0]
        assert d["dispatched_session_id"] == "early-session-xyz"
        assert d["status"] == "interrupted"
        assert d["effect_provenance"]["retry_disposition"] == "resume_by_identity"
        spawn_effect = next(
            effect
            for effect in d["effect_provenance"]["effects"]
            if effect["name"] == "process_spawn"
        )
        assert spawn_effect["phase"] == "confirmed"
        assert spawn_effect["known_downstream_identities"]["dispatched_session_id"] == (
            "early-session-xyz"
        )
