"""Test coverage for the enable_exploration gate tool (REQ-93)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from autoskillit.core import SessionType
from autoskillit.hooks._exploration_request_record import write_exploration_request_record
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore
from autoskillit.server import mcp

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.asyncio
async def test_enable_exploration_rejects_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORCHESTRATOR sessions cannot establish exploration authority."""
    from autoskillit.server.tools.tools_exploration import enable_exploration

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.ORCHESTRATOR,
    )
    consumer = MagicMock()
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration.consume_exploration_request_record",
        consumer,
    )
    result = json.loads(await enable_exploration())
    assert result["status"] == "error"
    assert result["code"] == "session_type_ineligible"
    consumer.assert_not_called()


@pytest.mark.asyncio
async def test_enable_exploration_rejects_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLEET sessions cannot establish exploration authority."""
    from autoskillit.server.tools.tools_exploration import enable_exploration

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.FLEET,
    )
    result = json.loads(await enable_exploration())
    assert result["status"] == "error"
    assert result["code"] == "session_type_ineligible"


@pytest.mark.asyncio
async def test_enable_exploration_succeeds_for_skill_session(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
) -> None:
    """The production-composed context binds the consumed native session identity."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx.project_dir,
        service=exploration_snapshot_service,
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    bind = MagicMock(wraps=store.bind_session_scoped)
    monkeypatch.setattr(store, "bind_session_scoped", bind)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )

    async with Client(mcp) as client:
        token = write_exploration_request_record(
            tool_ctx.project_dir, "enable_exploration", "test-session"
        )
        wire_result = await client.call_tool(
            "enable_exploration",
            {"_autoskillit_exploration_request_token": token},
        )
    result = json.loads(wire_result.structured_content["result"])
    assert result["status"] == "ok"
    assert result["exploration_enabled"] is True
    assert bind.call_args.kwargs["session_id"] == "test-session"
    assert bind.call_args.kwargs["source_identity"] == "interactive:test-session"


@pytest.mark.asyncio
async def test_enable_exploration_revokes_authority_when_visibility_enable_fails(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
) -> None:
    from autoskillit.server.tools.tools_exploration import enable_exploration

    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx.project_dir,
        service=exploration_snapshot_service,
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    cleanup = MagicMock(wraps=store.cleanup_session)
    monkeypatch.setattr(store, "cleanup_session", cleanup)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )
    request_ctx = MagicMock()
    request_ctx.enable_components = AsyncMock(side_effect=RuntimeError("enable failed"))

    result = json.loads(
        await enable_exploration(
            _autoskillit_exploration_request_token=token,
            ctx=request_ctx,
        )
    )

    assert result == {"status": "error", "code": "exploration_provisioning_failed"}
    assert store.session_scoped_capability("test-session") is None
    cleanup.assert_called_once_with("test-session")


@pytest.mark.asyncio
async def test_enable_exploration_revokes_authority_when_visibility_enable_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
) -> None:
    from autoskillit.server.tools.tools_exploration import enable_exploration

    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx.project_dir,
        service=exploration_snapshot_service,
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    cleanup = MagicMock(wraps=store.cleanup_session)
    monkeypatch.setattr(store, "cleanup_session", cleanup)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )
    request_ctx = MagicMock()
    request_ctx.enable_components = AsyncMock(side_effect=asyncio.CancelledError())

    result = json.loads(
        await enable_exploration(
            _autoskillit_exploration_request_token=token,
            ctx=request_ctx,
        )
    )

    assert result == {"success": False, "error": "cancelled", "subtype": "cancelled"}
    assert store.session_scoped_capability("test-session") is None
    cleanup.assert_called_once_with("test-session")


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "invalid", "A" * 43])
async def test_enable_exploration_rejects_missing_invalid_and_unknown_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    token: str,
) -> None:
    from autoskillit.server.tools.tools_exploration import enable_exploration

    store = tool_ctx.exploration_context_store
    assert isinstance(store, OwnerBoundExplorationContextStore)
    bind = MagicMock(wraps=store.bind_session_scoped)
    monkeypatch.setattr(store, "bind_session_scoped", bind)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )

    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))

    assert result == {"status": "error", "code": "no_session_id"}
    bind.assert_not_called()
