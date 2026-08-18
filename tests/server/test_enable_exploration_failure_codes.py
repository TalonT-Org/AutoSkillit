"""Precondition-code matrix for enable_exploration (#4684 Fix A).

Each test monkeypatches exactly one precondition and asserts the tool
returns its own distinct ExplorationFailureCode. Before this matrix, the
catch-all at tools_exploration.py's enable_exploration collapsed every
downstream failure into the single opaque "exploration_provisioning_failed"
code; this file pins one test per named code so a future contributor who
re-introduces a catch-all breaks a test in the right direction.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.core import SessionType
from autoskillit.hooks._exploration_request_record import write_exploration_request_record
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore
from autoskillit.server.tools.tools_exploration import enable_exploration

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _skill_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )


def _bound_store(
    tool_ctx, exploration_snapshot_service: MagicMock
) -> OwnerBoundExplorationContextStore[object]:
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tool_ctx.project_dir,
        service=exploration_snapshot_service,
    )
    tool_ctx.exploration_context_store = store
    (tool_ctx.project_dir / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    return store


async def _bind_raising(
    monkeypatch: pytest.MonkeyPatch,
    tool_ctx,
    exploration_snapshot_service: MagicMock,
    *,
    exc: BaseException,
    expected_code: str,
) -> None:
    _skill_session(monkeypatch)
    store = _bound_store(tool_ctx, exploration_snapshot_service)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    monkeypatch.setattr(store, "bind_session_scoped", MagicMock(side_effect=exc))
    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))
    assert result == {"status": "error", "code": expected_code}


@pytest.mark.asyncio
async def test_session_type_ineligible_returns_own_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.ORCHESTRATOR,
    )
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "session_type_ineligible"}


@pytest.mark.asyncio
async def test_store_unavailable_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx
) -> None:
    _skill_session(monkeypatch)
    tool_ctx.exploration_context_store = MagicMock(spec=[])
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "exploration_store_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "invalid", "A" * 43])
async def test_no_session_id_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, token: str
) -> None:
    _skill_session(monkeypatch)
    result = json.loads(await enable_exploration(_autoskillit_exploration_request_token=token))
    assert result == {"status": "error", "code": "no_session_id"}


@pytest.mark.asyncio
async def test_trusted_root_mismatch_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.TrustedRootMismatch("mismatch"),
        expected_code="trusted_root_mismatch",
    )


@pytest.mark.asyncio
async def test_service_not_configured_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.ServiceNotConfigured("no service"),
        expected_code="service_not_configured",
    )


@pytest.mark.asyncio
async def test_snapshot_stale_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.SnapshotStale("stale"),
        expected_code="snapshot_stale",
    )


@pytest.mark.asyncio
async def test_store_closed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.StoreClosed("closed"),
        expected_code="store_closed",
    )


@pytest.mark.asyncio
async def test_capacity_exceeded_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=OwnerBoundExplorationContextStore.CapacityExceeded("full"),
        expected_code="capacity_exceeded",
    )


@pytest.mark.asyncio
async def test_bind_failed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    """An unclassified bind_session_scoped failure gets its own code, distinct

    from any of the five named store exceptions above and from the opaque
    catch-all this matrix replaces.
    """
    await _bind_raising(
        monkeypatch,
        tool_ctx,
        exploration_snapshot_service,
        exc=RuntimeError("unclassified bind failure"),
        expected_code="bind_failed",
    )


@pytest.mark.asyncio
async def test_enable_components_failed_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx, exploration_snapshot_service: MagicMock
) -> None:
    _skill_session(monkeypatch)
    store = _bound_store(tool_ctx, exploration_snapshot_service)
    token = write_exploration_request_record(
        tool_ctx.project_dir, "enable_exploration", "test-session"
    )
    cleanup = MagicMock(wraps=store.cleanup_session)
    monkeypatch.setattr(store, "cleanup_session", cleanup)
    request_ctx = MagicMock()
    request_ctx.enable_components = AsyncMock(side_effect=RuntimeError("enable failed"))

    result = json.loads(
        await enable_exploration(
            _autoskillit_exploration_request_token=token,
            ctx=request_ctx,
        )
    )

    assert result == {"status": "error", "code": "enable_components_failed"}
    assert store.session_scoped_capability("test-session") is None
    cleanup.assert_called_once_with("test-session")


@pytest.mark.asyncio
async def test_unexpected_internal_error_returns_own_code(
    monkeypatch: pytest.MonkeyPatch, tool_ctx
) -> None:
    """A failure outside the bind/enable calls still fails closed with its own code."""
    _skill_session(monkeypatch)
    assert isinstance(tool_ctx.exploration_context_store, OwnerBoundExplorationContextStore)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_request_session",
        MagicMock(side_effect=RuntimeError("unclassified")),
    )
    result = json.loads(await enable_exploration())
    assert result == {"status": "error", "code": "unexpected_internal_error"}
