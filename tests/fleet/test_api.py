"""Tests for fleet._api module (Group J)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import structlog.testing

from autoskillit.fleet import (
    DispatchRecord,
    _write_pid,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


class TestWritePidExceptionSwallow:
    def test_nonexistent_state_logs_warning(self, tmp_path: Path) -> None:
        bogus = tmp_path / "nope" / "state.json"
        with structlog.testing.capture_logs() as logs:
            _write_pid(bogus, "d1", "id1", 123, 0)
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
            _write_pid(sp, "d1", "id1", 123, 0)
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
