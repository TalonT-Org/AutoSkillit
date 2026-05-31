"""Tests for _run_dispatch marker lifecycle via execution_marker context manager."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import anyio
import pytest

from autoskillit.core import SkillResult
from autoskillit.core._execution_marker import _touch_marker
from autoskillit.core.types._type_enums import RetryReason
from autoskillit.fleet._api import _run_dispatch
from tests.fleet._helpers import _no_sleep_quota_checker, _noop_quota_refresher, _setup_dispatch

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.medium, pytest.mark.feature("fleet")]


@pytest.mark.anyio
async def test_marker_created_before_dispatch(tool_ctx, monkeypatch, tmp_path: Path) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    marker_dir = tmp_path / "marker_dir"
    marker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "autoskillit.fleet._api.claude_code_project_dir",
        lambda _cwd: marker_dir,
    )

    async def _asserting_dispatch(**_kw):
        found = list(marker_dir.glob("*-in-progress-*.marker"))
        assert len(found) == 1, f"Expected 1 marker, found {len(found)}"
        raise asyncio.CancelledError

    monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _asserting_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await _run_dispatch(
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


@pytest.mark.anyio
async def test_marker_deleted_after_success(tool_ctx, monkeypatch, tmp_path: Path) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    marker_dir = tmp_path / "claude"
    marker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "autoskillit.fleet._api.claude_code_project_dir",
        lambda _cwd: marker_dir,
    )

    await _run_dispatch(
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

    remaining = list(marker_dir.glob("*-in-progress-*.marker"))
    assert remaining == [], f"Expected no marker files, found {remaining}"


@pytest.mark.anyio
async def test_marker_deleted_after_exception(tool_ctx, monkeypatch, tmp_path: Path) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    marker_dir = tmp_path / "claude"
    marker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "autoskillit.fleet._api.claude_code_project_dir",
        lambda _cwd: marker_dir,
    )

    async def _raise_runtime_error(**_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _raise_runtime_error)

    try:
        await _run_dispatch(
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
    except* RuntimeError:
        pass

    remaining = list(marker_dir.glob("*-in-progress-*.marker"))
    assert remaining == [], f"Expected no marker files after exception, found {remaining}"


@pytest.mark.anyio
async def test_heartbeat_refreshes_mtime(tmp_path: Path) -> None:
    marker_path = tmp_path / "dispatch-in-progress-test.marker"
    marker_path.write_text("{}")
    initial_mtime = marker_path.stat().st_mtime

    async with anyio.create_task_group() as tg:
        tg.start_soon(_touch_marker, marker_path, 0.05)

        async def _stop():
            await anyio.sleep(2.1)
            tg.cancel_scope.cancel()

        tg.start_soon(_stop)

    final_mtime = marker_path.stat().st_mtime
    assert final_mtime > initial_mtime, "heartbeat must advance marker mtime"


@pytest.mark.anyio
async def test_marker_not_written_when_cwd_unavailable(
    tool_ctx, monkeypatch, tmp_path: Path
) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)

    def _raise_oserror(_cwd: str) -> Path:
        raise OSError("simulated failure")

    monkeypatch.setattr(
        "autoskillit.fleet._api.claude_code_project_dir",
        _raise_oserror,
    )

    await _run_dispatch(
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

    assert len(tool_ctx.executor.dispatch_calls) == 1
    assert list(tmp_path.rglob("*.marker")) == []


@pytest.mark.anyio
async def test_run_dispatch_writes_heartbeat_file(tool_ctx, monkeypatch) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    dispatches_dir = tool_ctx.temp_dir / "dispatches"

    await _run_dispatch(
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

    remaining = list(dispatches_dir.glob("dispatch-*.heartbeat"))
    assert remaining == [], (
        f"Heartbeat file should be deleted after normal completion: {remaining}"
    )


@pytest.mark.anyio
async def test_run_dispatch_heartbeat_exists_during_execution(tool_ctx, monkeypatch) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    heartbeat_files_during: list[list[Path]] = []

    async def _asserting_dispatch(**_kw):
        found = list(dispatches_dir.glob("dispatch-*.heartbeat"))
        heartbeat_files_during.append(found)
        raise asyncio.CancelledError

    monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _asserting_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await _run_dispatch(
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

    assert len(heartbeat_files_during) == 1
    assert len(heartbeat_files_during[0]) == 1, (
        f"Expected 1 heartbeat file during dispatch, found {heartbeat_files_during[0]}"
    )

    remaining = list(dispatches_dir.glob("dispatch-*.heartbeat"))
    assert remaining == [], f"Heartbeat file should be deleted after CancelledError: {remaining}"


@pytest.mark.anyio
async def test_run_dispatch_heartbeat_mtime_is_fresh(tool_ctx, monkeypatch) -> None:
    _setup_dispatch(tool_ctx, monkeypatch)
    dispatches_dir = tool_ctx.temp_dir / "dispatches"
    recorded_mtime: list[float] = []

    async def _record_mtime_dispatch(**_kw):
        found = list(dispatches_dir.glob("dispatch-*.heartbeat"))
        if found:
            recorded_mtime.append(found[0].stat().st_mtime)
        return SkillResult(
            success=True,
            result="ok",
            session_id="",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr(tool_ctx.executor, "dispatch_food_truck", _record_mtime_dispatch)

    before = time.time()
    await _run_dispatch(
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

    assert len(recorded_mtime) == 1
    assert recorded_mtime[0] >= before - 1.0, "Heartbeat mtime should be recent at dispatch start"
