"""Tests for the per-tool operation lease middleware."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.exceptions import ToolError

from autoskillit.core import read_active_operation_leases
from autoskillit.server._operation_lease_middleware import OperationLeaseMiddleware
from autoskillit.server.lifecycle import _state

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_TOOL_NAME = "operation_lease_probe"


def test_make_context_stores_operation_lease_channel(make_tool_ctx, tmp_path: Path) -> None:
    ctx = make_tool_ctx(operation_lease_channel=tmp_path)

    assert ctx.operation_lease_channel == tmp_path


def _app(handler, *, completion_middleware: bool = False) -> FastMCP:
    app = FastMCP("operation-lease-test")
    app.add_middleware(OperationLeaseMiddleware())
    if completion_middleware:
        from autoskillit.server.response._run_skill_completion import (
            RunSkillCompletionMiddleware,
        )

        app.add_middleware(RunSkillCompletionMiddleware(app))

    @app.tool(name=_TOOL_NAME)
    async def operation_lease_probe() -> str:
        return await handler()

    return app


async def _call(app: FastMCP):
    async with Client(app) as client:
        return await client.call_tool(_TOOL_NAME, {})


@pytest.mark.parametrize("inherited", [None, "future"])
@pytest.mark.anyio
async def test_tool_call_writes_deadline_bounded_lease(
    tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: str | None
) -> None:
    tool_ctx.operation_lease_channel = tmp_path
    monkeypatch.setattr(_state, "_get_ctx_or_none", lambda: tool_ctx)
    inherited_epoch = time.time() + 10
    if inherited is not None:
        monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", str(inherited_epoch))

    observed: list[tuple[float, int]] = []

    async def inspect_lease() -> str:
        records = read_active_operation_leases(tmp_path, now_epoch=time.time())
        assert len(records) == 1
        assert records[0].operation == _TOOL_NAME
        observed.append((records[0].not_after_epoch, tool_ctx.in_flight_operations.active_count))
        return "ok"

    before = time.time()
    result = await _call(_app(inspect_lease))
    assert len(result.content) == 1
    assert tool_ctx.in_flight_operations.active_count == 0
    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()
    expected = min(
        before + tool_ctx.config.run_skill.mcp_tool_timeout_sec,
        inherited_epoch if inherited is not None else float("inf"),
    )
    assert observed[0][1] == 1
    assert observed[0][0] == pytest.approx(expected, abs=0.1)


@pytest.mark.anyio
async def test_handler_failure_still_removes_lease(
    tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.operation_lease_channel = tmp_path
    monkeypatch.setattr(_state, "_get_ctx_or_none", lambda: tool_ctx)

    async def fail() -> str:
        assert len(read_active_operation_leases(tmp_path, now_epoch=time.time())) == 1
        raise RuntimeError("handler failed")

    with pytest.raises(ToolError):
        await _call(_app(fail))

    assert tool_ctx.in_flight_operations.active_count == 0
    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()


@pytest.mark.anyio
async def test_no_channel_keeps_registry_active_without_file(
    tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.operation_lease_channel = None
    monkeypatch.setattr(_state, "_get_ctx_or_none", lambda: tool_ctx)
    counts: list[int] = []

    async def inspect_registry() -> str:
        counts.append(tool_ctx.in_flight_operations.active_count)
        return "ok"

    await _call(_app(inspect_registry))

    assert counts == [1]
    assert tool_ctx.in_flight_operations.active_count == 0
    assert tuple(tmp_path.iterdir()) == ()


def test_registered_lease_middleware_is_outermost() -> None:
    from autoskillit.server import mcp

    assert isinstance(mcp.middleware[0], OperationLeaseMiddleware)


@pytest.mark.anyio
async def test_completion_denial_releases_outer_lease(
    tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.pipeline import DefaultRunSkillCompletionAuthority

    authority = DefaultRunSkillCompletionAuthority()
    invocation = authority.begin(
        kitchen_id="kitchen",
        request_session_id="request",
        tracker_order_id="order",
        tracker_path="/tracker.json",
        tracker_kitchen_id="kitchen",
        tracker_incarnation_id="incarnation",
        step_name="step",
    )
    authority.draft(
        invocation,
        classification="success",
        success=True,
        result_digest="sha256:digest",
    )
    tool_ctx.run_skill_completion = authority
    tool_ctx.operation_lease_channel = tmp_path
    monkeypatch.setattr(_state, "_get_ctx_or_none", lambda: tool_ctx)
    called = False

    async def handler() -> str:
        nonlocal called
        called = True
        return "unexpected"

    result = await _call(_app(handler, completion_middleware=True))

    assert not called
    assert result.isError
    assert tool_ctx.in_flight_operations.active_count == 0
    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()


@pytest.mark.anyio
async def test_expired_inherited_deadline_fails_before_lease_admission(
    tool_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.operation_lease_channel = tmp_path
    monkeypatch.setattr(_state, "_get_ctx_or_none", lambda: tool_ctx)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", str(time.time() - 1))
    called = False

    async def handler() -> str:
        nonlocal called
        called = True
        return "unexpected"

    with pytest.raises(ToolError):
        await _call(_app(handler))

    assert not called
    assert tool_ctx.in_flight_operations.active_count == 0
    assert read_active_operation_leases(tmp_path, now_epoch=time.time()) == ()


@pytest.mark.parametrize("value", ["", "invalid", "nan", "inf", "-1"])
def test_inherited_session_deadline_parser_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from autoskillit.server.tools._execution_helpers._dispatch_metadata import (
        inherited_session_deadline_epoch,
    )

    monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", value)

    assert inherited_session_deadline_epoch() == 0.0
