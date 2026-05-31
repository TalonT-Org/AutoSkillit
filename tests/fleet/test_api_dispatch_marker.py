"""Tests for _run_dispatch marker lifecycle via execution_marker context manager."""

from __future__ import annotations

import asyncio
from pathlib import Path

import anyio
import pytest

from autoskillit.core._execution_marker import _touch_marker
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
